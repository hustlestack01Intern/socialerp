"""
Catalog API Routes

REST API endpoints for product catalog management.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.auth.dependencies import get_current_user, get_db
from packages.core.models import Category as CategoryModel
from packages.core.models import Product as ProductModel
from packages.core.models import ProductVariant as ProductVariantModel

router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])


# =============================================================================
# Pydantic Models for API
# =============================================================================


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    parent_id: UUID | None = None
    is_active: bool = True
    sort_order: int = 0
    image_url: str | None = None


class CategoryResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    description: str | None
    parent_id: UUID | None
    is_active: bool
    sort_order: int
    image_url: str | None
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class CategoryList(BaseModel):
    items: list[CategoryResponse]
    total: int
    page: int
    page_size: int


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    base_sku: str | None = None
    category_id: UUID | None = None
    brand: str = ""
    tags: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    variants: list[dict[str, Any]] = Field(default_factory=list)  # Simplified for API


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    base_sku: str | None = None
    category_id: UUID | None = None
    brand: str | None = None
    tags: list[str] | None = None
    images: list[str] | None = None
    status: str | None = None


class ProductResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    description: str
    base_sku: str | None
    category_id: UUID | None
    brand: str
    tags: list[str]
    status: str
    images: list[str]
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class ProductList(BaseModel):
    items: list[ProductResponse]
    total: int
    page: int
    page_size: int


class VariantCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=50)
    attributes: list[dict[str, Any]]  # [{"type": "color", "value": "red", "display_name": "Red"}]
    cost_price_cents: int = Field(ge=0)
    sell_price_cents: int = Field(ge=0)
    quantity: int = 0
    reorder_level: int = 5
    supplier_id: UUID | None = None
    location_id: UUID | None = None


class VariantResponse(BaseModel):
    id: UUID
    product_id: UUID
    tenant_id: UUID
    sku: str
    attributes: list[dict[str, Any]]
    cost_price_cents: int
    sell_price_cents: int
    quantity: int
    reorder_level: int
    supplier_id: UUID | None
    location_id: UUID | None
    is_active: bool
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


# =============================================================================
# Category Endpoints
# =============================================================================


@router.post("/categories", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
async def create_category(
    data: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> CategoryModel:
    """Create a new category."""
    tenant_id = current_user.tenant_id

    # Check parent exists if provided
    if data.parent_id:
        parent = await db.get(CategoryModel, data.parent_id)
        if not parent or parent.tenant_id != tenant_id:
            raise HTTPException(status_code=400, detail="Parent category not found")

    category = CategoryModel(
        tenant_id=tenant_id,
        name=data.name,
        description=data.description,
        parent_id=data.parent_id,
        is_active=data.is_active,
        sort_order=data.sort_order,
        image_url=data.image_url,
    )

    db.add(category)
    await db.commit()
    await db.refresh(category)

    return category


@router.get("/categories", response_model=CategoryList)
async def list_categories(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    is_active: bool | None = None,
    parent_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> CategoryList:
    """List categories with pagination."""
    tenant_id = current_user.tenant_id

    query = select(CategoryModel).where(CategoryModel.tenant_id == tenant_id)

    if is_active is not None:
        query = query.where(CategoryModel.is_active == is_active)
    if parent_id:
        query = query.where(CategoryModel.parent_id == parent_id)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    # Pagination
    query = query.order_by(CategoryModel.sort_order, CategoryModel.name)
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    categories = result.scalars().all()

    return CategoryList(
        items=[CategoryResponse.model_validate(c) for c in categories],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/categories/{category_id}", response_model=CategoryResponse)
async def get_category(
    category_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> CategoryModel:
    """Get a category by ID."""
    tenant_id = current_user.tenant_id

    result = await db.execute(
        select(CategoryModel).where(
            CategoryModel.id == category_id,
            CategoryModel.tenant_id == tenant_id,
        )
    )
    category = result.scalar_one_or_none()

    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    return category


@router.patch("/categories/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: UUID,
    data: dict[str, Any],  # Simplified for now
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> CategoryModel:
    """Update a category."""
    tenant_id = current_user.tenant_id

    category = await db.get(CategoryModel, category_id)
    if not category or category.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Category not found")

    # Update fields
    for key, value in data.items():
        if hasattr(category, key):
            setattr(category, key, value)

    await db.commit()
    await db.refresh(category)

    return category


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> None:
    """Delete a category (soft delete)."""
    tenant_id = current_user.tenant_id

    category = await db.get(CategoryModel, category_id)
    if not category or category.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Category not found")

    category.is_active = False
    await db.commit()

    return None


# =============================================================================
# Product Endpoints
# =============================================================================


@router.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    data: ProductCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> ProductModel:
    """Create a new product with variants."""
    tenant_id = current_user.tenant_id

    # Check category exists if provided
    if data.category_id:
        category = await db.get(CategoryModel, data.category_id)
        if not category or category.tenant_id != tenant_id:
            raise HTTPException(status_code=400, detail="Category not found")

    # Create product
    product = ProductModel(
        tenant_id=tenant_id,
        name=data.name,
        description=data.description,
        base_sku=data.base_sku,
        category_id=data.category_id,
        brand=data.brand,
        tags=data.tags,
        images=data.images,
        status="draft",
    )

    db.add(product)
    await db.flush()

    # Create variants
    for v_data in data.variants:
        variant = ProductVariantModel(
            product_id=product.id,
            tenant_id=tenant_id,
            sku=v_data["sku"],
            attributes=v_data["attributes"],
            cost_price_cents=v_data["cost_price_cents"],
            sell_price_cents=v_data["sell_price_cents"],
            quantity=v_data.get("quantity", 0),
            reorder_level=v_data.get("reorder_level", 5),
            supplier_id=v_data.get("supplier_id"),
            location_id=v_data.get("location_id"),
        )
        db.add(variant)

    await db.commit()
    await db.refresh(product)

    return product


@router.get("/products", response_model=ProductList)
async def list_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    category_id: UUID | None = None,
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> ProductList:
    """List products with pagination and filters."""
    tenant_id = current_user.tenant_id

    query = select(ProductModel).where(ProductModel.tenant_id == tenant_id)

    if status:
        query = query.where(ProductModel.status == status)
    if category_id:
        query = query.where(ProductModel.category_id == category_id)
    if search:
        query = query.where(ProductModel.name.ilike(f"%{search}%"))

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    # Pagination
    query = query.order_by(ProductModel.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    products = result.scalars().all()

    return ProductList(
        items=[ProductResponse.model_validate(p) for p in products],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/products/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> ProductModel:
    """Get a product by ID."""
    tenant_id = current_user.tenant_id

    result = await db.execute(
        select(ProductModel).where(
            ProductModel.id == product_id,
            ProductModel.tenant_id == tenant_id,
        )
    )
    product = result.scalar_one_or_none()

    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    return product


@router.patch("/products/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: UUID,
    data: ProductUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> ProductModel:
    """Update a product."""
    tenant_id = current_user.tenant_id

    product = await db.get(ProductModel, product_id)
    if not product or product.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Product not found")

    update_data = data.model_dump(exclude_unset=True)

    # Handle category_id validation
    if update_data.get("category_id"):
        category = await db.get(CategoryModel, update_data["category_id"])
        if not category or category.tenant_id != tenant_id:
            raise HTTPException(status_code=400, detail="Category not found")

    for key, value in update_data.items():
        setattr(product, key, value)

    await db.commit()
    await db.refresh(product)

    return product


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> None:
    """Delete a product (soft delete)."""
    tenant_id = current_user.tenant_id

    product = await db.get(ProductModel, product_id)
    if not product or product.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Product not found")

    product.status = "discontinued"
    await db.commit()

    return None


# =============================================================================
# Variant Endpoints
# =============================================================================


@router.post(
    "/products/{product_id}/variants",
    response_model=VariantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_variant(
    product_id: UUID,
    data: VariantCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> ProductVariantModel:
    """Create a variant for a product."""
    tenant_id = current_user.tenant_id

    # Check product exists
    product = await db.get(ProductModel, product_id)
    if not product or product.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Product not found")

    # Check SKU uniqueness
    from sqlalchemy import select

    existing = await db.execute(
        select(ProductVariantModel).where(
            ProductVariantModel.tenant_id == tenant_id,
            ProductVariantModel.sku == data.sku,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="SKU already exists")

    # Check price validation
    if data.sell_price_cents < data.cost_price_cents:
        raise HTTPException(status_code=400, detail="Sell price cannot be less than cost price")

    variant = ProductVariantModel(
        product_id=product_id,
        tenant_id=tenant_id,
        sku=data.sku,
        attributes=data.attributes,
        cost_price_cents=data.cost_price_cents,
        sell_price_cents=data.sell_price_cents,
        quantity=data.quantity,
        reorder_level=data.reorder_level,
        supplier_id=data.supplier_id,
        location_id=data.location_id,
    )

    db.add(variant)
    await db.commit()
    await db.refresh(variant)

    return variant


@router.get("/products/{product_id}/variants", response_model=list[VariantResponse])
async def list_variants(
    product_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> Sequence[ProductVariantModel]:
    """List variants for a product."""
    tenant_id = current_user.tenant_id

    # Verify product exists
    product = await db.get(ProductModel, product_id)
    if not product or product.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Product not found")

    result = await db.execute(
        select(ProductVariantModel)
        .where(
            ProductVariantModel.product_id == product_id,
            ProductVariantModel.tenant_id == tenant_id,
        )
        .order_by(ProductVariantModel.created_at)
    )
    variants = result.scalars().all()

    return variants


@router.patch("/variants/{variant_id}", response_model=VariantResponse)
async def update_variant(
    variant_id: UUID,
    data: dict[str, Any],  # Simplified
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> ProductVariantModel:
    """Update a variant."""
    tenant_id = current_user.tenant_id

    variant = await db.get(ProductVariantModel, variant_id)
    if not variant or variant.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Variant not found")

    # Validate prices if provided
    if (
        "cost_price_cents" in data
        and "sell_price_cents" in data
        and data["sell_price_cents"] < data["cost_price_cents"]
    ):
        raise HTTPException(status_code=400, detail="Sell price cannot be less than cost price")

    for key, value in data.items():
        if hasattr(variant, key):
            setattr(variant, key, value)

    await db.commit()
    await db.refresh(variant)

    return variant


@router.delete("/variants/{variant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_variant(
    variant_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> None:
    """Delete a variant (soft delete)."""
    tenant_id = current_user.tenant_id

    variant = await db.get(ProductVariantModel, variant_id)
    if not variant or variant.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Variant not found")

    variant.is_active = False
    await db.commit()

    return None
