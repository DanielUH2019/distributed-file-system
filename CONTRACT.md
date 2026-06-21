# The Contract (frozen Day 0)

Decided, not up for unilateral change — all 5 members build against this so work
happens in parallel. Any change needs whole-team sign-off because it breaks someone.

**Transport:** HTTP + JSON everywhere. Chunk bytes sent raw (`application/octet-stream`); metadata is JSON.

**Chunk size:** exactly **1024 bytes** (last chunk may be smaller).

**Replication factor:** **2** (env `REPLICATION_FACTOR`, default 2).

**Chunk ID:** `{filename}_{index}` — e.g. `notes.txt_0`, `notes.txt_1`.

## Service addresses (env-driven; defaults for local compose)

| Service        | Default URL            | Env vars                                   |
|----------------|------------------------|--------------------------------------------|
| Naming server  | `http://naming:8000`   | `DB_PATH`, `REPLICATION_FACTOR`            |
| Storage server | `http://storageN:9000` | `STORAGE_ID`, `STORAGE_PORT`, `DATA_DIR`, `NAMING_URL` |

Storage servers self-register on startup: `POST /storage/register {id, url}`.

## Naming server API (owner: M1) — implemented

```
POST   /storage/register   {id, url}                    -> {ok:true}
GET    /storage                                          -> {servers:[{id,url}]}
GET    /placement/{n}       -> {chunks:[{index, server_ids:[..]}], replication_factor}
POST   /register           {file, size, chunks:[{index, server_ids:[..]}]} -> {ok:true}
GET    /locate/{file}       -> {file, size, chunks:[{index, server_ids, server_urls}]}
GET    /size/{file}         -> {file, size}
DELETE /file/{file}         -> {file, chunks:[{id, server_ids, server_urls}]}
GET    /health              -> {status:"ok"}
```
Placement = round-robin over the live pool, RF distinct servers per chunk.

## Storage server API (owner: M2)

```
PUT    /chunk/{id}   body = raw bytes   -> 200
GET    /chunk/{id}                      -> raw bytes (404 if missing)
DELETE /chunk/{id}                      -> 200 (idempotent)
GET    /health                          -> {id}
```

## Error convention

JSON `{"detail": "..."}` (FastAPI default) with proper status: 404 not found,
503 when too few storage servers / no reachable replica. Client retries the
*other* replica before failing a read.
