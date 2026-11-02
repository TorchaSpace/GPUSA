"""FastAPI app.

Run locally:  uvicorn gpusa_server.main:app --reload   (from the server/ dir)
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Response, status

from . import __version__, db
from .auth_routes import router as auth_router
from .config import Settings, load_settings
from .product_routes import router as product_router
from .read_routes import router as read_router
from .sale_routes import router as sale_router
from .shipment_routes import router as shipment_router
from .ledger_routes import router as ledger_router
from .people_routes import router as people_router
from .place_routes import router as place_router
from .purchase_routes import router as purchase_router
from .return_routes import router as return_router
from .stock_routes import router as stock_router

log = logging.getLogger("gpusa_server")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else load_settings()
    log.info("Starting GPUSA server %s with %r", __version__, settings)

    app = FastAPI(title="GPUSA API", version=__version__)
    app.state.settings = settings
    app.include_router(auth_router)
    app.include_router(product_router)
    app.include_router(read_router)
    app.include_router(stock_router)
    app.include_router(sale_router)
    app.include_router(shipment_router)
    app.include_router(people_router)
    app.include_router(return_router)
    app.include_router(purchase_router)
    app.include_router(ledger_router)
    app.include_router(place_router)

    @app.get("/health")
    def health() -> dict:
        """Liveness: the process is up. Never touches the database, so a DB
        blip doesn't make the load balancer kill every instance."""
        return {"status": "ok", "version": __version__}

    @app.get("/health/db")
    def health_db(response: Response) -> dict:
        """Readiness: can we reach PostgreSQL? 503 if not."""
        if settings.mode == "none":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "error", "database": "not_configured"}
        try:
            db.ping(settings)
        except Exception as exc:
            # Log the type only: driver messages can echo host/user details.
            log.warning("Database health check failed: %s", type(exc).__name__)
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "error", "database": "unreachable"}
        return {"status": "ok", "database": "ok"}

    return app


app = create_app()
