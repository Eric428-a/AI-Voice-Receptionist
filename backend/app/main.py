"""FastAPI application entry point.

Run (from the backend directory):  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.services.container import ServiceContainer, build_container
from app.services.rag.ingestion import KnowledgeWatcher

logger = get_logger(__name__)

ContainerFactory = Callable[[Settings], ServiceContainer]


async def _initial_ingest(container: ServiceContainer) -> None:
    try:
        await container.ingestion.ingest()
    except Exception:
        logger.exception("Startup knowledge ingestion failed; the call service keeps running")


def create_app(settings: Settings | None = None, container_factory: ContainerFactory = build_container) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = container_factory(settings)
        app.state.container = container
        app.state.settings = settings
        container.sessions.start_janitor()
        background: list[asyncio.Task[None]] = []
        if settings.auto_ingest_on_startup:
            background.append(asyncio.create_task(_initial_ingest(container)))
        if settings.knowledge_watch_interval_seconds > 0:
            container.watcher = KnowledgeWatcher(
                container.ingestion, settings.knowledge_directory, settings.knowledge_watch_interval_seconds
            )
            container.watcher.start()
        logger.info(
            "Backend started",
            extra={"llm": container.chat.configured, "model": settings.openrouter_model or None,
                   "environment": settings.environment},
        )
        try:
            yield
        finally:
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            await container.aclose()
            logger.info("Backend stopped")

    app = FastAPI(
        title=f"{settings.company_name} AI Voice Caller",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if settings.environment != "production" else None,
        openapi_url="/api/openapi.json" if settings.environment != "production" else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-Admin-Key"],
    )
    app.include_router(api_router)
    return app


app = create_app()
