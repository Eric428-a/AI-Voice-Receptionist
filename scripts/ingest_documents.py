"""Index every PDF under the knowledge directory (recursively).

Usage (from the project root, with the backend virtualenv active):

    python scripts/ingest_documents.py                 # incremental
    python scripts/ingest_documents.py --force         # rebuild everything
    python scripts/ingest_documents.py --watch         # keep watching for changes
    python scripts/ingest_documents.py --via-api http://localhost:8000

Use --via-api while the backend is running so only one process writes to the
vector store (requires ADMIN_API_KEY in the environment or .env).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.schemas.knowledge import IngestionReport  # noqa: E402
from app.services.rag.document_loader import directory_signature, discover_pdfs  # noqa: E402
from app.services.rag.embeddings import create_embedding_provider  # noqa: E402
from app.services.rag.ingestion import IngestionService  # noqa: E402
from app.services.rag.vector_store import ChromaVectorStore  # noqa: E402


def print_report(report: IngestionReport) -> None:
    print(f"Discovered PDFs : {report.discovered}")
    print(f"Added           : {len(report.added)}")
    print(f"Updated         : {len(report.updated)}")
    print(f"Unchanged       : {len(report.unchanged)}")
    print(f"Removed         : {len(report.removed)}")
    print(f"Duplicates      : {len(report.duplicates)}")
    print(f"Failed          : {len(report.failed)}")
    for path, error in report.failed.items():
        print(f"  - {path}: {error}")
    print(f"Chunks in index : {report.total_chunks}")
    if report.full_reindex:
        print("(full re-index performed)")


async def run_local(force: bool, watch: bool, interval: float) -> int:
    settings = get_settings()
    configure_logging("WARNING")
    print(f"Knowledge directory: {settings.knowledge_directory}")
    embedder = create_embedding_provider(settings)
    store = ChromaVectorStore(settings.vector_store_path, settings.chroma_collection)
    service = IngestionService(settings, embedder, store)
    try:
        print_report(await service.ingest(force=force))
        if not watch:
            return 0
        print(f"\nWatching for changes every {interval:.0f}s (Ctrl+C to stop)...")
        signature = directory_signature(discover_pdfs(settings.knowledge_directory))
        while True:
            await asyncio.sleep(interval)
            current = directory_signature(discover_pdfs(settings.knowledge_directory))
            if current != signature:
                print("\nChange detected; re-indexing...")
                print_report(await service.ingest())
                signature = current
    finally:
        await embedder.aclose()


def run_via_api(base_url: str, force: bool) -> int:
    import httpx

    settings = get_settings()
    key = os.environ.get("ADMIN_API_KEY") or (
        settings.admin_api_key.get_secret_value() if settings.admin_api_key else None
    )
    if not key:
        print("ADMIN_API_KEY is not set.", file=sys.stderr)
        return 2
    response = httpx.post(
        f"{base_url.rstrip('/')}/api/knowledge/ingest",
        json={"force": force}, headers={"X-Admin-Key": key}, timeout=None,
    )
    if response.status_code != 200:
        print(f"Ingestion failed ({response.status_code}): {response.text}", file=sys.stderr)
        return 1
    print_report(IngestionReport.model_validate(response.json()))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Index Haumex Labs Inc. knowledge PDFs.")
    parser.add_argument("--force", action="store_true", help="re-index every document")
    parser.add_argument("--watch", action="store_true", help="keep watching the folder for changes")
    parser.add_argument("--interval", type=float, default=30.0, help="watch interval in seconds")
    parser.add_argument("--via-api", metavar="URL", help="trigger ingestion on a running backend")
    parser.add_argument("--json", action="store_true", help="print the final status as JSON")
    args = parser.parse_args()
    if args.via_api:
        return run_via_api(args.via_api, args.force)
    try:
        return asyncio.run(run_local(args.force, args.watch, args.interval))
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except Exception as exc:  # surface provider/config errors clearly
        print(f"Ingestion failed: {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
