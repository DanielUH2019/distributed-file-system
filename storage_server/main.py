"""Storage server — raw chunk storage for the distributed file system.

Contract: see CONTRACT.md at repo root.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from storage_server.config import Settings, get_settings
from storage_server.logging_config import configure_logging
from storage_server.storage import (
    InvalidChunkIdError,
    StorageIOError,
    delete_chunk,
    load_chunk,
    save_chunk,
)

logger = configure_logging()
settings: Settings | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize configuration on startup and log graceful shutdown."""
    global settings
    settings = get_settings()
    logger.info(
        "Storage server started",
        extra={
            "event": "startup",
            "id": settings.storage_id,
            "status": "success",
        },
    )
    yield
    logger.info(
        "Storage server shutting down",
        extra={"event": "shutdown", "status": "success"},
    )


app = FastAPI(title="Storage Server", lifespan=lifespan)


def _get_settings() -> Settings:
    if settings is None:
        raise RuntimeError("Settings not initialized")
    return settings


def _log(event: str, chunk_id: str, status: str, level: int = logging.INFO) -> None:
    logger.log(level, event, extra={"event": event, "id": chunk_id, "status": status})


def _storage_unavailable(exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"error": str(exc)},
    )


@app.put("/chunk/{chunk_id:path}")
async def put_chunk(chunk_id: str, request: Request) -> Response:
    """Store raw chunk bytes."""
    body = await request.body()
    data_dir = _get_settings().data_dir
    try:
        await save_chunk(data_dir, chunk_id, body)
    except InvalidChunkIdError as exc:
        _log("chunk_put", chunk_id, "invalid_id", logging.WARNING)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except StorageIOError as exc:
        _log("chunk_put", chunk_id, "error", logging.ERROR)
        return _storage_unavailable(exc)
    _log("chunk_put", chunk_id, "success")
    return Response(status_code=200)


@app.get("/chunk/{chunk_id:path}")
async def get_chunk(chunk_id: str) -> Response:
    """Return raw chunk bytes."""
    data_dir = _get_settings().data_dir
    try:
        data = await load_chunk(data_dir, chunk_id)
    except InvalidChunkIdError as exc:
        _log("chunk_get", chunk_id, "invalid_id", logging.WARNING)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except StorageIOError as exc:
        _log("chunk_get", chunk_id, "error", logging.ERROR)
        return _storage_unavailable(exc)

    if data is None:
        _log("chunk_get", chunk_id, "not_found", logging.INFO)
        return JSONResponse(status_code=404, content={"error": "Chunk not found"})

    _log("chunk_get", chunk_id, "success")
    return Response(content=data, media_type="application/octet-stream")


@app.delete("/chunk/{chunk_id:path}")
async def remove_chunk(chunk_id: str) -> Response:
    """Remove a chunk file (idempotent)."""
    data_dir = _get_settings().data_dir
    try:
        await delete_chunk(data_dir, chunk_id)
    except InvalidChunkIdError as exc:
        _log("chunk_delete", chunk_id, "invalid_id", logging.WARNING)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except StorageIOError as exc:
        _log("chunk_delete", chunk_id, "error", logging.ERROR)
        return _storage_unavailable(exc)
    _log("chunk_delete", chunk_id, "success")
    return Response(status_code=200)


@app.get("/health")
async def health() -> dict[str, str]:
    """Return the storage server identity."""
    return {"id": _get_settings().storage_id}
