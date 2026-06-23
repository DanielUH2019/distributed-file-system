# Storage Server

Raw chunk storage node for the distributed file system. Stores chunk bytes on disk
with atomic writes, path sanitization, and two-level directory sharding.

## Environment variables

| Variable       | Required | Default   | Description                          |
|----------------|----------|-----------|--------------------------------------|
| `STORAGE_ID`   | yes      | —         | Unique ID for this node (e.g. `storage-1`) |
| `STORAGE_PORT` | no       | `9000`    | HTTP listen port                     |
| `DATA_DIR`     | no       | `./data`  | Directory for chunk files (created if missing) |
| `NAMING_URL`   | no       | —         | Naming server URL. When set, the node self-registers on startup; unset = no registration (e.g. tests) |
| `STORAGE_URL`  | no       | `http://{STORAGE_ID}:{STORAGE_PORT}` | Address peers use to reach this node (advertised during registration) |

On startup, if `NAMING_URL` is set, the node registers itself with the naming
server (`POST /storage/register {id, url}`), retrying in the background so a slow
or unavailable naming server never blocks chunk serving.

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
