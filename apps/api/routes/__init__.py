"""
API Routes Package

All API route modules.
"""

from fastapi import APIRouter

from apps.api.routes import catalog, orders, stock

api_router = APIRouter()

api_router.include_router(catalog.router)
api_router.include_router(stock.router)
api_router.include_router(orders.router)

__all__ = ["api_router", "catalog", "orders", "stock"]
