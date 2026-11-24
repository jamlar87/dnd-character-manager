"""`/api/health?deep=1` extends the liveness answer into a usable diagnosis.

The shallow probe only proves the process is up and the database answers. That
is the wrong question at 8pm with players waiting: a dead GPU, an unloaded SRD or
a full disk all break the session while the shallow probe still says "ok". Deep
mode reports each soft dependency by name and keeps the database as the single
hard failure — art and NPC prose degrade, the table does not.
"""

import main


def _stub_probes(monkeypatch, *failing):
    """Make every network probe answer, with the named ones failing."""
    def fake(url, timeout=2.0, headers=None):
        for name in failing:
            if name in url:
                return False, "ConnectionRefusedError: refused"
        return True, "HTTP 200"
    monkeypatch.setattr(main, "_probe_http", fake)


def test_deep_mode_reports_every_soft_dependency(client, monkeypatch):
    _stub_probes(monkeypatch)
    r = client.get("/api/health?deep=1")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"srd_spells", "srd_monsters", "gpu_vlm",
                                   "freellmapi", "disk"}
    assert body["degraded"] == []
    assert body["counts"]["characters"] >= 0
    assert body["counts"]["campaigns"] >= 0


def test_a_dead_gpu_degrades_without_failing_the_probe(client, monkeypatch):
    _stub_probes(monkeypatch, "11434")
    r = client.get("/api/health?deep=1")
    assert r.status_code == 200, "a dead GPU must not read as a dead server"
    body = r.json()
    assert body["degraded"] == ["gpu_vlm"]
    assert body["checks"]["gpu_vlm"]["ok"] is False
    assert "refused" in body["checks"]["gpu_vlm"]["detail"]


def test_deep_mode_still_503s_on_a_dead_database(client, monkeypatch):
    _stub_probes(monkeypatch)

    def _boom():
        raise OSError("database is gone")

    monkeypatch.setattr(main, "get_db", _boom)
    r = client.get("/api/health?deep=1")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert "database is gone" in body["db"]
    assert "counts" not in body


def test_shallow_mode_stays_small(client):
    body = client.get("/api/health").json()
    assert set(body) == {"status", "db", "uptime_s"}
