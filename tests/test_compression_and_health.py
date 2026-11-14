"""The origin must compress its own text responses, and must say whether it is alive.

Two regressions are guarded here.

1. `/static/sheet.js` is 317KB of un-minified JS and `dm_tools.js` another 291KB. The origin
   answered `content-length: 324864` with no `Content-Encoding`: Cloudflare gzips for the
   browser, so the saving was invisible on the public URL and the full payload crossed the
   wire for LAN clients and on every cache miss. `_CompressibleGZip` is mounted for that.

2. There was no health endpoint at all — `GET /api/health` was a 404, so an uptime check had
   to fetch `/` and infer health from a 200. `/api/health` now probes the database and says
   so, which is the difference between "the process is up" and "the process can serve".

Range requests are the reason the middleware is a subclass: a 206 hands out byte offsets into
the uncompressed body, so gzipping it would return a file the range does not describe.
"""


def test_static_asset_is_gzipped_when_the_client_asks(client):
    r = client.get("/static/sheet.js", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    assert "accept-encoding" in r.headers.get("vary", "").lower()
    # The body is streamed once compressed, so Content-Length is absent rather than shrunk —
    # but if it is present it must describe the compressed size, not the 317KB file.
    if "content-length" in r.headers:
        assert int(r.headers["content-length"]) < 200_000


def test_static_asset_is_not_encoded_for_a_client_that_cannot_accept_it(client):
    r = client.get("/static/sheet.js", headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") is None
    assert int(r.headers["content-length"]) > 200_000


def test_range_request_is_never_compressed(client):
    """A 206 must describe bytes of the file the client actually receives."""
    r = client.get(
        "/static/sheet.js",
        headers={"Accept-Encoding": "gzip", "Range": "bytes=0-99"},
    )
    assert r.status_code == 206
    assert r.headers.get("content-encoding") is None
    assert r.headers.get("content-range", "").startswith("bytes 0-99/")
    assert len(r.content) == 100


def test_health_reports_the_database(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert isinstance(body["uptime_s"], int)


def test_health_is_not_cacheable_and_plants_no_cookie(client):
    r = client.get("/api/health")
    assert "set-cookie" not in r.headers
    assert r.headers.get("cache-control") == "no-store"


def test_health_is_degraded_when_the_database_is_unreachable(client, monkeypatch):
    import main

    def _boom():
        raise OSError("database is gone")

    monkeypatch.setattr(main, "get_db", _boom)
    r = client.get("/api/health")
    assert r.status_code == 503
    assert r.json()["status"] == "degraded"
    assert "database is gone" in r.json()["db"]
