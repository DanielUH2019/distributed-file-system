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

## Develop (uv)

```bash
uv sync                                    # install deps
uv run pytest                              # run tests
uv run uvicorn naming_server.app:app --reload --port 8000   # run naming server
```

Then open <http://localhost:8000/docs> for interactive API docs.

## Documentation

- [CONTRACT.md](CONTRACT.md) — frozen inter-service API
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — component design, write/read paths, design decisions
- [docs/FAULT_TOLERANCE.md](docs/FAULT_TOLERANCE.md) — fault-tolerance analysis (the 4 graded questions)
