"""Core async client logic for the distributed file system."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiofiles
import httpx

from config import CHUNK_SIZE, Settings, StorageServer, get_settings
from exceptions import (
    ClientError,
    ConfigurationError,
    DfsFileNotFoundError,
    DownloadError,
    InvalidFilenameError,
    InvalidFileTypeError,
    UploadError,
)

BACKOFF_DELAYS = (1.0, 2.0, 4.0)
TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".log",
    ".py",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".conf",
}
FILENAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+$")

logger = logging.getLogger("dfs_client")


class JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", record.getMessage()),
        }
        for key in ("file", "chunk_id", "status", "message", "error", "command"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def configure_logging(level: int = logging.INFO) -> None:
    """Configure structured JSON logging for the client."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def sanitize_filename(filename: str) -> str:
    """Reject filenames that could escape chunk ID boundaries."""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise InvalidFilenameError(f"Invalid filename: {filename!r}")
    if not FILENAME_PATTERN.fullmatch(filename):
        raise InvalidFilenameError(f"Invalid filename: {filename!r}")
    return filename


def validate_text_file(filepath: Path) -> None:
    """Ensure the local file is a supported text type."""
    if not filepath.is_file():
        raise ClientError(f"Local file not found: {filepath}")
    if filepath.suffix.lower() not in TEXT_EXTENSIONS:
        raise InvalidFileTypeError(
            f"Only text files are supported (allowed extensions: {sorted(TEXT_EXTENSIONS)})"
        )


def chunk_id(filename: str, index: int) -> str:
    """Build the storage chunk identifier."""
    safe_name = sanitize_filename(filename)
    return f"{safe_name}_{index}"


def split_file(data: bytes, chunk_size: int = CHUNK_SIZE) -> list[bytes]:
    """Split bytes into fixed-size chunks (last chunk may be smaller)."""
    if not data:
        return [b""]
    return [data[index : index + chunk_size] for index in range(0, len(data), chunk_size)]


def iter_file_chunks(filepath: Path, chunk_size: int = CHUNK_SIZE) -> Iterator[tuple[int, bytes]]:
    """Stream a local file into numbered chunks without loading it entirely."""
    with filepath.open("rb") as handle:
        index = 0
        while True:
            block = handle.read(chunk_size)
            if not block:
                if index == 0:
                    yield 0, b""
                break
            yield index, block
            index += 1


def round_robin_placement(
    chunk_index: int,
    servers: list[StorageServer],
    replication_factor: int,
) -> list[StorageServer]:
    """Pick distinct storage servers for a chunk using round-robin."""
    if replication_factor > len(servers):
        raise ConfigurationError(
            f"replication factor {replication_factor} exceeds pool size {len(servers)}"
        )
    start = chunk_index % len(servers)
    selected: list[StorageServer] = []
    offset = 0
    while len(selected) < replication_factor:
        candidate = servers[(start + offset) % len(servers)]
        if candidate not in selected:
            selected.append(candidate)
        offset += 1
    return selected


def _is_retryable_response(response: httpx.Response) -> bool:
    return response.status_code >= 500


def _is_retryable_exception(exc: Exception) -> bool:
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError))


async def _request_with_backoff(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    content: bytes | None = None,
    json: dict[str, Any] | None = None,
) -> httpx.Response:
    """Retry transient network and 5xx failures with exponential backoff."""
    last_error: Exception | None = None
    attempts = len(BACKOFF_DELAYS) + 1
    for attempt in range(attempts):
        if attempt > 0:
            await asyncio.sleep(BACKOFF_DELAYS[attempt - 1])
        try:
            response = await client.request(method, url, content=content, json=json)
            if _is_retryable_response(response):
                last_error = UploadError(f"{method} {url} failed with {response.status_code}")
                continue
            return response
        except Exception as exc:
            if not _is_retryable_exception(exc):
                raise
            last_error = exc
    if last_error is None:
        raise UploadError(f"{method} {url} failed")
    raise UploadError(str(last_error)) from last_error


async def _put_chunk(
    client: httpx.AsyncClient,
    server: StorageServer,
    chunk_name: str,
    data: bytes,
) -> None:
    url = f"{server.url}/chunk/{quote(chunk_name, safe='')}"
    response = await _request_with_backoff(client, "PUT", url, content=data)
    if response.status_code != 200:
        raise UploadError(f"PUT {url} returned {response.status_code}: {response.text}")


