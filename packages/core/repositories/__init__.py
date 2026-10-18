"""
Repository Layer

Data access layer with tenant isolation enforced via RLS.
All queries automatically filtered by the current tenant context.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.domain import SKU, Money, Quantity, TenantId
from packages.core.domain.auth import ApiKeyCreate, ApiKeyList, ApiKeyResponse
from packages.core.domain.catalog import (
    Category,
    CreateProductRequest,
    CreateVariantRequest,
    Product,
    ProductStatus,
    ProductVariant,
    UpdateProductRequest,
    VariantAttribute,
    VariantAttributeType,
)
from packages.core.domain.tenant import (
    Tenant,
    TenantCreate,
    TenantList,
    TenantSettings,
    TenantUpdate,
)
from packages.core.domain.user import User, UserCreate, UserList, UserRole, UserUpdate
from packages.core.models import ApiKey as ApiKeyModel
from packages.core.models import AuditLog as AuditLogModel
from packages.core.models import Category as CategoryModel
from packages.core.models import Customer as CustomerModel
from packages.core.models import Order as OrderModel
from packages.core.models import OrderItem as OrderItemModel
from packages.core.models import Product as ProductModel
from packages.core.models import ProductVariant as ProductVariantModel
from packages.core.models import Tenant as TenantModel
from packages.core.models import User as UserModel
from packages.core.models.core import UserRole as UserRoleModel


def _affected_rows(result: Any) -> int:
    """Return the affected row count for a DML statement.

    ``AsyncSession.execute`` is typed as returning ``Result`` (which has no
    ``rowcount``); for ``update``/``delete`` statements the concrete object is a
    ``CursorResult``. Read the attribute defensively so both shapes type-check.
    """
    return int(getattr(result, "rowcount", 0))


def _variant_attributes_to_json(
    attributes: tuple[VariantAttribute, ...],
) -> list[dict[str, Any]]:
    """Serialize domain variant attributes to the JSONB column shape."""
    return [
        {"type": attr.type.value, "value": attr.value, "display_name": attr.display_name}
        for attr in attributes
    ]


def _variant_attributes_from_json(raw: list[dict[str, Any]]) -> tuple[VariantAttribute, ...]:
    """Deserialize JSONB variant attributes back to domain value objects."""
    return tuple(
        VariantAttribute(
            type=VariantAttributeType(item["type"]),
            value=item["value"],
            display_name=item.get("display_name"),
        )
        for item in raw
    )


class TenantRepository:
    """Repository for tenant operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: TenantCreate) -> Tenant:
        """Create a new tenant."""
        tenant = TenantModel(
            name=data.name,
            owner_phone=data.owner_phone,
            owner_email=data.owner_email,
            settings=data.settings.model_dump(),
        )
        self.session.add(tenant)
        await self.session.flush()
        await self.session.refresh(tenant)
        return self._to_domain(tenant)

    async def get(self, tenant_id: UUID) -> Tenant | None:
        """Get tenant by ID."""
        result = await self.session.execute(select(TenantModel).where(TenantModel.id == tenant_id))
        tenant = result.scalar_one_or_none()
        return self._to_domain(tenant) if tenant else None

    async def get_by_email(self, email: str) -> Tenant | None:
        """Get tenant by owner email."""
        result = await self.session.execute(
            select(TenantModel).where(TenantModel.owner_email == email),
        )
        tenant = result.scalar_one_or_none()
        return self._to_domain(tenant) if tenant else None

    async def update(self, tenant_id: UUID, data: TenantUpdate) -> Tenant | None:
        """Update tenant."""
        tenant = await self.session.get(TenantModel, tenant_id)
        if not tenant:
            return None

        update_data = data.model_dump(exclude_unset=True)
        if "settings" in update_data:
            update_data["settings"] = update_data["settings"].model_dump()

        for key, value in update_data.items():
            setattr(tenant, key, value)

        tenant.updated_at = datetime.now()
        await self.session.flush()
        await self.session.refresh(tenant)
        return self._to_domain(tenant)

    async def deactivate(self, tenant_id: UUID) -> bool:
        """Deactivate tenant (soft delete)."""
        result = await self.session.execute(
            update(TenantModel)
            .where(TenantModel.id == tenant_id)
            .values(is_active=False, updated_at=datetime.now()),
        )
        rowcount = result.rowcount if hasattr(result, "rowcount") else 0
        return bool(rowcount > 0)

    async def list(self, page: int = 1, page_size: int = 20) -> TenantList:
        """List tenants (superuser only - RLS will filter for app user)."""
        # Count total
        count_result = await self.session.execute(select(func.count(TenantModel.id)))
        total = count_result.scalar() or 0

        # Get page
        offset = (page - 1) * page_size
        result = await self.session.execute(
            select(TenantModel)
            .order_by(TenantModel.created_at.desc())
            .offset(offset)
            .limit(page_size),
        )
        tenants = result.scalars().all()

        return TenantList(
            items=[self._to_domain(t) for t in tenants],
            total=int(total),
            page=page,
            page_size=page_size,
        )

    def _to_domain(self, model: TenantModel) -> Tenant:
        """Convert SQLAlchemy model to domain model."""
        return Tenant(
            id=model.id,
            name=model.name,
            owner_phone=model.owner_phone,
            owner_email=model.owner_email,
            settings=TenantSettings(**model.settings) if model.settings else TenantSettings(),
            is_active=model.is_active,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )


