# Storage Server

Raw chunk storage node for the distributed file system. Stores chunk bytes on disk
with atomic writes, path sanitization, and two-level directory sharding.

## Environment variables

| Variable       | Required | Default   | Description                          |
|----------------|----------|-----------|--------------------------------------|
| `STORAGE_ID`   | yes      | —         | Unique ID for this node (e.g. `storage-1`) |
| `STORAGE_PORT` | no       | `9000`    | HTTP listen port                     |
| `DATA_DIR`     | no       | `./data`  | Directory for chunk files (created if missing) |

## Run locally

```bash
export STORAGE_ID=storage-1
export STORAGE_PORT=9000
export DATA_DIR=./data

uvicorn storage_server.main:app --host 0.0.0.0 --port "$STORAGE_PORT"
```

Or with `uv`:

```bash
STORAGE_ID=storage-1 uv run uvicorn storage_server.main:app --host 0.0.0.0 --port 9000
```

## API

| Method | Path            | Body        | Response                          |
|--------|-----------------|-------------|-----------------------------------|
| PUT    | `/chunk/{id}`   | raw bytes   | `200 OK`                          |
| GET    | `/chunk/{id}`   | —           | raw bytes, or `404 {"error":"Chunk not found"}` |
| DELETE | `/chunk/{id}`   | —           | `200 OK` (idempotent)             |
| GET    | `/health`       | —           | `{"id":"<STORAGE_ID>"}`           |

Invalid chunk IDs (characters outside `[a-zA-Z0-9_.-]`) return `400 Bad Request`.

## Tests

```bash
uv run pytest tests/test_storage_server.py -q
```
