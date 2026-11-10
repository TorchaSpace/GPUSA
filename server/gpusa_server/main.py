"""FastAPI app.

Run locally:  uvicorn gpusa_server.main:app --reload   (from the server/ dir)
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse

from . import __version__, db, discovery, migrate
from .activity_routes import changes_router, router as activity_router
from .auth_routes import router as auth_router
from .identity_routes import router as identity_router
from .config import Settings, load_settings
from .product_routes import router as product_router
from .read_routes import router as read_router
from .sale_routes import router as sale_router
from .shipment_routes import router as shipment_router
from .ledger_routes import router as ledger_router
from .people_routes import router as people_router
from .place_routes import router as place_router
from .purchase_routes import router as purchase_router
from .recovery_routes import router as recovery_router
from .shield import Shield
from .return_routes import router as return_router
from .setup_routes import router as setup_router
from .stock_routes import router as stock_router

log = logging.getLogger("gpusa_server")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else load_settings()
    log.info("Starting GPUSA server %s with %r", __version__, settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if migrate.enabled(settings):
            migrate.upgrade_to_head(settings)  # new code, new columns: the schema follows on every start
        responder = discovery.start_from_environment()  # lets new PCs on the network find this server
        try:
            yield
        finally:
            if responder is not None:
                responder.stop()

    # The interactive docs hand an attacker the whole map of the API; they are for developers only
    # (GPUSA_DOCS=1 turns them on). The schema itself is still built for tests and tools.
    docs = os.environ.get("GPUSA_DOCS") == "1"
    app = FastAPI(title="GPUSA API", version=__version__, lifespan=lifespan,
                  docs_url="/docs" if docs else None, redoc_url="/redoc" if docs else None,
                  openapi_url="/openapi.json" if docs else None)
    app.state.settings = settings
    app.add_middleware(Shield)

    @app.exception_handler(psycopg.errors.DataError)
    async def _bad_value(_: Request, exc: psycopg.errors.DataError) -> JSONResponse:
        # A value PostgreSQL cannot store (a NUL character, a number or date out of range): the caller's mistake.
        log.info("Refused a value the database cannot store: %s", type(exc).__name__)
        return JSONResponse(status_code=422, content={"detail": "A value in that request cannot be stored."})
    app.include_router(auth_router)
    app.include_router(identity_router)
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
    app.include_router(recovery_router)
    app.include_router(setup_router)
    app.include_router(activity_router)
    app.include_router(changes_router)

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
