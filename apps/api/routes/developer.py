"""
The developer API (M1.6, U80).

"API access" is a plan feature, so it has to be *something*. Before M1.6 the
platform could mint an API key (on a plan that pays for it) and no endpoint
accepted one - the feature was a line on the pricing page and nothing else.

This is that something, and it is deliberately small and read-only:

* ``GET /api/v1/developer/catalog/products``  the tenant's catalogue
* ``GET /api/v1/developer/orders``            the tenant's orders
* ``GET /api/v1/developer/stock/levels``      the tenant's stock levels

Rules:

* the key is the only credential; a JWT is not accepted here;
* the key's scope must include ``read``;
* the key decides the tenant: a key can never read another tenant's data
  (the tenant comes from the key row, never from a query parameter);
* read-only for now. The pricing page says exactly this, and no plan promises
  writes or inbound webhooks yet.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.auth.dependencies import get_db, require_api_key
from packages.core.models.catalog import Product as ProductModel
from packages.core.models.core import ApiKey
from packages.core.models.order import Order as OrderModel
from packages.core.orders import OrderService

router = APIRouter(prefix="/api/v1/developer", tags=["developer API"])


class DeveloperProduct(BaseModel):
    """One product, as the API shows it."""

    id: str
    name: str
    status: str
    sku_count: int = 0


class DeveloperProductList(BaseModel):
    items: list[DeveloperProduct]
    total: int
    page: int
    page_size: int


class DeveloperOrder(BaseModel):
    """One order, as the API shows it."""

    id: str
    status: str
    payment_status: str
    total_cents: int
    currency: str = "KES"
    created_at: str | None = None


class DeveloperOrderList(BaseModel):
    items: list[DeveloperOrder]
    total: int
    page: int
    page_size: int


class DeveloperStockLevel(BaseModel):
    """One stock level, as the API shows it."""

    variant_id: str
    sku: str
    product_name: str
    quantity: int


@router.get("/catalog/products", response_model=DeveloperProductList)
async def list_products(
    api_key: Annotated[ApiKey, Depends(require_api_key)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DeveloperProductList:
    """The tenant's catalogue, read with an API key."""
    _require_read_scope(api_key)
    query = select(ProductModel).where(ProductModel.tenant_id == api_key.tenant_id)
    total = int(await db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = await db.execute(
        query.order_by(ProductModel.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    products = list(rows.scalars().all())
    return DeveloperProductList(
        items=[
            DeveloperProduct(id=str(p.id), name=p.name, status=p.status) for p in products
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/orders", response_model=DeveloperOrderList)
async def list_orders(
    api_key: Annotated[ApiKey, Depends(require_api_key)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[str | None, Query()] = None,
) -> DeveloperOrderList:
    """The tenant's orders, read with an API key."""
    _require_read_scope(api_key)
    service = OrderService(db)
    orders, total = await service.list_orders(
        api_key.tenant_id, status=status, page=page, page_size=page_size
    )
    return DeveloperOrderList(
        items=[
            DeveloperOrder(
                id=str(order.id),
                status=order.status,
                payment_status=order.payment_status,
                total_cents=order.total_cents,
                created_at=order.created_at.isoformat() if order.created_at else None,
            )
            for order in orders
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/stock/levels", response_model=list[DeveloperStockLevel])
async def stock_levels(
    api_key: Annotated[ApiKey, Depends(require_api_key)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[DeveloperStockLevel]:
    """The tenant's stock levels, read with an API key."""
    _require_read_scope(api_key)
    from packages.core.models.catalog import ProductVariant

    rows = await db.execute(
        select(ProductVariant, ProductModel.name)
        .join(ProductModel, ProductModel.id == ProductVariant.product_id)
        .where(
            ProductVariant.tenant_id == api_key.tenant_id,
            ProductVariant.is_active.is_(True),
        )
        .order_by(ProductVariant.sku)
    )
    return [
        DeveloperStockLevel(
            variant_id=str(variant.id),
            sku=variant.sku,
            product_name=name,
            quantity=variant.quantity,
        )
        for variant, name in rows.all()
    ]


def _require_read_scope(api_key: ApiKey) -> None:
    """The key must carry the read scope."""
    from fastapi import HTTPException, status

    scopes = list(api_key.scopes or [])
    if "read" not in scopes and "admin" not in scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This API key does not have the 'read' scope.",
        )


__all__: list[str] = []
