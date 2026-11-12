"""Aggregates every route under the /api prefix."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import calls, health, knowledge, websocket

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(calls.router)
api_router.include_router(knowledge.router)
api_router.include_router(websocket.router)
