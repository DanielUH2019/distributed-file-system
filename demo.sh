#!/usr/bin/env bash
# End-to-end demo + integration test for the distributed file system.
#
# Brings up the full stack, then exercises create -> size -> read -> delete and
# verifies the round-trip is byte-for-byte correct. Also demonstrates fault
# tolerance: a storage server is stopped mid-flight and reads still succeed from
# the surviving replica.
#
# Exits non-zero on any failure, so it doubles as CI.
#
# Usage: ./demo.sh
set -euo pipefail

cd "$(dirname "$0")"

COMPOSE="docker compose"
FILES_DIR="./files"
SRC_NAME="demo.txt"
SRC="${FILES_DIR}/${SRC_NAME}"
OUT="${FILES_DIR}/downloaded_${SRC_NAME}"

green() { printf "\033[0;32m%s\033[0m\n" "$1"; }
red()   { printf "\033[0;31m%s\033[0m\n" "$1"; }
step()  { printf "\n\033[1;34m== %s ==\033[0m\n" "$1"; }

cleanup() {
  step "Tearing down"
  $COMPOSE down -v >/dev/null 2>&1 || true
}
trap cleanup EXIT

# --- 0. fresh sample file (>1 KB so it spans multiple chunks) ----------------
step "Preparing sample file (${SRC})"
mkdir -p "$FILES_DIR"
rm -f "$OUT"
{
  for i in $(seq 1 80); do
    echo "Line $i: the quick brown fox jumps over the lazy dog. 0123456789"
  done
} > "$SRC"
SRC_SIZE=$(wc -c < "$SRC" | tr -d ' ')
green "Created ${SRC} (${SRC_SIZE} bytes, ~$(( (SRC_SIZE + 1023) / 1024 )) chunks)"

# --- 1. bring the cluster up -------------------------------------------------
step "Starting cluster (1 naming + 3 storage)"
$COMPOSE up --build -d naming storage1 storage2 storage3

# Wait until naming + all storage report healthy (compose healthchecks).
step "Waiting for services to become healthy"
for svc in naming storage1 storage2 storage3; do
  for _ in $(seq 1 30); do
    status=$($COMPOSE ps --format '{{.Health}}' "$svc" 2>/dev/null || echo "")
    if [ "$status" = "healthy" ]; then
      green "$svc healthy"
      break
    fi
    sleep 1
  done
done

# Give storage servers a moment to self-register, then ask the naming server how
# many are in the live pool. Naming is published on localhost:8000.
sleep 2
REGISTERED=$(python3 -c "import urllib.request,json; print(len(json.load(urllib.request.urlopen('http://localhost:8000/storage'))['servers']))" 2>/dev/null | tail -1)
green "Storage servers registered with naming: ${REGISTERED:-unknown}"
if [ "${REGISTERED:-0}" -lt 2 ]; then
  red "FAIL: fewer than 2 storage servers registered"; exit 1
fi

run_client() { $COMPOSE run --rm --no-deps client "$@"; }

# --- 2. create ---------------------------------------------------------------
step "create ${SRC_NAME}"
run_client create "/files/${SRC_NAME}"

# --- 3. size (from metadata only) -------------------------------------------
step "size ${SRC_NAME}"
REPORTED_SIZE=$(run_client size "${SRC_NAME}" | tail -1 | tr -d '\r')
green "naming reports size = ${REPORTED_SIZE} (expected ${SRC_SIZE})"
if [ "$REPORTED_SIZE" != "$SRC_SIZE" ]; then
  red "FAIL: size mismatch"; exit 1
fi

# --- 4. fault tolerance: kill a storage server, read must still work ---------
step "Fault tolerance: stopping storage2, then reading"
$COMPOSE stop storage2 >/dev/null
run_client read "${SRC_NAME}" "/files/downloaded_${SRC_NAME}"

if diff -q "$SRC" "$OUT" >/dev/null; then
  green "Read reassembled correctly WITH storage2 down (replica survived)"
else
  red "FAIL: read with storage2 down did not match original"; exit 1
fi

# bring storage2 back for the rest of the demo
$COMPOSE start storage2 >/dev/null
sleep 2

# --- 5. delete ---------------------------------------------------------------
step "delete ${SRC_NAME}"
run_client delete "${SRC_NAME}"

# size after delete should fail (file gone)
step "Verify file is gone (size should fail)"
if run_client size "${SRC_NAME}" >/dev/null 2>&1; then
  red "FAIL: file still present after delete"; exit 1
else
  green "Confirmed: ${SRC_NAME} no longer exists"
fi

step "RESULT"
green "ALL CHECKS PASSED ✅  (create / size / read-with-failover / delete)"
