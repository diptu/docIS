"""
FastAPI application entry point.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import close_db, get_db

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Manage application startup and shutdown."""
    try:
        yield
    finally:
        await close_db()


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
)

health_router = APIRouter(
    prefix="/health",
    tags=["Health"],
)


@health_router.get(
    "",
    status_code=status.HTTP_200_OK,
)
async def health_check() -> dict[str, str]:
    """Return application health status."""
    return {"status": "healthy"}


@health_router.get(
    "/database",
    status_code=status.HTTP_200_OK,
)
async def database_health(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    """Verify PostgreSQL connectivity."""
    await db.execute(text("SELECT 1"))
    return {"database": "healthy"}


app.include_router(health_router)
