"""Naming server — metadata authority for the distributed file system.

Owns: which files exist, how many chunks each has, and which storage servers
hold each chunk. Stores metadata in SQLite. Never stores chunk content.

Contract: see CONTRACT.md at repo root.
"""
import os
import sqlite3
from contextlib import contextmanager

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DB_PATH = os.environ.get("DB_PATH", "naming.db")
REPLICATION_FACTOR = int(os.environ.get("REPLICATION_FACTOR", "2"))
# How long to wait when probing a storage server's /health during placement.
HEALTH_TIMEOUT = float(os.environ.get("HEALTH_TIMEOUT", "1.5"))

app = FastAPI(title="Naming Server")


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@app.on_event("startup")
def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                name TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                num_chunks INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks (
                file TEXT NOT NULL,
                idx INTEGER NOT NULL,
                server_ids TEXT NOT NULL,  -- comma-separated storage ids
                PRIMARY KEY (file, idx),
                FOREIGN KEY (file) REFERENCES files(name) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS storage_servers (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL
            );
            """
        )


# ---- models -------------------------------------------------------------

class StorageReg(BaseModel):
    id: str
    url: str


class ChunkPlacement(BaseModel):
    index: int
    server_ids: list[str]


class RegisterFile(BaseModel):
    file: str
    size: int
    chunks: list[ChunkPlacement]


# ---- storage pool -------------------------------------------------------

@app.post("/storage/register")
def register_storage(reg: StorageReg):
    with db() as conn:
        conn.execute(
            "INSERT INTO storage_servers (id, url) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET url = excluded.url",
            (reg.id, reg.url),
        )
    return {"ok": True}


@app.get("/storage")
def list_storage():
    with db() as conn:
        rows = conn.execute("SELECT id, url FROM storage_servers").fetchall()
    return {"servers": [dict(r) for r in rows]}


def _is_alive(url: str) -> bool:
    """Probe a storage server's /health so placement skips dead replicas."""
    try:
        resp = httpx.get(f"{url.rstrip('/')}/health", timeout=HEALTH_TIMEOUT)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


@app.get("/placement/{num_chunks}")
def placement(num_chunks: int):
    """Tell the client where to put each chunk: round-robin over the live pool,
    REPLICATION_FACTOR distinct servers per chunk. Each chunk entry includes both
    the chosen server ids and their URLs so the client can PUT without a second call.

    Only servers that respond to /health are used, so placement never targets a
    storage server that is currently down."""
    with db() as conn:
        rows = conn.execute("SELECT id, url FROM storage_servers ORDER BY id").fetchall()
    live = [r for r in rows if _is_alive(r["url"])]
    servers = [r["id"] for r in live]
    urls = {r["id"]: r["url"] for r in live}
    if len(servers) < REPLICATION_FACTOR:
        raise HTTPException(
            503,
            f"need >= {REPLICATION_FACTOR} live storage servers, have {len(servers)}",
        )
    plan = []
    for i in range(num_chunks):
        ids = [servers[(i + r) % len(servers)] for r in range(REPLICATION_FACTOR)]
        plan.append({
            "index": i,
            "server_ids": ids,
            "server_urls": [urls[s] for s in ids],
        })
    return {"chunks": plan, "replication_factor": REPLICATION_FACTOR}


# ---- file metadata ------------------------------------------------------

@app.post("/register")
def register_file(f: RegisterFile):
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO files (name, size, num_chunks) VALUES (?, ?, ?)",
            (f.file, f.size, len(f.chunks)),
        )
        conn.execute("DELETE FROM chunks WHERE file = ?", (f.file,))
        conn.executemany(
            "INSERT INTO chunks (file, idx, server_ids) VALUES (?, ?, ?)",
            [(f.file, c.index, ",".join(c.server_ids)) for c in f.chunks],
        )
    return {"ok": True}


def _server_urls(conn):
    return {r["id"]: r["url"] for r in conn.execute("SELECT id, url FROM storage_servers")}


@app.get("/locate/{file}")
def locate(file: str):
    with db() as conn:
        meta = conn.execute("SELECT size FROM files WHERE name = ?", (file,)).fetchone()
        if not meta:
            raise HTTPException(404, "file not found")
        urls = _server_urls(conn)
        rows = conn.execute(
            "SELECT idx, server_ids FROM chunks WHERE file = ? ORDER BY idx", (file,)
        ).fetchall()
    chunks = [
        {
            "index": r["idx"],
            "server_ids": r["server_ids"].split(","),
            "server_urls": [urls.get(s) for s in r["server_ids"].split(",")],
        }
        for r in rows
    ]
    return {"file": file, "size": meta["size"], "chunks": chunks}


@app.get("/size/{file}")
def size(file: str):
    with db() as conn:
        row = conn.execute("SELECT size FROM files WHERE name = ?", (file,)).fetchone()
    if not row:
        raise HTTPException(404, "file not found")
    return {"file": file, "size": row["size"]}


@app.delete("/file/{file}")
def delete_file(file: str):
    """Drop metadata and return chunk ids + locations so the client can purge replicas."""
    with db() as conn:
        meta = conn.execute("SELECT name FROM files WHERE name = ?", (file,)).fetchone()
        if not meta:
            raise HTTPException(404, "file not found")
        urls = _server_urls(conn)
        rows = conn.execute(
            "SELECT idx, server_ids FROM chunks WHERE file = ?", (file,)
        ).fetchall()
        conn.execute("DELETE FROM chunks WHERE file = ?", (file,))
        conn.execute("DELETE FROM files WHERE name = ?", (file,))
    chunks = [
        {
            "id": f"{file}_{r['idx']}",
            "server_ids": r["server_ids"].split(","),
            "server_urls": [urls.get(s) for s in r["server_ids"].split(",")],
        }
        for r in rows
    ]
    return {"file": file, "chunks": chunks}


@app.get("/health")
def health():
    return {"status": "ok"}
