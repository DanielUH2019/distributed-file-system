"""Storage server — raw chunk storage for the distributed file system.

Contract: see CONTRACT.md at repo root.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
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


REGISTER_RETRIES = 10
REGISTER_BACKOFF = 1.0


async def _register_with_naming(cfg: Settings) -> None:
    """Announce this node to the naming server so it knows the live pool.

    Best-effort: naming may not be up yet, so we retry a few times and never
    crash startup if it stays unreachable. Skipped entirely when NAMING_URL
    is unset (e.g. unit tests, standalone runs).
    """
    if not cfg.naming_url:
        return
    url = f"{cfg.naming_url.rstrip('/')}/storage/register"
    payload = {"id": cfg.storage_id, "url": cfg.self_url()}
    async with httpx.AsyncClient(timeout=5.0) as client:
        for attempt in range(1, REGISTER_RETRIES + 1):
            try:
                response = await client.post(url, json=payload)
                if response.status_code == 200:
                    logger.info(
                        "Registered with naming server",
                        extra={"event": "register", "id": cfg.storage_id, "status": "success"},
                    )
                    return
                logger.warning(
                    "Naming registration returned non-200",
                    extra={"event": "register", "id": cfg.storage_id, "status": str(response.status_code)},
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                logger.warning(
                    "Naming registration attempt failed",
                    extra={"event": "register", "id": cfg.storage_id, "status": f"retry:{attempt}", "error": str(exc)},
                )
            await asyncio.sleep(REGISTER_BACKOFF)
    logger.error(
        "Gave up registering with naming server",
        extra={"event": "register", "id": cfg.storage_id, "status": "failed"},
    )


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
    # Self-register in the background so a slow/unavailable naming server never
    # blocks the storage server from serving chunk requests.
    asyncio.create_task(_register_with_naming(settings))
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
