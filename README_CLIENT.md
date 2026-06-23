# DFS Client

User-facing CLI for the distributed file system. Hides chunking, replication, and
storage placement behind four commands.

Chunk placement and chunk locations come from the **naming server** (the metadata
authority): `create` asks `GET /placement/{n}` where to store each chunk, and
`read`/`delete` use the locations returned by `/locate` and `/file`. The client
therefore does not need to know the storage pool up front.

## Environment variables

| Variable            | Required | Default               | Description |
|---------------------|----------|-----------------------|-------------|
| `NAMING_URL`        | no       | `http://naming:8000`  | Naming server base URL |
| `REPLICATION_FACTOR`| no       | `2`                   | Number of replicas per chunk |
| `STORAGE_SERVERS`   | no       | —                     | Optional fallback only: comma-separated storage URLs, used to resolve URLs if the naming server omits them |
| `REQUEST_TIMEOUT`   | no       | `10.0`                | HTTP timeout in seconds |

Example:

```bash
export NAMING_URL=http://localhost:8000
export REPLICATION_FACTOR=2
```

## Usage

```bash
# Upload a local text file (chunked to 1024-byte blocks, replicated to 2 servers)
python client.py create ./notes.txt

# Download a file (writes to ./notes.txt by default)
python client.py read notes.txt

# Download to a specific path
python client.py read notes.txt ./backup/notes.txt

# Delete a file and purge all chunk replicas
python client.py delete notes.txt

# Print file size in bytes
python client.py size notes.txt
```

With `uv`:

```bash
STORAGE_SERVERS=http://storage1:9000,http://storage2:9000,http://storage3:9000 \
  uv run python client.py create ./notes.txt
```

## Tests

```bash
uv run pytest tests/test_client.py -v
```
