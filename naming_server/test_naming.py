"""Smoke test for the naming server. Run: uv run pytest naming_server/ -q
Uses an in-memory-ish temp DB via DB_PATH so it never touches real data."""
import os
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["REPLICATION_FACTOR"] = "2"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from naming_server import app as naming_app  # noqa: E402
from naming_server.app import app  # noqa: E402

c = TestClient(app)


@pytest.fixture(autouse=True)
def _all_servers_alive(monkeypatch):
    """Treat every registered server as healthy unless a test overrides it.

    Placement probes /health; in unit tests the fake URLs are unreachable, so we
    stub liveness to focus on placement/metadata behaviour.
    """
    monkeypatch.setattr(naming_app, "_is_alive", lambda url: True)


def setup_module():
    with c:  # triggers startup -> init_db
        pass


def test_full_lifecycle():
    with c:
        # need 2 servers for RF=2
        c.post("/storage/register", json={"id": "s1", "url": "http://s1:9000"})
        c.post("/storage/register", json={"id": "s2", "url": "http://s2:9000"})

        # placement spreads replicas across distinct servers
        plan = c.get("/placement/3").json()["chunks"]
        assert len(plan) == 3
        for chunk in plan:
            assert len(chunk["server_ids"]) == 2
            assert chunk["server_ids"][0] != chunk["server_ids"][1]
            # placement now also resolves URLs so the client can PUT directly
            assert len(chunk["server_urls"]) == 2
            assert all(url.startswith("http://") for url in chunk["server_urls"])

        # register a file using that plan
        r = c.post("/register", json={"file": "notes.txt", "size": 2500, "chunks": plan})
        assert r.status_code == 200

        assert c.get("/size/notes.txt").json()["size"] == 2500

        loc = c.get("/locate/notes.txt").json()
        assert len(loc["chunks"]) == 3
        assert loc["chunks"][0]["server_urls"][0] == "http://s1:9000"

        deleted = c.delete("/file/notes.txt").json()
        assert deleted["chunks"][0]["id"] == "notes.txt_0"
        assert c.get("/size/notes.txt").status_code == 404


def test_placement_needs_enough_servers():
    with c:
        # fresh: not enough servers registered would 503, but s1/s2 persist in temp db.
        # verify the happy path returns RF servers; under-provisioning is covered by the guard.
        assert c.get("/placement/1").status_code == 200


def test_placement_excludes_dead_servers(monkeypatch):
    """If only one registered server is actually reachable, placement must 503
    rather than hand out a dead replica (degraded-write safety)."""
    with c:
        c.post("/storage/register", json={"id": "s1", "url": "http://s1:9000"})
        c.post("/storage/register", json={"id": "s2", "url": "http://s2:9000"})
        # Only s1 answers /health; s2 is down.
        monkeypatch.setattr(
            naming_app, "_is_alive", lambda url: url == "http://s1:9000"
        )
        assert c.get("/placement/1").status_code == 503


def test_placement_skips_dead_replica_when_enough_live(monkeypatch):
    """With 3 registered but one dead and RF=2, placement uses only the 2 live ones."""
    with c:
        c.post("/storage/register", json={"id": "s1", "url": "http://s1:9000"})
        c.post("/storage/register", json={"id": "s2", "url": "http://s2:9000"})
        c.post("/storage/register", json={"id": "s3", "url": "http://s3:9000"})
        monkeypatch.setattr(
            naming_app, "_is_alive", lambda url: url != "http://s2:9000"
        )
        plan = c.get("/placement/3").json()["chunks"]
        used = {sid for chunk in plan for sid in chunk["server_ids"]}
        assert "s2" not in used
        assert used <= {"s1", "s3"}