class UserRepository:
    """Repository for user operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: UserCreate, password_hash: str) -> User:
        """Create a new user."""
        user = UserModel(
            tenant_id=data.tenant_id,
            email=data.email,
            password_hash=password_hash,
            role=UserRoleModel(data.role.value),
        )
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return self._to_domain(user)

    async def get(self, user_id: UUID) -> User | None:
        """Get user by ID."""
        result = await self.session.execute(select(UserModel).where(UserModel.id == user_id))
        user = result.scalar_one_or_none()
        return self._to_domain(user) if user else None

    async def get_by_email(self, tenant_id: UUID, email: str) -> User | None:
        """Get user by email within tenant."""
        result = await self.session.execute(
            select(UserModel)
            .where(UserModel.tenant_id == tenant_id)
            .where(UserModel.email == email),
        )
        user = result.scalar_one_or_none()
        return self._to_domain(user) if user else None

    async def get_by_email_global(self, email: str) -> User | None:
        """Get user by email (cross-tenant - for superuser)."""
        result = await self.session.execute(select(UserModel).where(UserModel.email == email))
        user = result.scalar_one_or_none()
        return self._to_domain(user) if user else None

    async def update(
        self, user_id: UUID, data: UserUpdate, password_hash: str | None = None,
    ) -> User | None:
        """Update user."""
        user = await self.session.get(UserModel, user_id)
        if not user:
            return None

        update_data = data.model_dump(exclude_unset=True)
        if password_hash:
            update_data["password_hash"] = password_hash

        for key, value in update_data.items():
            setattr(user, key, value)

        user.updated_at = datetime.now()
        await self.session.flush()
        await self.session.refresh(user)
        return self._to_domain(user)

    async def update_last_login(self, user_id: UUID) -> None:
        """Update user's last login timestamp."""
        await self.session.execute(
            update(UserModel).where(UserModel.id == user_id).values(last_login_at=datetime.now()),
        )

    async def deactivate(self, user_id: UUID) -> bool:
        """Deactivate user (soft delete)."""
        result = await self.session.execute(
            update(UserModel)
            .where(UserModel.id == user_id)
            .values(is_active=False, updated_at=datetime.now()),
        )
        rowcount = result.rowcount if hasattr(result, "rowcount") else 0
        return bool(rowcount > 0)

    async def list(self, tenant_id: UUID, page: int = 1, page_size: int = 20) -> UserList:
        """List users in tenant."""
        # Count total
        count_result = await self.session.execute(
            select(func.count(UserModel.id)).where(UserModel.tenant_id == tenant_id),
        )
        total = count_result.scalar()

        # Get page
        offset = (page - 1) * page_size
        result = await self.session.execute(
            select(UserModel)
            .where(UserModel.tenant_id == tenant_id)
            .order_by(UserModel.created_at.desc())
            .offset(offset)
            .limit(page_size),
        )
        users = result.scalars().all()

        return UserList(
            items=[self._to_domain(u) for u in users],
            total=int(total or 0),
            page=page,
            page_size=page_size,
        )

    def _to_domain(self, model: UserModel) -> User:
        """Convert SQLAlchemy model to domain model."""
        return User(
            id=model.id,
            tenant_id=model.tenant_id,
            email=model.email,
            role=UserRole(model.role.value),
            is_active=model.is_active,
            last_login_at=model.last_login_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )


