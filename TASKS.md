# Distributed File System — Task Breakdown (5 members)

One Git repo. Stack: Python + FastAPI, SQLite for metadata, chunks as files on disk.
Replication factor = 2. All components talk over **HTTP + JSON**.

Goal: each member owns a vertical slice so everyone has real commits.

---

## The Contract (frozen Day 0 — build against this, do not change unilaterally)

This is the agreement. It's decided, not "to be discussed" — so all 5 can code in parallel.
Any change requires the whole team's sign-off because it breaks someone's component.

**Transport:** HTTP + JSON everywhere. Chunk bytes are sent raw (`application/octet-stream`),
metadata is JSON.

**Chunk size:** exactly **1024 bytes** (last chunk may be smaller).

**Replication factor:** **2** (env var `REPLICATION_FACTOR`, default 2).

**Chunk ID:** `{filename}_{index}` — e.g. `notes.txt_0`, `notes.txt_1`.

**Service addresses (env-driven, defaults for local compose):**
| Service        | Default URL              | Env var          |
|----------------|--------------------------|------------------|
| Naming server  | `http://naming:8000`     | `NAMING_URL`     |
| Storage server | `http://storageN:9000`   | `STORAGE_PORT`, `STORAGE_ID`, `DATA_DIR` |

Storage servers register themselves with the naming server on startup via
`POST /storage/register {"id": "...", "url": "..."}` so naming knows the live pool.

### Naming server API (owner: M1)
```
POST   /storage/register   {id, url}                  -> 200
POST   /register           {file, size, chunks:[      -> 200
                              {index, server_ids:[..]}]}
GET    /locate/{file}       -> {size, chunks:[{index, server_ids:[..]}]}
GET    /size/{file}         -> {size}
DELETE /file/{file}         -> {chunks:[{id, server_ids:[..]}]}   (so client can purge)
```
Placement: round-robin over the registered storage pool, REPLICATION_FACTOR distinct servers per chunk.

### Storage server API (owner: M2)
```
PUT    /chunk/{id}          body = raw bytes          -> 200
GET    /chunk/{id}          -> raw bytes (404 if missing)
DELETE /chunk/{id}          -> 200 (idempotent)
GET    /health             -> 200 {"id": "..."}
```

### Error convention
JSON `{"error": "..."}` with proper HTTP status (404 not found, 503 when a chunk
has no reachable replica). Client retries the *other* replica before failing a read.

This block lives in `CONTRACT.md` at repo root (M1 commits it Day 0; everyone reads it).

---

## Members & ownership

### Member 1 — Naming Server (metadata authority)
- SQLite schema: `files(name, size, num_chunks)`, `chunks(file, index, server_ids)`.
- `POST /register` — store file + chunk→server placement.
- `GET /locate/{file}` — return chunk locations.
- `DELETE /file/{file}` — drop metadata, return chunk list to purge.
- `GET /size/{file}` — size from metadata only.
- Placement logic: pick N servers per chunk (round-robin is fine).

### Member 2 — Storage Server (runs as N replicas)
- `PUT /chunk/{id}` — write bytes to local disk (one file per chunk).
- `GET /chunk/{id}` — read bytes.
- `DELETE /chunk/{id}` — remove file.
- `GET /health` — for the fault-tolerance demo.
- Reads its own ID / port / data-dir from env vars.

### Member 3 — Client (user-facing tool, hides distribution)
- **Create:** split file into 1 KB chunks → ask naming server for placement → `PUT` each chunk to its 2 replicas → register.
- **Read:** `locate` → fetch each chunk from any available replica → reassemble.
- **Delete:** naming server delete → `DELETE` chunks from all servers.
- **Size:** call naming server `size`.
- CLI: `client.py create/read/delete/size <file>`.

### Member 4 — Infra & Packaging (deliverables 2 & 3)
- Dockerfiles for naming server, storage server, client.
- `docker-compose.yml`: 1 naming + ≥3 storage + client, with ports/env/volumes.
- README "How to run".
- `demo.sh` — create/read/delete end-to-end (doubles as integration test).

### Member 5 — Docs & Fault-Tolerance Analysis (deliverables 1 & 4 — graded reasoning)
- Architecture doc: diagram, chunking + replication design, trade-offs.
- Fault-tolerance section (all 4 PDF questions):
  - one storage server down → reads survive (other replica), writes degrade.
  - naming server down → **single point of failure**, system unavailable.
  - replication survives `replication_factor − 1` simultaneous storage failures.
  - recoverable vs. data-loss scenarios.
- README "How to use" with client examples.

---

## Parallel vs. dependent

```
Day 0  CONTRACT (whole team) ── must finish first ──┐
                                                    │
        ┌───────────────┬───────────────┬──────────┴──────────┐
        ▼               ▼               ▼                      ▼
   M1 Naming       M2 Storage      M5 Docs (start          (M3 waits
     server          server         design/diagram          on stubs)
        │               │            immediately)
        └──────┬────────┘
               ▼
         M3 Client  ── needs M1 + M2 endpoints live (or mocked) ──
               │
               ▼
         M4 Docker + demo.sh ── needs all 3 services runnable ──
               │
               ▼
         M5 finalizes fault-tolerance analysis (runs demo.sh,
            kills a storage server, documents what survives)
```

**Fully parallel from Day 0 (no dependencies on each other):**
- M1 Naming Server
- M2 Storage Server
- M5 architecture doc + diagram (design is known up front)

**Has dependencies:**
- **M3 (Client)** depends on M1 + M2 contracts. Unblock early by coding against the frozen endpoints with simple mocks/stubs, then integrate when M1/M2 land.
- **M4 (Docker/compose/demo)** depends on all three services being runnable. M4 can write Dockerfiles in parallel using each component's expected run command, but `demo.sh` and full `docker-compose up` need M1+M2+M3 working.
- **M5 final fault-tolerance section** depends on M4's compose stack (needs to kill a container and observe behavior). The *design* half is parallel; the *empirical* half is last.

**Critical path:** Day 0 → M1+M2 → M3 → M4 → M5 final.
M5's doc work runs alongside the whole thing, so M5 is never idle.

---

## Everyone-commits checklist
- [ ] M1 — naming server + schema
- [ ] M2 — storage server
- [ ] M3 — client + CLI
- [ ] M4 — Dockerfiles, docker-compose, demo.sh
- [ ] M5 — architecture doc, fault-tolerance analysis, README "how to use"