async def _delete_chunk(client: httpx.AsyncClient, server_url: str, chunk_name: str) -> None:
    url = f"{server_url.rstrip('/')}/chunk/{quote(chunk_name, safe='')}"
    try:
        response = await client.delete(url)
    except (httpx.TimeoutException, httpx.NetworkError):
        logger.warning(
            "chunk cleanup delete failed",
            extra={"event": "chunk_cleanup", "chunk_id": chunk_name, "status": "network_error"},
        )
        return
    if response.status_code not in (200, 404):
        logger.warning(
            "chunk cleanup delete unexpected status",
            extra={
                "event": "chunk_cleanup",
                "chunk_id": chunk_name,
                "status": str(response.status_code),
            },
        )


async def _cleanup_uploads(
    client: httpx.AsyncClient,
    uploaded: list[tuple[str, str]],
) -> None:
    if not uploaded:
        return
    await asyncio.gather(
        *(_delete_chunk(client, server_url, chunk_name) for server_url, chunk_name in uploaded),
        return_exceptions=True,
    )


async def _upload_chunk_replicas(
    client: httpx.AsyncClient,
    servers: list[StorageServer],
    chunk_name: str,
    data: bytes,
    uploaded: list[tuple[str, str]],
) -> None:
    async def put_one(server: StorageServer) -> tuple[str, str]:
        await _put_chunk(client, server, chunk_name, data)
        entry = (server.url, chunk_name)
        uploaded.append(entry)
        return entry

    try:
        await asyncio.gather(*(put_one(server) for server in servers))
    except UploadError:
        raise
    except Exception as exc:
        raise UploadError(str(exc)) from exc


def count_chunks(file_size: int, chunk_size: int = CHUNK_SIZE) -> int:
    """Number of chunks a file of ``file_size`` bytes splits into (min 1)."""
    if file_size <= 0:
        return 1
    return (file_size + chunk_size - 1) // chunk_size


async def _fetch_placement(
    client: httpx.AsyncClient,
    naming_url: str,
    num_chunks: int,
) -> list[dict[str, Any]]:
    """Ask the naming server where each chunk should be stored.

    The naming server is the authority on the live storage pool and chunk
    placement; it returns both server ids and their URLs per chunk.
    """
    url = f"{naming_url}/placement/{num_chunks}"
    response = await _request_with_backoff(client, "GET", url)
    if response.status_code != 200:
        raise UploadError(f"placement failed with {response.status_code}: {response.text}")
    chunks = response.json().get("chunks", [])
    if len(chunks) != num_chunks:
        raise UploadError(
            f"naming returned {len(chunks)} placements, expected {num_chunks}"
        )
    return sorted(chunks, key=lambda item: item["index"])