class ApiKeyRepository:
    """Repository for API key operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        tenant_id: UUID,
        data: ApiKeyCreate,
        key_hash: str,
        key_prefix: str,
        full_key: str | None = None,
    ) -> ApiKeyResponse:
        """Create a new API key."""
        expires_at = None
        if data.expires_in_days:
            expires_at = datetime.now() + timedelta(days=data.expires_in_days)

        api_key = ApiKeyModel(
            tenant_id=tenant_id,
            name=data.name,
            key_hash=key_hash,
            scopes=data.scopes,
            expires_at=expires_at,
        )
        self.session.add(api_key)
        await self.session.flush()
        await self.session.refresh(api_key)

        return ApiKeyResponse(
            id=api_key.id,
            tenant_id=api_key.tenant_id,
            name=api_key.name,
            key=full_key,
            key_prefix=key_prefix,
            scopes=api_key.scopes,
            expires_at=api_key.expires_at,
            created_at=api_key.created_at,
            revoked_at=api_key.revoked_at,
        )

    async def get(self, api_key_id: UUID) -> ApiKeyResponse | None:
        """Get API key by ID."""
        result = await self.session.execute(select(ApiKeyModel).where(ApiKeyModel.id == api_key_id))
        api_key = result.scalar_one_or_none()
        return self._to_domain(api_key) if api_key else None

    async def get_by_prefix(self, key_prefix: str) -> ApiKeyResponse | None:
        """Get API key by prefix (for validation)."""
        result = await self.session.execute(
            select(ApiKeyModel).where(ApiKeyModel.key_hash.like(f"{key_prefix}%")),
        )
        api_key = result.scalar_one_or_none()
        return self._to_domain(api_key) if api_key else None

    async def revoke(self, api_key_id: UUID) -> bool:
        """Revoke API key."""
        result = await self.session.execute(
            update(ApiKeyModel)
            .where(ApiKeyModel.id == api_key_id)
            .values(revoked_at=datetime.now()),
        )
        return _affected_rows(result) > 0

    async def update_last_used(self, api_key_id: UUID) -> None:
        """Update last used timestamp."""
        await self.session.execute(
            update(ApiKeyModel)
            .where(ApiKeyModel.id == api_key_id)
            .values(last_used_at=datetime.now()),
        )

    async def list(self, tenant_id: UUID, page: int = 1, page_size: int = 20) -> ApiKeyList:
        """List API keys in tenant."""
        # Count total
        count_result = await self.session.execute(
            select(func.count(ApiKeyModel.id)).where(ApiKeyModel.tenant_id == tenant_id),
        )
        total = count_result.scalar()

        # Get page
        offset = (page - 1) * page_size
        result = await self.session.execute(
            select(ApiKeyModel)
            .where(ApiKeyModel.tenant_id == tenant_id)
            .order_by(ApiKeyModel.created_at.desc())
            .offset(offset)
            .limit(page_size),
        )
        api_keys = result.scalars().all()

        return ApiKeyList(
            items=[self._to_domain(k) for k in api_keys],
            total=int(total or 0),
            page=page,
            page_size=page_size,
        )

    def _to_domain(self, model: ApiKeyModel) -> ApiKeyResponse:
        """Convert SQLAlchemy model to domain model."""
        return ApiKeyResponse(
            id=model.id,
            tenant_id=model.tenant_id,
            name=model.name,
            key=None,  # Never return full key after creation
            key_prefix=model.key_hash[:8] if model.key_hash else "",
            scopes=model.scopes,
            expires_at=model.expires_at,
            created_at=model.created_at,
            revoked_at=model.revoked_at,
        )


class AuditLogRepository:
    """Repository for audit log operations (read-only for app user)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, log_id: UUID) -> AuditLogModel | None:
        """Get audit log entry by ID."""
        result = await self.session.execute(select(AuditLogModel).where(AuditLogModel.id == log_id))
        return result.scalar_one_or_none()

    async def list(
        self,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
        action: str | None = None,
        resource: str | None = None,
        resource_id: UUID | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> list[AuditLogModel]:
        """List audit logs with filters."""
        query = select(AuditLogModel)

        if tenant_id:
            query = query.where(AuditLogModel.tenant_id == tenant_id)
        if user_id:
            query = query.where(AuditLogModel.user_id == user_id)
        if action:
            query = query.where(AuditLogModel.action == action)
        if resource:
            query = query.where(AuditLogModel.resource == resource)
        if resource_id:
            query = query.where(AuditLogModel.resource_id == resource_id)
        if start_date:
            query = query.where(AuditLogModel.created_at >= start_date)
        if end_date:
            query = query.where(AuditLogModel.created_at <= end_date)

        query = query.order_by(AuditLogModel.created_at.desc())

        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count(
        self,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
        action: str | None = None,
        resource: str | None = None,
        resource_id: UUID | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> int:
        """Count audit logs with filters."""
        query = select(func.count(AuditLogModel.id))

        if tenant_id:
            query = query.where(AuditLogModel.tenant_id == tenant_id)
        if user_id:
            query = query.where(AuditLogModel.user_id == user_id)
        if action:
            query = query.where(AuditLogModel.action == action)
        if resource:
            query = query.where(AuditLogModel.resource == resource)
        if resource_id:
            query = query.where(AuditLogModel.resource_id == resource_id)
        if start_date:
            query = query.where(AuditLogModel.created_at >= start_date)
        if end_date:
            query = query.where(AuditLogModel.created_at <= end_date)

        result = await self.session.execute(query)
        return result.scalar() or 0


class ProductRepository:
    """Repository for product operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, tenant_id: UUID, data: CreateProductRequest) -> Product:
        """Create a new product.

        The tenant is passed explicitly (matching ``CategoryRepository.create``);
        ``CreateProductRequest`` carries only the product payload.
        """
        product = ProductModel(
            tenant_id=tenant_id,
            name=data.name,
            description=data.description,
            base_sku=str(data.base_sku) if data.base_sku else None,
            category_id=data.category_id,
            brand=data.brand,
            tags=list(data.tags),
            images=list(data.images),
        )
        self.session.add(product)
        await self.session.flush()
        await self.session.refresh(product)
        return self._to_domain(product)

    async def get(self, product_id: UUID) -> Product | None:
        """Get product by ID."""
        result = await self.session.execute(
            select(ProductModel).where(ProductModel.id == product_id),
        )
        product = result.scalar_one_or_none()
        return self._to_domain(product) if product else None

    async def get_by_sku(self, tenant_id: UUID, sku: SKU) -> Product | None:
        """Get product by SKU within tenant."""
        result = await self.session.execute(
            select(ProductModel)
            .where(ProductModel.tenant_id == tenant_id)
            .where(ProductModel.base_sku == str(sku)),
        )
        product = result.scalar_one_or_none()
        return self._to_domain(product) if product else None

    async def update(self, product_id: UUID, data: UpdateProductRequest) -> Product | None:
        """Update product."""
        product = await self.session.get(ProductModel, product_id)
        if not product:
            return None

        # ``UpdateProductRequest`` is a frozen dataclass, so build the change-set
        # explicitly (only the fields that were provided).
        update_data: dict[str, Any] = {}
        if data.name is not None:
            update_data["name"] = data.name
        if data.description is not None:
            update_data["description"] = data.description
        if data.base_sku is not None:
            update_data["base_sku"] = str(data.base_sku)
        if data.category_id is not None:
            update_data["category_id"] = data.category_id
        if data.brand is not None:
            update_data["brand"] = data.brand
        if data.tags is not None:
            update_data["tags"] = list(data.tags)
        if data.images is not None:
            update_data["images"] = list(data.images)
        if data.status is not None:
            update_data["status"] = data.status.value

        for key, value in update_data.items():
            setattr(product, key, value)

        product.updated_at = datetime.now()
        await self.session.flush()
        await self.session.refresh(product)
        return self._to_domain(product)

    async def delete(self, product_id: UUID) -> bool:
        """Soft delete product (set status to DISCONTINUED)."""
        result = await self.session.execute(
            update(ProductModel)
            .where(ProductModel.id == product_id)
            .values(status=ProductStatus.DISCONTINUED.value, updated_at=datetime.now()),
        )
        return _affected_rows(result) > 0

    async def list(
        self,
        tenant_id: UUID,
        status: ProductStatus | None = None,
        category_id: UUID | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> list[Product]:
        """List products in tenant with filters."""
        query = select(ProductModel).where(ProductModel.tenant_id == tenant_id)

        if status:
            query = query.where(ProductModel.status == status.value)
        if category_id:
            query = query.where(ProductModel.category_id == category_id)
        if search:
            query = query.where(ProductModel.name.ilike(f"%{search}%"))

        query = query.order_by(ProductModel.created_at.desc())

        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)

        result = await self.session.execute(query)
        products = result.scalars().all()

        return [self._to_domain(p) for p in products]

    async def add_variant(
        self, tenant_id: UUID, product_id: UUID, data: CreateVariantRequest,
    ) -> ProductVariant:
        """Add variant to product."""
        variant = ProductVariantModel(
            tenant_id=tenant_id,
            product_id=product_id,
            sku=str(data.sku),
            attributes=_variant_attributes_to_json(data.attributes),
            cost_price_cents=data.cost_price_cents,
            sell_price_cents=data.sell_price_cents,
            quantity=data.quantity,
            reorder_level=data.reorder_level,
            supplier_id=data.supplier_id,
            location_id=data.location_id,
        )
        self.session.add(variant)
        await self.session.flush()
        await self.session.refresh(variant)
        return self._variant_to_domain(variant)

    def _to_domain(self, model: ProductModel) -> Product:
        """Convert SQLAlchemy model to domain model."""
        return Product(
            id=model.id,
            tenant_id=TenantId(model.tenant_id),
            name=model.name,
            description=model.description,
            base_sku=SKU(model.base_sku) if model.base_sku else None,
            category_id=model.category_id,
            brand=model.brand,
            tags=tuple(model.tags) if model.tags else (),
            variants=(),  # Loaded separately if needed
            status=ProductStatus(model.status),
            images=tuple(model.images) if model.images else (),
            metadata=model.metadata_json or {},
            created_at=model.created_at.isoformat() if model.created_at else "",
            updated_at=model.updated_at.isoformat() if model.updated_at else "",
        )

    def _variant_to_domain(self, model: ProductVariantModel) -> ProductVariant:
        """Convert SQLAlchemy model to domain model."""
        return ProductVariant(
            id=model.id,
            sku=SKU(model.sku),
            attributes=_variant_attributes_from_json(model.attributes) if model.attributes else (),
            cost_price=Money.from_cents(model.cost_price_cents),
            sell_price=Money.from_cents(model.sell_price_cents),
            quantity=Quantity(model.quantity),
            reorder_level=Quantity(model.reorder_level),
            supplier_id=model.supplier_id,
            location_id=model.location_id,
            is_active=model.is_active,
            metadata=model.metadata_json or {},
        )


class CategoryRepository:
    """Repository for category operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        tenant_id: UUID,
        name: str,
        description: str = "",
        parent_id: UUID | None = None,
        is_active: bool = True,
        sort_order: int = 0,
        image_url: str = "",
    ) -> Category:
        """Create a new category."""
        category = CategoryModel(
            tenant_id=tenant_id,
            name=name,
            description=description,
            parent_id=parent_id,
            is_active=is_active,
            sort_order=sort_order,
            image_url=image_url,
        )
        self.session.add(category)
        await self.session.flush()
        await self.session.refresh(category)
        return self._to_domain(category)

    async def get(self, category_id: UUID) -> Category | None:
        """Get category by ID."""
        result = await self.session.execute(
            select(CategoryModel).where(CategoryModel.id == category_id),
        )
        category = result.scalar_one_or_none()
        return self._to_domain(category) if category else None

    async def update(
        self,
        category_id: UUID,
        name: str | None = None,
        description: str | None = None,
        is_active: bool | None = None,
        sort_order: int | None = None,
        image_url: str | None = None,
    ) -> Category | None:
        """Update category."""
        category = await self.session.get(CategoryModel, category_id)
        if not category:
            return None

        if name is not None:
            category.name = name
        if description is not None:
            category.description = description
        if is_active is not None:
            category.is_active = is_active
        if sort_order is not None:
            category.sort_order = sort_order
        if image_url is not None:
            category.image_url = image_url

        category.updated_at = datetime.now()
        await self.session.flush()
        await self.session.refresh(category)
        return self._to_domain(category)

    async def delete(self, category_id: UUID) -> bool:
        """Soft delete category."""
        result = await self.session.execute(
            update(CategoryModel)
            .where(CategoryModel.id == category_id)
            .values(is_active=False, updated_at=datetime.now()),
        )
        return _affected_rows(result) > 0

    async def list(
        self, tenant_id: UUID, is_active: bool | None = None, page: int = 1, page_size: int = 20,
    ) -> list[Category]:
        """List categories in tenant."""
        query = select(CategoryModel).where(CategoryModel.tenant_id == tenant_id)

        if is_active is not None:
            query = query.where(CategoryModel.is_active == is_active)

        query = query.order_by(CategoryModel.sort_order, CategoryModel.name)

        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)

        result = await self.session.execute(query)
        categories = result.scalars().all()

        return [self._to_domain(c) for c in categories]

    def _to_domain(self, model: CategoryModel) -> Category:
        """Convert SQLAlchemy model to domain model."""
        return Category(
            id=model.id,
            tenant_id=TenantId(model.tenant_id),
            name=model.name,
            description=model.description,
            parent_id=model.parent_id,
            is_active=model.is_active,
            sort_order=model.sort_order,
            image_url=model.image_url,
            metadata=model.metadata_json or {},
        )


