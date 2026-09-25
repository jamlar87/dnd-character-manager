"""Reference art must not be cached for hours.

The regression this guards: portraits are REWRITTEN IN PLACE behind a stable URL. The filename is a
slug derived from the record name, so regenerating a portrait does not change its address. With the
old `public, max-age=14400`, a regenerated image was invisible to the browser and to Cloudflare for
four hours — measured directly: a second request for a just-regenerated file returned
`cf-cache-status: HIT`. The ref-image endpoint was worse at `max-age=604800` (a week).

Short max-age plus must-revalidate is the fix: an unchanged file still answers 304, so freshness
costs almost nothing.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _max_age(header: str) -> int:
    m = re.search(r"max-age=(\d+)", header or "")
    return int(m.group(1)) if m else 0


def test_static_files_are_not_cached_for_hours():
    from fastapi.testclient import TestClient
    import main

    # Pick a real file that exists, so the response is a genuine static hit and not a 404.
    candidates = sorted((ROOT / "static").rglob("*.webp"))
    if not candidates:
        pytest.skip("no static webp to test with")
    rel = candidates[0].relative_to(ROOT / "static").as_posix()

    with TestClient(main.app) as client:
        r = client.get(f"/static/{rel}")
    assert r.status_code == 200, f"expected 200 for /static/{rel}, got {r.status_code}"
    cc = r.headers.get("cache-control", "")
    assert _max_age(cc) <= 600, (
        f"static max-age must stay short so regenerated art appears; got {cc!r}")
    assert "must-revalidate" in cc, (
        f"without must-revalidate a stale entry can be served past max-age: {cc!r}")


def test_the_ref_image_endpoint_is_not_cached_for_a_week():
    """This one literally said max-age=604800 — a week of showing the previous portrait."""
    from fastapi.testclient import TestClient
    import main

    with TestClient(main.app) as client:
        r = client.get("/api/ref-image/npc/__nonexistent__")
    # 404 is fine here: the header is what matters, and the route must not claim a week-long cache.
    cc = r.headers.get("cache-control", "")
    assert _max_age(cc) <= 600, f"ref-image max-age must stay short; got {cc!r}"