async def create_file(
    filepath: Path,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Upload a local text file to the distributed file system.

    Placement is decided by the naming server (the metadata authority on the
    live storage pool), so the client does not need to know which storage
    servers exist up front.
    """
    cfg = settings or get_settings()
    validate_text_file(filepath)
    remote_name = sanitize_filename(filepath.name)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=cfg.request_timeout)

    uploaded: list[tuple[str, str]] = []
    file_size = filepath.stat().st_size

    try:
        assert client is not None
        num_chunks = count_chunks(file_size)
        placements = await _fetch_placement(client, cfg.naming_url, num_chunks)
        chunk_placements: list[dict[str, Any]] = []

        for index, block in iter_file_chunks(filepath):
            placement = placements[index]
            selected = [
                StorageServer(id=sid, url=url)
                for sid, url in zip(placement["server_ids"], placement["server_urls"])
            ]
            name = chunk_id(remote_name, index)
            try:
                await _upload_chunk_replicas(client, selected, name, block, uploaded)
            except UploadError:
                await _cleanup_uploads(client, uploaded)
                raise
            chunk_placements.append(
                {"index": index, "server_ids": placement["server_ids"]}
            )

        register_payload = {
            "file": remote_name,
            "size": file_size,
            "chunks": chunk_placements,
        }
        register_url = f"{cfg.naming_url}/register"
        response = await _request_with_backoff(
            client,
            "POST",
            register_url,
            json=register_payload,
        )
        if response.status_code != 200:
            await _cleanup_uploads(client, uploaded)
            raise UploadError(f"register failed with {response.status_code}: {response.text}")

        logger.info(
            "file created",
            extra={"event": "create", "file": remote_name, "status": "success"},
        )
        return remote_name
    finally:
        if owns_client and client is not None:
            await client.aclose()


def _resolve_server_urls(
    chunk: dict[str, Any],
    url_map: dict[str, str],
) -> list[str]:
    urls = chunk.get("server_urls") or []
    resolved = [url for url in urls if url]
    if resolved:
        return resolved
    server_ids = chunk.get("server_ids") or []
    return [url_map[server_id] for server_id in server_ids]


async def _fetch_chunk(
    client: httpx.AsyncClient,
    chunk_name: str,
    server_urls: list[str],
) -> bytes:
    """Fetch a chunk from the first reachable replica, then the next."""
    last_error: Exception | None = None
    for replica_index, server_url in enumerate(server_urls):
        url = f"{server_url.rstrip('/')}/chunk/{quote(chunk_name, safe='')}"
        try:
            response = await client.get(url)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = exc
            if replica_index < len(server_urls) - 1:
                continue
            raise DownloadError(f"failed to read {chunk_name}: {exc}") from exc

        if response.status_code == 200:
            return response.content
        if response.status_code >= 500 and replica_index < len(server_urls) - 1:
            last_error = DownloadError(f"{url} returned {response.status_code}")
            continue
        if response.status_code == 404:
            if replica_index < len(server_urls) - 1:
                last_error = DownloadError(f"{url} returned 404")
                continue
            raise DownloadError(f"chunk not found: {chunk_name}")
        raise DownloadError(f"{url} returned {response.status_code}: {response.text}")

    raise DownloadError(f"failed to read {chunk_name}: {last_error}")


async def read_file(
    filename: str,
    output_path: Path | None = None,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> Path:
    """Download and reassemble a file from storage replicas."""
    cfg = settings or get_settings()
    remote_name = sanitize_filename(filename)
    destination = output_path or Path(f"./{remote_name}")
    temp_path = destination.with_name(f"{destination.name}.tmp")
    url_map = cfg.storage_url_map()

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=cfg.request_timeout)

    try:
        assert client is not None
        locate_url = f"{cfg.naming_url}/locate/{quote(remote_name, safe='')}"
        locate_response = await client.get(locate_url)
        if locate_response.status_code == 404:
            raise DfsFileNotFoundError(f"file not found: {remote_name}")
        if locate_response.status_code != 200:
            raise DownloadError(
                f"locate failed with {locate_response.status_code}: {locate_response.text}"
            )

        payload = locate_response.json()
        chunks = sorted(payload["chunks"], key=lambda item: item["index"])

        temp_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(temp_path, "wb") as handle:
            for chunk in chunks:
                name = chunk_id(remote_name, chunk["index"])
                server_urls = _resolve_server_urls(chunk, url_map)
                data = await _fetch_chunk(client, name, server_urls)
                await handle.write(data)

        os.replace(temp_path, destination)
        logger.info(
            "file read",
            extra={"event": "read", "file": remote_name, "status": "success"},
        )
        return destination
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise
    finally:
        if owns_client and client is not None:
            await client.aclose()


async def delete_file(
    filename: str,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Delete file metadata and purge all chunk replicas."""
    cfg = settings or get_settings()
    remote_name = sanitize_filename(filename)
    url_map = cfg.storage_url_map()

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=cfg.request_timeout)

    try:
        assert client is not None
        delete_url = f"{cfg.naming_url}/file/{quote(remote_name, safe='')}"
        response = await client.delete(delete_url)
        if response.status_code == 404:
            raise DfsFileNotFoundError(f"file not found: {remote_name}")
        if response.status_code != 200:
            raise ClientError(f"delete metadata failed: {response.status_code}: {response.text}")

        payload = response.json()
        delete_tasks: list[asyncio.Task[None]] = []
        for chunk in payload.get("chunks", []):
            chunk_name = chunk["id"]
            server_urls = _resolve_server_urls(chunk, url_map)
            for server_url in server_urls:
                delete_tasks.append(asyncio.create_task(_delete_chunk(client, server_url, chunk_name)))
        if delete_tasks:
            await asyncio.gather(*delete_tasks)

        logger.info(
            "file deleted",
            extra={"event": "delete", "file": remote_name, "status": "success"},
        )
    finally:
        if owns_client and client is not None:
            await client.aclose()


async def get_file_size(
    filename: str,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> int:
    """Return the file size from the naming server."""
    cfg = settings or get_settings()
    remote_name = sanitize_filename(filename)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=cfg.request_timeout)

    try:
        assert client is not None
        size_url = f"{cfg.naming_url}/size/{quote(remote_name, safe='')}"
        response = await client.get(size_url)
        if response.status_code == 404:
            raise DfsFileNotFoundError(f"file not found: {remote_name}")
        if response.status_code != 200:
            raise ClientError(f"size lookup failed: {response.status_code}: {response.text}")
        size = int(response.json()["size"])
        logger.info(
            "file size",
            extra={"event": "size", "file": remote_name, "status": "success"},
        )
        return size
    finally:
        if owns_client and client is not None:
            await client.aclose()
