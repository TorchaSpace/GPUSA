"""FastAPI app.

Run locally:  uvicorn gpusa_server.main:app --reload   (from the server/ dir)
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Response, status

from . import __version__, db
from .config import Settings, load_settings

log = logging.getLogger("gpusa_server")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else load_settings()
    log.info("Starting GPUSA server %s with %r", __version__, settings)

    app = FastAPI(title="GPUSA API", version=__version__)
    app.state.settings = settings

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
