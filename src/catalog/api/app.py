"""FastAPI application factory (read-only REST API)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine

from catalog.api.routes import audit, catalog
from catalog.config import Settings, get_settings
from catalog.db import get_engine

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="Catalog consolidation API",
        version="1.0.0",
        description="Read-only access to the consolidated marketplace catalog.",
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = engine or get_engine(settings.api_database_url)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "HEAD", "OPTIONS"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(catalog.router, prefix=API_PREFIX)
    app.include_router(audit.router, prefix=API_PREFIX)
    return app