class CustomerRepository:
    """Repository for customer operations (tenant-scoped)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, tenant_id: UUID, customer_id: UUID) -> CustomerModel | None:
        """Get a customer by id within the tenant."""
        result = await self.session.execute(
            select(CustomerModel).where(
                CustomerModel.id == customer_id,
                CustomerModel.tenant_id == tenant_id,
            ),
        )
        return result.scalar_one_or_none()

    async def get_by_phone(self, tenant_id: UUID, phone: str) -> CustomerModel | None:
        """Get a customer by normalized phone within the tenant."""
        result = await self.session.execute(
            select(CustomerModel).where(
                CustomerModel.tenant_id == tenant_id,
                CustomerModel.phone == phone,
            ),
        )
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        tenant_id: UUID,
        phone: str,
        name: str | None = None,
        email: str | None = None,
    ) -> CustomerModel:
        """Find the customer for a phone or create one.

        Identity is the normalized phone number per tenant; a name/email already
        on file is not overwritten by a later, less complete message.
        """
        existing = await self.get_by_phone(tenant_id, phone)
        if existing is not None:
            if name and not existing.name:
                existing.name = name
            if email and not existing.email:
                existing.email = email
            await self.session.flush()
            return existing

        customer = CustomerModel(
            tenant_id=tenant_id,
            phone=phone,
            name=name,
            email=email,
        )
        self.session.add(customer)
        await self.session.flush()
        await self.session.refresh(customer)
        return customer


class OrderRepository:
    """Repository for order operations (tenant-scoped)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, order: OrderModel, items: list[OrderItemModel]) -> OrderModel:
        """Persist an order and its line items."""
        self.session.add(order)
        await self.session.flush()
        for item in items:
            item.order_id = order.id
            self.session.add(item)
        await self.session.flush()
        return order

    async def get(self, tenant_id: UUID, order_id: UUID) -> OrderModel | None:
        """Get an order by id within the tenant."""
        result = await self.session.execute(
            select(OrderModel).where(
                OrderModel.id == order_id,
                OrderModel.tenant_id == tenant_id,
            ),
        )
        return result.scalar_one_or_none()

    async def get_by_idempotency_key(
        self, tenant_id: UUID, idempotency_key: str,
    ) -> OrderModel | None:
        """Get an order previously created with the same idempotency key."""
        result = await self.session.execute(
            select(OrderModel).where(
                OrderModel.tenant_id == tenant_id,
                OrderModel.metadata_json["idempotency_key"].astext == idempotency_key,
            ),
        )
        return result.scalar_one_or_none()

    async def items_for(self, tenant_id: UUID, order_id: UUID) -> list[OrderItemModel]:
        """Load the line items for an order."""
        result = await self.session.execute(
            select(OrderItemModel)
            .where(
                OrderItemModel.tenant_id == tenant_id,
                OrderItemModel.order_id == order_id,
            )
            .order_by(OrderItemModel.created_at),
        )
        return list(result.scalars().all())

    async def list(
        self,
        tenant_id: UUID,
        status: str | None = None,
        customer_id: UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[OrderModel], int]:
        """List orders in a tenant with filters; returns (rows, total)."""
        query = select(OrderModel).where(OrderModel.tenant_id == tenant_id)
        if status is not None:
            query = query.where(OrderModel.status == status)
        if customer_id is not None:
            query = query.where(OrderModel.customer_id == customer_id)

        count_query = select(func.count()).select_from(query.subquery())
        total = int(await self.session.scalar(count_query) or 0)

        query = (
            query
            .order_by(OrderModel.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all()), total
