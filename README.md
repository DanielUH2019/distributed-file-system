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
| Client           | M3    | done           |
| Docker / compose | M4    | done           |
| Arch doc / FT    | M5    | done           |

## How to run (Docker)

Requires Docker with Compose v2. From the repo root:

```bash
# Build images and start the cluster (1 naming + 3 storage servers).
docker compose up --build -d

# Confirm everything is healthy.
docker compose ps
```

The naming server is exposed on <http://localhost:8000> (open
<http://localhost:8000/docs> for interactive API docs). Storage servers stay on
the internal network as `storage1`/`storage2`/`storage3`. Each storage server
**self-registers** with the naming server on startup, so the naming server is
the authority on the live pool and on chunk placement.

| Service   | Internal URL            | Host port | Key env vars |
|-----------|-------------------------|-----------|--------------|
| naming    | `http://naming:8000`    | `8000`    | `DB_PATH`, `REPLICATION_FACTOR` |
| storage1-3| `http://storageN:9000`  | —         | `STORAGE_ID`, `STORAGE_URL`, `NAMING_URL`, `DATA_DIR` |
| client    | (run-to-completion)     | —         | `NAMING_URL`, `REPLICATION_FACTOR` |

Metadata (`naming-db`) and each storage server's chunks (`storageN-data`) live on
named volumes, so data survives restarts. To wipe everything: `docker compose down -v`.

## How to use (client)

The client is a run-to-completion CLI. Put local text files in `./files/`
(bind-mounted into the client at `/files`), then:

```bash
# Upload a text file (chunked to 1 KB, replicated to 2 storage servers)
docker compose run --rm client create /files/notes.txt

# File size from metadata only (no chunk transfer)
docker compose run --rm client size notes.txt

# Download and reassemble (writes back into ./files/)
docker compose run --rm client read notes.txt /files/notes_out.txt

# Delete the file and purge all chunk replicas
docker compose run --rm client delete notes.txt
```

## End-to-end demo / integration test

`demo.sh` brings up the stack and runs create → size → read → delete, verifying a
byte-for-byte round-trip. It also **stops a storage server mid-flight** and shows
that reads still succeed from the surviving replica. It exits non-zero on any
failure, so it doubles as an integration test.

```bash
./demo.sh
```

## Develop (without Docker)

```bash
uv sync                                    # install deps (or: python -m venv .venv && pip install -r requirements.txt)
uv run pytest                              # run the full test suite
uv run uvicorn naming_server.app:app --reload --port 8000   # run naming server
```

## Documentation

- [CONTRACT.md](CONTRACT.md) — frozen inter-service API
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — component design, write/read paths, design decisions
- [docs/FAULT_TOLERANCE.md](docs/FAULT_TOLERANCE.md) — fault-tolerance analysis (the 4 graded questions)

## Design note: placement authority

Per [CONTRACT.md](CONTRACT.md), storage servers self-register with the naming
server, and the naming server owns chunk placement. On **create**, the client
asks `GET /placement/{n}` for where to store each chunk; on **read**/**delete**
it uses the locations returned by `/locate` and `/file`. The client therefore
does not need a hardcoded list of storage servers — adding a storage replica is
a matter of registering it with the naming server. (`STORAGE_SERVERS` remains an
optional client-side fallback only.)
