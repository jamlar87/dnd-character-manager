"""PWA: installable, and honest about being offline.

The value of these tests is mostly in what they refuse: a service worker that caches a sheet, or
that sits in front of the table channel's EventSource, would break the app in ways that only show
up at a real table — offline and half-live. So the rules are asserted here, and the live behaviour
is proven against a real browser in the slice verification.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "static"


def _png_size(path: Path) -> tuple[int, int]:
    """Real pixel dimensions out of the PNG header: the manifest's claim has to match the bytes."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def test_the_manifest_is_served_with_a_type_browsers_accept(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json"), (
        "a manifest with the wrong MIME type is ignored entirely")

    m = json.loads(r.text)
    assert m["name"] and m["short_name"]
    assert m["start_url"] == "/" and m["scope"] == "/", "the worker must control the whole origin"
    assert m["display"] == "standalone"
    assert m["theme_color"] and m["background_color"]
    purposes = {i["purpose"] for i in m["icons"]}
    assert "any" in purposes and "maskable" in purposes, "a maskable icon for Android"


def test_every_icon_the_manifest_promises_exists_at_the_size_it_claims(client):
    m = json.loads(client.get("/manifest.webmanifest").text)
    for icon in m["icons"]:
        src = icon["src"]
        path = STATIC / src.replace("/static/", "")
        assert path.exists(), f"{src} is in the manifest but not on disk"
        width, height = _png_size(path)
        assert f"{width}x{height}" == icon["sizes"], f"{src} is {width}x{height}, manifest says {icon['sizes']}"
    assert (STATIC / "icons" / "apple-touch-icon-180.png").exists(), "iOS reads this one, not the manifest"


def test_the_service_worker_is_never_cacheable(client):
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert "no-store" in r.headers.get("cache-control", ""), (
        "a CDN-pinned sw.js is a service worker that can never update")
    assert r.headers.get("service-worker-allowed") == "/"
    assert "self.addEventListener('fetch'" in r.text


def test_the_worker_stays_out_of_writes_and_out_of_live_traffic():
    src = (STATIC / "sw.js").read_text()
    assert "req.method !== 'GET'" in src, "writes must fail loudly, never appear to succeed offline"
    assert "url.pathname.indexOf('/api/') === 0" in src
    assert "url.pathname.indexOf('stream') !== -1" in src, (
        "the table channel is an EventSource: a worker in front of it is a silent table")


def test_the_worker_caches_assets_but_never_a_navigation():
    """Only the versioned static branch may write to the cache: a cached sheet is a stale lie."""
    src = (STATIC / "sw.js").read_text()
    fetch_handler = src[src.index("self.addEventListener('fetch'"):]
    assert fetch_handler.count("c.put(") == 1, "exactly one cache write in the whole worker"
    navigate = fetch_handler[fetch_handler.index("req.mode === 'navigate'"):]
    assert "c.put(" not in navigate and "cache.put" not in navigate
    assert "caches.match(OFFLINE)" in navigate, "offline navigations get the notice, not a stale page"
    assert "'/static/'" in fetch_handler


def test_the_offline_page_says_what_still_works(client):
    r = client.get("/offline")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "offline" in r.text.lower()
    assert "nothing is queued" in r.text.lower(), (
        "the honest part: a roll made offline is not saved for later, and pretending otherwise is "
        "how a table loses an evening")


def test_both_surfaces_link_the_manifest_and_load_the_wiring():
    """The players' page is standalone (it does not extend layout.html), so it needs its own copy —
    exactly the trap that once put a concentration card in a template nothing rendered."""
    layout = (REPO / "templates" / "layout.html").read_text()
    table = (REPO / "templates" / "table.html").read_text()
    for name, text in (("layout.html", layout), ("table.html", table)):
        assert 'rel="manifest"' in text, f"{name} does not link the manifest"
        assert "pwa.js?v=" in text, f"{name} does not load the PWA wiring with a version"
        assert "apple-touch-icon" in text, f"{name} has no iOS icon"
    assert 'id="installHint"' in table, "the players' surface is the one worth installing"
    pwa = (STATIC / "pwa.js").read_text()
    assert "navigator.serviceWorker.register('/sw.js'" in pwa
    assert "updateViaCache: 'none'" in pwa, (
        "the worker's update check must not be answered by the browser's HTTP cache")
    assert "beforeinstallprompt" in pwa and "dismissInstallHint" in pwa


# ── the family bug the PWA probe ran into ───────────────────────────────────────────────

def test_a_body_less_post_is_a_bad_request_not_a_crash(client, auth_headers):
    """~80 routes call `await request.json()` unguarded. One app-level handler covers them all,
    which is why this test asks the route that was still returning 500 (the player key)."""
    r = client.post("/api/campaign/1/player-key", headers=auth_headers)
    assert r.status_code == 400, f"a body-less POST must be a bad request, got {r.status_code}"
    assert "json body" in r.json()["error"].lower()

    r = client.post("/api/campaign/1/player-key", content=b"{not json",
                    headers={**auth_headers, "Content-Type": "application/json"})
    assert r.status_code == 400, "malformed JSON is the same class of bad request"


def test_the_handler_covers_routes_that_never_guarded_their_body():
    """Text guard: the handler exists, and it is registered for JSONDecodeError itself rather than
    for one route — otherwise the next new route repeats the bug."""
    src = (REPO / "main.py").read_text()
    assert "exception_handler(json.JSONDecodeError)" in src
    assert '{"error": "expected a JSON body"}' in src


def test_worker_navigations_force_the_network():
    """A worker's fetch() can be answered by the browser's own HTTP cache. For a live page that is
    the exact staleness this worker must not introduce, so the navigation fetch is explicit."""
    src = (STATIC / "sw.js").read_text()
    navigate = src[src.index("req.mode === 'navigate'"):]
    assert "fetch(req, {cache: 'no-store'})" in navigate, (
        "without no-store a navigation can be served from the HTTP cache, and a device with no "
        "network can be handed a stale page instead of the offline notice")


def test_the_cache_identity_is_versioned_so_a_rule_change_lands():
    """Changing the caching rules has to invalidate what devices already hold, or the old rules
    keep running out of the old cache."""
    src = (STATIC / "sw.js").read_text()
    assert "const CACHE = 'dnd-shell-v" in src
    assert "k !== CACHE" in src and "caches.delete(k)" in src, "activate removes every older cache"
