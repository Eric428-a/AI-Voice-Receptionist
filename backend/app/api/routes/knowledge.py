"""GET /api/knowledge/status and POST /api/knowledge/ingest (admin only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_container
from app.core.errors import HaumexError
from app.core.security import require_admin
from app.schemas.knowledge import IngestionReport, IngestRequest, KnowledgeStatus
from app.services.container import ServiceContainer
from app.services.rag.ingestion import IngestionInProgressError

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("/status", response_model=KnowledgeStatus)
async def knowledge_status(container: ServiceContainer = Depends(get_container)) -> KnowledgeStatus:
    return await container.ingestion.status()


@router.post("/ingest", response_model=IngestionReport, dependencies=[Depends(require_admin)])
async def ingest_knowledge(
    body: IngestRequest | None = None, container: ServiceContainer = Depends(get_container)
) -> IngestionReport:
    try:
        return await container.ingestion.ingest(force=bool(body and body.force))
    except IngestionInProgressError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except HaumexError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, exc.message) from exc
