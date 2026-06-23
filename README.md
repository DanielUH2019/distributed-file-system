# Distributed File System

A small GFS-inspired distributed file system for **text files**: files are split
into fixed **1 KB chunks**, distributed and **replicated** (factor 2) across
multiple storage servers, with a single **naming server** as the metadata authority.

> University group project — Cloud Computing / Distributed Systems.

## Architecture

```
        +-------------+
        |   Client    |   split / reassemble, hides distribution
        +------+------+
               | HTTP + JSON
   +-----------+-----------+
   v                       v
+--------------+   +---------------------------+
| Naming server|   | Storage servers (N, RF=2) |
|  (metadata)  |   |  raw chunk bytes on disk  |
|  SQLite      |   +---------------------------+
+--------------+
```

- **Naming server** — indexes files, knows where every chunk lives. SQLite for
  metadata; **never** stores chunk content. Single point of failure (see arch doc).
- **Storage servers** — each holds only a fraction of the chunks, on disk.
- **Client** — splits/reassembles files, replicates chunks, hides distribution.

The frozen inter-service API is in [CONTRACT.md](CONTRACT.md).
Task split across the 5-person team is in [TASKS.md](TASKS.md).

## Status

| Component        | Owner | State          |
|------------------|-------|----------------|
| Naming server    | M1    | done           |
| Storage server   | M2    | done           |
| Client           | M3    | todo           |
| Docker / compose | M4    | todo           |
| Arch doc / FT    | M5    | done           |

## Develop (uv)

```bash
uv sync                                    # install deps
uv run pytest                              # run tests
uv run uvicorn naming_server.app:app --reload --port 8000   # run naming server
```

Then open <http://localhost:8000/docs> for interactive API docs.

## How to use (current endpoints)

The user-facing `client.py` CLI belongs to M3 and is not in this repo yet.  
For now, you can exercise the live contract endpoints directly:

```bash
# 1) Register a storage node in naming metadata
curl -X POST "http://localhost:8000/storage/register" ^
  -H "Content-Type: application/json" ^
  -d "{\"id\":\"storage-1\",\"url\":\"http://localhost:9000\"}"

# 2) Ask naming server for chunk placement (RF=2 by default)
curl "http://localhost:8000/placement/3"
```

## Documentation

- [CONTRACT.md](CONTRACT.md) — frozen inter-service API
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — architecture and design choices
- [docs/FAULT_TOLERANCE.md](docs/FAULT_TOLERANCE.md) — fault-tolerance analysis

Limitation: naming server is a single metadata authority in this build.

Once Docker is wired up (M4):

```bash
docker compose up --build
```
