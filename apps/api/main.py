"""
SocialERP API - FastAPI Application Entry Point.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from socialerp.database import close_db, init_db
from socialerp.observability import (
    configure_logging,
    get_metrics,
    init_instrumentation,
    init_metrics,
    init_tracing,
    observability_middleware,
)
from socialerp.settings import get_settings as get_socialerp_settings

settings = get_socialerp_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Application lifespan manager."""
    # Startup
    configure_logging()
    init_tracing()
    init_metrics()
    await init_db()
    init_instrumentation(app)

    try:
        yield
    finally:
        # Shutdown
        await close_db()


def create_app() -> FastAPI:
    """Build the SocialERP FastAPI application."""
    app = FastAPI(
        title="SocialERP API",
        version="0.1.0",
        description="Agentic ERP for Kenyan Social Commerce Sellers",
        lifespan=lifespan,
        docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
        redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS if hasattr(settings, "CORS_ORIGINS") else ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Observability middleware
    app.middleware("http")(observability_middleware)

    # =========================================================================
    # HEALTH & METRICS
    # =========================================================================

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "healthy", "service": "socialerp-api", "version": "0.1.0"}

    @app.get("/metrics")
    async def metrics() -> Response:
        """Prometheus metrics endpoint."""
        return Response(content=get_metrics(), media_type="text/plain")

    # =========================================================================
    # ROOT
    # =========================================================================

    @app.get("/")
    async def root() -> dict[str, str]:
        """Root endpoint."""
        return {
            "name": "SocialERP",
            "version": "0.1.0",
            "description": "Agentic ERP for Kenyan Social Commerce Sellers",
            "docs": "/docs",
        }

    # =========================================================================
    # ERROR HANDLERS
    # =========================================================================

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Global exception handler."""
        from socialerp.observability import get_logger

        logger = get_logger(__name__)
        logger.exception("Unhandled exception", path=request.url.path, error=str(exc))
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    # =========================================================================
    # ROUTE INCLUDES
    # =========================================================================

    from apps.api.routes import auth

    app.include_router(auth.router, prefix="/api/v1", tags=["authentication"])

    # Phase 2: WhatsApp webhooks
    from packages.integrations.meta.webhook import router as whatsapp_webhook_router

    app.include_router(whatsapp_webhook_router, tags=["WhatsApp Webhooks"])

    # Phase 4: Aggregator webhooks
    from packages.integrations.aggregator.webhook import router as aggregator_webhook_router

    app.include_router(aggregator_webhook_router, tags=["Aggregator Webhooks"])

    # Phase 3: Catalog, Inventory, Orders
    # NOTE: these routers carry their own "/api/v1/<section>" prefix, so we do
    # NOT pass a prefix here (doing both produced doubled paths).
    from apps.api.routes import catalog, orders, stock

    app.include_router(catalog.router, tags=["catalog"])
    app.include_router(stock.router, tags=["stock"])
    app.include_router(orders.router, tags=["orders"])

    # Phase 4: Payments (router carries its own "/api/v1/payments" prefix)
    from apps.api.routes import payments

    app.include_router(payments.router, tags=["payments"])

    # Phase 7b: Delivery (router carries its own "/api/v1/delivery" prefix)
    from apps.api.routes import delivery

    app.include_router(delivery.router, tags=["delivery"])

    # Phase 7 remainder: public tracking (own "/api/v1/tracking" + "/tracking")
    from apps.api.routes import tracking

    app.include_router(tracking.router, tags=["tracking"])
    app.include_router(tracking.page_router, tags=["tracking"])

    # Phase 7 remainder: owner alerts (own "/api/v1/alerts" prefix)
    from apps.api.routes import alerts

    app.include_router(alerts.router, tags=["alerts"])

    # Phase 8a: CRM memory (router carries its own "/api/v1/crm" prefix)
    from apps.api.routes import crm

    app.include_router(crm.router, tags=["crm"])

    # Phase 8b: segments + insights (each router carries its own "/api/v1/..." prefix)
    from apps.api.routes import insights, segments

    app.include_router(segments.router, tags=["segments"])
    app.include_router(insights.router, tags=["insights"])

    # Phase 5: Agent
    # from apps.api.routes import agent
    # app.include_router(agent.router, prefix="/api/v1/agent", tags=["agent"])

    # Phase 7: Delivery
    # from apps.api.routes import delivery
    # app.include_router(delivery.router, prefix="/api/v1/delivery", tags=["delivery"])

    # Phase 8: CRM
    # from apps.api.routes import crm
    # app.include_router(crm.router, prefix="/api/v1/crm", tags=["crm"])

    # Phase 9: Onboarding
    from apps.api.routes import onboarding

    app.include_router(onboarding.router, tags=["onboarding"])

    # Phase 11: Billing
    # from apps.api.routes import billing
    # app.include_router(billing.router, prefix="/api/v1/billing", tags=["billing"])

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
