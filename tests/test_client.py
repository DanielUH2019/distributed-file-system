"""Tests for the DFS client."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from client_logic import (
    create_file,
    delete_file,
    read_file,
    round_robin_placement,
    split_file,
    validate_text_file,
)
from config import CHUNK_SIZE, Settings, StorageServer
from exceptions import InvalidFileTypeError, UploadError

STORAGE_URLS = "http://storage1:9000,http://storage2:9000,http://storage3:9000"
SERVERS = [
    StorageServer(id="storage1", url="http://storage1:9000"),
    StorageServer(id="storage2", url="http://storage2:9000"),
    StorageServer(id="storage3", url="http://storage3:9000"),
]


@pytest.fixture
def settings() -> Settings:
    return Settings(
        NAMING_URL="http://naming:8000",
        REPLICATION_FACTOR=2,
        STORAGE_SERVERS=STORAGE_URLS,
        REQUEST_TIMEOUT=5.0,
    )


@pytest.fixture
def text_file(tmp_path: Path) -> Path:
    path = tmp_path / "notes.txt"
    path.write_bytes(b"x" * 100)
    return path


def test_round_robin_placement() -> None:
    chunk0 = round_robin_placement(0, SERVERS, replication_factor=2)
    chunk1 = round_robin_placement(1, SERVERS, replication_factor=2)
    chunk2 = round_robin_placement(2, SERVERS, replication_factor=2)

    assert [server.id for server in chunk0] == ["storage1", "storage2"]
    assert [server.id for server in chunk1] == ["storage2", "storage3"]
    assert [server.id for server in chunk2] == ["storage3", "storage1"]
    assert len({server.id for server in chunk0}) == 2
    assert len({server.id for server in chunk1}) == 2
    assert len({server.id for server in chunk2}) == 2


def test_split_file() -> None:
    data = b"x" * 2560
    chunks = split_file(data, chunk_size=CHUNK_SIZE)

    assert len(chunks) == 3
    assert len(chunks[0]) == 1024
    assert len(chunks[1]) == 1024
    assert len(chunks[2]) == 512


def test_validate_text_file_accepts_supported_text_extension(tmp_path: Path) -> None:
    path = tmp_path / "notes.md"
    path.write_text("distributed file system notes", encoding="utf-8")

    validate_text_file(path)


def test_validate_text_file_rejects_non_text_extension(tmp_path: Path) -> None:
    path = tmp_path / "diagram.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")

    with pytest.raises(InvalidFileTypeError):
        validate_text_file(path)


@respx.mock
@pytest.mark.asyncio
async def test_create_success(text_file: Path, settings: Settings) -> None:
    # naming server is the authority on placement; the client asks it where to
    # store each chunk and uses the URLs it returns.
    placement_route = respx.get("http://naming:8000/placement/1").mock(
        return_value=httpx.Response(
            200,
            json={
                "replication_factor": 2,
                "chunks": [
                    {
                        "index": 0,
                        "server_ids": ["storage-1", "storage-2"],
                        "server_urls": ["http://storage1:9000", "http://storage2:9000"],
                    }
                ],
            },
        )
    )
    put_routes = [
        respx.put(f"http://storage{i}:9000/chunk/notes.txt_0").mock(
            return_value=httpx.Response(200)
        )
        for i in (1, 2)
    ]
    register_route = respx.post("http://naming:8000/register").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    async with httpx.AsyncClient() as client:
        remote_name = await create_file(text_file, settings=settings, client=client)

    assert remote_name == "notes.txt"
    assert placement_route.called
    assert all(route.called for route in put_routes)
    assert register_route.called
    payload = json.loads(register_route.calls.last.request.content)
    assert payload["file"] == "notes.txt"
    assert payload["size"] == 100
    assert payload["chunks"] == [{"index": 0, "server_ids": ["storage-1", "storage-2"]}]


@respx.mock
@pytest.mark.asyncio
async def test_create_partial_failure_attempts_cleanup(
    text_file: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("client_logic.BACKOFF_DELAYS", (0.0, 0.0, 0.0))
    respx.get("http://naming:8000/placement/1").mock(
        return_value=httpx.Response(
            200,
            json={
                "replication_factor": 2,
                "chunks": [
                    {
                        "index": 0,
                        "server_ids": ["storage-1", "storage-2"],
                        "server_urls": ["http://storage1:9000", "http://storage2:9000"],
                    }
                ],
            },
        )
    )
    respx.put("http://storage1:9000/chunk/notes.txt_0").mock(return_value=httpx.Response(200))
    respx.put("http://storage2:9000/chunk/notes.txt_0").mock(return_value=httpx.Response(500))
    cleanup_route = respx.delete("http://storage1:9000/chunk/notes.txt_0").mock(
        return_value=httpx.Response(200)
    )
    register_route = respx.post("http://naming:8000/register").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(UploadError):
            await create_file(text_file, settings=settings, client=client)

    assert cleanup_route.called
    assert not register_route.called


@respx.mock
@pytest.mark.asyncio
async def test_read_retries_second_replica(settings: Settings, tmp_path: Path) -> None:
    respx.get("http://naming:8000/locate/notes.txt").mock(
        return_value=httpx.Response(
            200,
            json={
                "file": "notes.txt",
                "size": 5,
                "chunks": [
                    {
                        "index": 0,
                        "server_ids": ["storage1", "storage2"],
                        "server_urls": ["http://storage1:9000", "http://storage2:9000"],
                    }
                ],
            },
        )
    )
    respx.get("http://storage1:9000/chunk/notes.txt_0").mock(return_value=httpx.Response(500))
    respx.get("http://storage2:9000/chunk/notes.txt_0").mock(
        return_value=httpx.Response(200, content=b"hello")
    )

    output_path = tmp_path / "downloaded.txt"
    async with httpx.AsyncClient() as client:
        destination = await read_file(
            "notes.txt",
            output_path=output_path,
            settings=settings,
            client=client,
        )

    assert destination == output_path
    assert output_path.read_bytes() == b"hello"


@respx.mock
@pytest.mark.asyncio
async def test_delete_purges_storage_chunks(settings: Settings) -> None:
    respx.delete("http://naming:8000/file/notes.txt").mock(
        return_value=httpx.Response(
            200,
            json={
                "file": "notes.txt",
                "chunks": [
                    {
                        "id": "notes.txt_0",
                        "server_ids": ["storage1", "storage2"],
                        "server_urls": ["http://storage1:9000", "http://storage2:9000"],
                    }
                ],
            },
        )
    )
    delete_routes = [
        respx.delete(f"http://storage{i}:9000/chunk/notes.txt_0").mock(
            return_value=httpx.Response(200)
        )
        for i in (1, 2)
    ]

    async with httpx.AsyncClient() as client:
        await delete_file("notes.txt", settings=settings, client=client)

    assert all(route.called for route in delete_routes)
