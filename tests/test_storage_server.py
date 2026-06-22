"""Comprehensive tests for the storage server."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from storage_server.storage import (
    InvalidChunkIdError,
    StorageIOError,
    chunk_path,
    delete_chunk,
    load_chunk,
    sanitize_chunk_id,
    save_chunk,
)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def client(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("STORAGE_ID", "test-storage-1")
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("STORAGE_PORT", "9000")

    from storage_server import main

    main.settings = None

    with TestClient(main.app) as test_client:
        yield test_client


# ---- unit: sanitization -------------------------------------------------


def test_sanitize_chunk_id_accepts_valid_ids() -> None:
    assert sanitize_chunk_id("notes.txt_0") == "notes.txt_0"
    assert sanitize_chunk_id("valid_0") == "valid_0"
    assert sanitize_chunk_id("a-b.c_1") == "a-b.c_1"


@pytest.mark.parametrize(
    "invalid_id",
    [
        "../../etc/passwd",
        "foo/bar",
        "foo\\bar",
        "bad id",
        "bad..id",
        "",
        "chunk@1",
    ],
)
def test_sanitize_chunk_id_rejects_invalid_ids(invalid_id: str) -> None:
    with pytest.raises(InvalidChunkIdError):
        sanitize_chunk_id(invalid_id)


def test_chunk_path_shards_by_first_two_characters(data_dir: Path) -> None:
    path = chunk_path(data_dir, "valid_0")
    assert path == data_dir / "v" / "a" / "valid_0"


def test_chunk_path_shards_short_id_with_hash(data_dir: Path) -> None:
    path = chunk_path(data_dir, "x")
    assert len(path.parts) >= 3
    assert path.name == "x"


# ---- unit: atomic writes ------------------------------------------------


@pytest.mark.asyncio
async def test_atomic_write_no_temp_file_leak(data_dir: Path) -> None:
    await save_chunk(data_dir, "valid_0", b"payload")

    final_path = chunk_path(data_dir, "valid_0")
    assert final_path.is_file()
    assert final_path.read_bytes() == b"payload"

    shard_dir = final_path.parent
    temp_files = list(shard_dir.glob("*.tmp"))
    assert temp_files == []


@pytest.mark.asyncio
async def test_atomic_write_cleans_up_temp_on_failure(data_dir: Path) -> None:
    destination = chunk_path(data_dir, "valid_0")
    destination.parent.mkdir(parents=True, exist_ok=True)

    with patch("storage_server.storage.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(StorageIOError):
            await save_chunk(data_dir, "valid_0", b"payload")

    assert not destination.exists()
    temp_files = list(destination.parent.glob("*.tmp"))
    assert temp_files == []


# ---- API tests ----------------------------------------------------------


def test_put_chunk_success(client: TestClient, data_dir: Path) -> None:
    payload = b"hello chunk data"
    response = client.put("/chunk/valid_0", content=payload)
    assert response.status_code == 200

    stored = chunk_path(data_dir, "valid_0")
    assert stored.is_file()
    assert stored.read_bytes() == payload


def test_put_chunk_rejects_path_traversal(client: TestClient) -> None:
    # Literal ../ in URLs is normalized by HTTP stacks before routing; encoded ids are rejected.
    response = client.put("/chunk/..%2F..%2Fetc%2Fpasswd", content=b"evil")
    assert response.status_code == 400


def test_get_chunk_success(client: TestClient) -> None:
    payload = b"raw bytes here"
    client.put("/chunk/valid_0", content=payload)

    response = client.get("/chunk/valid_0")
    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"].startswith("application/octet-stream")


def test_get_chunk_missing(client: TestClient) -> None:
    response = client.get("/chunk/missing")
    assert response.status_code == 404
    assert response.json() == {"error": "Chunk not found"}


def test_delete_chunk_first_time(client: TestClient, data_dir: Path) -> None:
    client.put("/chunk/valid_0", content=b"data")
    assert chunk_path(data_dir, "valid_0").is_file()

    response = client.delete("/chunk/valid_0")
    assert response.status_code == 200
    assert not chunk_path(data_dir, "valid_0").exists()


def test_delete_chunk_idempotent_second_time(client: TestClient) -> None:
    client.put("/chunk/valid_0", content=b"data")
    client.delete("/chunk/valid_0")

    response = client.delete("/chunk/valid_0")
    assert response.status_code == 200


def test_delete_chunk_missing_is_idempotent(client: TestClient) -> None:
    response = client.delete("/chunk/missing")
    assert response.status_code == 200


def test_put_empty_body(client: TestClient, data_dir: Path) -> None:
    response = client.put("/chunk/empty_0", content=b"")
    assert response.status_code == 200
    assert chunk_path(data_dir, "empty_0").read_bytes() == b""


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"id": "test-storage-1"}


def test_data_dir_created_on_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    base = Path(tempfile.mkdtemp())
    missing_dir = base / "nested" / "storage-data"
    assert not missing_dir.exists()

    monkeypatch.setenv("STORAGE_ID", "boot-test")
    monkeypatch.setenv("DATA_DIR", str(missing_dir))

    from storage_server import main

    main.settings = None

    with TestClient(main.app) as test_client:
        response = test_client.get("/health")
        assert response.status_code == 200

    assert missing_dir.is_dir()


def test_storage_io_error_returns_503(client: TestClient) -> None:
    with patch("storage_server.main.save_chunk", side_effect=StorageIOError("disk full")):
        response = client.put("/chunk/valid_0", content=b"data")
    assert response.status_code == 503
    assert response.json() == {"error": "disk full"}


# ---- concurrency --------------------------------------------------------


def test_concurrent_puts_same_chunk(client: TestClient, data_dir: Path) -> None:
    payloads = [f"payload-{index}".encode() for index in range(10)]

    async def run_puts() -> list[int]:
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
            tasks = [
                async_client.put("/chunk/race_0", content=payload)
                for payload in payloads
            ]
            responses = await asyncio.gather(*tasks)
            return [response.status_code for response in responses]

    statuses = asyncio.run(run_puts())
    assert all(status == 200 for status in statuses)

    final_content = chunk_path(data_dir, "race_0").read_bytes()
    assert final_content in payloads

    temp_files = list(chunk_path(data_dir, "race_0").parent.glob("*.tmp"))
    assert temp_files == []


@pytest.mark.asyncio
async def test_load_and_delete_roundtrip(data_dir: Path) -> None:
    await save_chunk(data_dir, "round_0", b"roundtrip")
    loaded = await load_chunk(data_dir, "round_0")
    assert loaded == b"roundtrip"

    removed = await delete_chunk(data_dir, "round_0")
    assert removed is True
    assert await load_chunk(data_dir, "round_0") is None

    removed_again = await delete_chunk(data_dir, "round_0")
    assert removed_again is False
