"""Check that the Haumex Labs Inc. AI Voice Caller is configured correctly.

    python scripts/check_environment.py          # offline checks
    python scripts/check_environment.py --live   # also contact the providers

--live checks that OPENROUTER_MODEL appears in OpenRouter's model list
(GET /api/v1/models) and that the API key is accepted. It makes no paid
generation requests. Speech runs in the browser, so no audio model is needed.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

OK, WARN, FAIL = "[ ok ]", "[warn]", "[FAIL]"
problems = 0


def report(status: str, message: str) -> None:
    global problems
    if status == FAIL:
        problems += 1
    print(f"{status} {message}")


def check_python() -> None:
    version = sys.version_info
    if version >= (3, 11):
        report(OK, f"Python {version.major}.{version.minor}.{version.micro}")
    else:
        report(FAIL, f"Python 3.11+ required (found {version.major}.{version.minor})")


def check_packages() -> None:
    for module in ("fastapi", "uvicorn", "pydantic", "pydantic_settings", "httpx", "numpy", "pypdf", "chromadb"):
        try:
            importlib.import_module(module)
            report(OK, f"package {module}")
        except ImportError:
            report(FAIL, f"package {module} missing - run: pip install -r backend/requirements.txt")


def check_settings() -> object | None:
    try:
        from app.core.config import get_settings

        settings = get_settings()
    except Exception as exc:
        report(FAIL, f"configuration invalid: {exc}")
        return None
    report(OK, "configuration loaded")
    env_files = [p for p in (PROJECT_ROOT / ".env", PROJECT_ROOT / "backend" / ".env") if p.exists()]
    report(OK if env_files else WARN, f".env files: {', '.join(str(p) for p in env_files) or 'none found'}")
    report(OK if settings.openrouter_api_key else FAIL, "OPENROUTER_API_KEY set (the only AI key this app uses)")
    report(OK if settings.openrouter_model else FAIL,
           f"OPENROUTER_MODEL = {settings.openrouter_model or '(empty)'}")
    report(OK, f"speech recognition and voice run in the caller's browser ({settings.speech_language}); "
               "OpenRouter only receives text")
    try:
        importlib.import_module("fastembed")
        report(OK, f"local PDF-search model via fastembed ({settings.embedding_model}, no API key)")
    except ImportError:
        report(FAIL, "fastembed not installed - run: pip install -r backend/requirements.txt")
    report(OK if settings.admin_api_key else WARN, "ADMIN_API_KEY set (needed for POST /api/knowledge/ingest)")

    knowledge = settings.knowledge_directory
    if knowledge.is_dir():
        pdfs = [p for p in knowledge.rglob("*") if p.suffix.lower() == ".pdf"]
        report(OK if pdfs else WARN, f"{len(pdfs)} PDF(s) in {knowledge}")
    else:
        report(FAIL, f"knowledge directory missing: {knowledge}")
    report(OK, f"vector store path: {settings.vector_store_path}")
    return settings


def check_live(settings: object) -> None:
    import httpx

    from app.core.config import Settings

    assert isinstance(settings, Settings)
    base = settings.openrouter_base_url.rstrip("/")
    try:
        response = httpx.get(f"{base}/models", timeout=20)
        response.raise_for_status()
        ids = {m.get("id") for m in response.json().get("data", []) if isinstance(m, dict)}
        if settings.openrouter_model in ids:
            report(OK, f"OpenRouter lists model {settings.openrouter_model}")
        else:
            report(FAIL, f"OpenRouter does not list model '{settings.openrouter_model}'")
    except Exception as exc:
        report(FAIL, f"could not reach OpenRouter: {exc}")
    if settings.openrouter_api_key:
        try:
            key_resp = httpx.get(
                f"{base}/key",
                headers={"Authorization": f"Bearer {settings.openrouter_api_key.get_secret_value()}"},
                timeout=20,
            )
            report(OK if key_resp.status_code == 200 else FAIL,
                   f"OpenRouter API key accepted (HTTP {key_resp.status_code})")
        except Exception as exc:
            report(FAIL, f"OpenRouter key check failed: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="contact providers to verify credentials")
    args = parser.parse_args()
    print("Haumex Labs Inc. AI Voice Caller - environment check\n")
    check_python()
    check_packages()
    settings = check_settings()
    if args.live and settings is not None:
        print("\nLive provider checks")
        check_live(settings)
    print(f"\n{'All checks passed.' if problems == 0 else f'{problems} problem(s) found.'}")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
