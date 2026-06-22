"""Core chunk storage: sanitization, sharding, and async disk I/O."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path

import aiofiles
import aiofiles.os

CHUNK_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+$")


class InvalidChunkIdError(ValueError):
    """Raised when a chunk ID fails sanitization."""


class StorageIOError(OSError):
    """Raised when disk operations fail."""


def sanitize_chunk_id(chunk_id: str) -> str:
    """Validate chunk ID against the allowed character set.

    Only ``[a-zA-Z0-9_.-]`` is permitted to prevent path traversal.
    Consecutive-dot segments (``..``) are always rejected.
    """
    if ".." in chunk_id:
        raise InvalidChunkIdError(f"Invalid chunk ID: {chunk_id!r}")
    if not CHUNK_ID_PATTERN.fullmatch(chunk_id):
        raise InvalidChunkIdError(f"Invalid chunk ID: {chunk_id!r}")
    return chunk_id


def _shard_prefix(sanitized_id: str) -> tuple[str, str]:
    """Return the two-level shard directory names for a sanitized chunk ID."""
    if len(sanitized_id) >= 2:
        return sanitized_id[0], sanitized_id[1]
    digest = hashlib.sha256(sanitized_id.encode("utf-8")).hexdigest()
    return digest[0], digest[1]


def chunk_path(data_dir: Path, sanitized_id: str) -> Path:
    """Resolve the on-disk path for a sanitized chunk ID using 2-level sharding."""
    level1, level2 = _shard_prefix(sanitized_id)
    return data_dir / level1 / level2 / sanitized_id


async def save_chunk(data_dir: Path, chunk_id: str, data: bytes) -> Path:
    """Atomically persist chunk bytes to disk."""
    sanitized_id = sanitize_chunk_id(chunk_id)
    destination = chunk_path(data_dir, sanitized_id)
    destination.parent.mkdir(parents=True, exist_ok=True)

    temp_path = destination.parent / f"{sanitized_id}.{uuid.uuid4().hex}.tmp"
    try:
        async with aiofiles.open(temp_path, "wb") as handle:
            await handle.write(data)
        os.replace(temp_path, destination)
    except (OSError, PermissionError) as exc:
        if temp_path.exists():
            try:
                await aiofiles.os.remove(temp_path)
            except OSError:
                pass
        raise StorageIOError(str(exc)) from exc
    finally:
        if temp_path.exists():
            try:
                await aiofiles.os.remove(temp_path)
            except OSError:
                pass

    return destination


async def load_chunk(data_dir: Path, chunk_id: str) -> bytes | None:
    """Load chunk bytes from disk. Returns None when the chunk is missing."""
    sanitized_id = sanitize_chunk_id(chunk_id)
    path = chunk_path(data_dir, sanitized_id)
    if not path.is_file():
        return None
    try:
        async with aiofiles.open(path, "rb") as handle:
            return await handle.read()
    except (OSError, PermissionError) as exc:
        raise StorageIOError(str(exc)) from exc


async def delete_chunk(data_dir: Path, chunk_id: str) -> bool:
    """Delete a chunk file. Returns True if a file was removed, False if missing."""
    sanitized_id = sanitize_chunk_id(chunk_id)
    path = chunk_path(data_dir, sanitized_id)
    if not path.is_file():
        return False
    try:
        await aiofiles.os.remove(path)
    except (OSError, PermissionError) as exc:
        raise StorageIOError(str(exc)) from exc
    return True
