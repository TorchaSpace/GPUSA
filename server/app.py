"""FastAPI entry point: `uvicorn server.app:app`.

Only the health endpoints exist so far; /v1 (auth, sync push/pull) is next.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from server.config import Settings, load_settings
from server.db import make_pool

API_VERSION = "0.1.0"


def create_app(settings: Settings | None = None, pool=None) -> FastAPI:
    """Build the app. Tests pass their own pool; production reads the env."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.pool = pool
        if app.state.pool is None:
            app.state.pool = make_pool(settings or load_settings())
            app.state.pool.open()
        try:
            yield
        finally:
            if pool is None:
                app.state.pool.close()

    app = FastAPI(title="GPUSA server", version=API_VERSION, lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        """Liveness only: never touches the database."""
        return {"status": "ok", "version": API_VERSION}

    @app.get("/health/db")
    def health_db() -> dict:
        """Readiness: one round trip to PostgreSQL (wakes a paused Aurora)."""
        try:
            with app.state.pool.connection() as conn:
                conn.execute("SELECT 1")
        except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        return {"status": "ok"}

    return app


# Settings are read when the server starts (lifespan), not at import time.
app = create_app()
