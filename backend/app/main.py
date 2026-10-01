"""FastAPI application factory.

Dev run (Windows needs the selector loop for psycopg async):
    uvicorn app.main:app --loop asyncio:SelectorEventLoop
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from sqlalchemy import text

from app.api.deps import DbSession
from app.api.errors import install_error_handlers
from app.api.routes import auth, discovery, jobs, media, projects, taxonomy, youtube
from app.core.db import dispose_engine
from app.core.logging import configure_logging


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="YT Lead Intelligence API", version="0.1.0", lifespan=_lifespan,
                  openapi_url="/api/openapi.json", docs_url="/api/docs", redoc_url=None)
    install_error_handlers(app)

    api = APIRouter(prefix="/api")

    @api.get("/health", tags=["ops"])
    async def health(db: DbSession) -> dict[str, str]:
        await db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "ok"}

    for module in (auth, projects, taxonomy, discovery, youtube, media, jobs):
        api.include_router(module.router)
    app.include_router(api)
    return app


app = create_app()
