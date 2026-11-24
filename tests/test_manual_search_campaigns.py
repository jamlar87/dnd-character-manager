"""Reference-manual cache and search must cover the adventure/campaign books.

Regression: pdf_map paths are stored relative to the DnD-Manuals *parent*
folder ("DnD-Manuals/Campaigns/…") while MANUALS_DIR already points at that
folder, so a naive join doubled the prefix.  Every campaign book silently fell
out of the text cache, which made adventure content invisible to the DM manual
search even though the PDFs were present and ingested.
"""
import json
import os

import pytest

from routes.characters import helpers


def _manual_root_available() -> bool:
    return helpers.MANUALS_DIR.is_dir()


pytestmark = pytest.mark.skipif(
    not _manual_root_available(),
    reason=f"manual PDF library not mounted at {helpers.MANUALS_DIR}",
)


def _pdf_map():
    meta = json.loads((helpers.DATA_DIR / "manual_data" / "_meta.json").read_text())
    return meta["pdf_map"]


def test_every_pdf_map_entry_resolves():
    missing = [slug for slug, entry in _pdf_map().items()
               if helpers._resolve_manual_pdf(entry["path"]) is None]
    assert missing == []


def test_campaign_books_are_cached():
    cached = helpers._ensure_manual_cache()
    for label in ("LMoP", "ToA", "WDH", "HotDQ", "RoT", "CotN"):
        assert label in cached, f"{label} missing from the manual text cache"
        assert cached[label].stat().st_size > 100_000, f"{label} text cache is empty"


def test_phrase_prefixed_paths_resolve():
    """The doubled-prefix shape that used to break every campaign book."""
    entry = _pdf_map()["LMoP"]["path"]
    assert entry.startswith("DnD-Manuals/")
    resolved = helpers._resolve_manual_pdf(entry)
    assert resolved is not None and resolved.is_file()
    assert "DnD-Manuals/DnD-Manuals" not in str(resolved)


@pytest.mark.parametrize("query,book", [
    ("Wave Echo Cave", "LMoP"),
    ("Port Nyanzaru", "ToA"),
    ("Vault of Dragons", "WDH"),
])
def test_search_ranks_the_owning_adventure_first(query, book):
    results = helpers._search_manuals(query, 5)
    assert results, f"no manual hits for {query!r}"
    assert results[0]["book"] == book, (
        f"{query!r} ranked {results[0]['book']} first; own words in {book}"
    )


def test_search_route_serves_campaign_hits(client, auth_headers):
    """The DM screen's research endpoint must return adventure text, not just rulebooks."""
    resp = client.post("/api/dm/search-manuals",
                       json={"query": "Wave Echo Cave"}, headers=auth_headers)
    assert resp.status_code == 200, resp.text[:200]
    payload = resp.json()
    results = payload.get("results", [])
    assert results, payload
    assert results[0]["book"] == "LMoP"
    assert "results" in payload or isinstance(payload, list)


def test_search_route_honours_a_campaign_source_filter(client, auth_headers):
    """Picking only an adventure in the source picker must scope results to it."""
    resp = client.post("/api/dm/search-manuals",
                       json={"query": "Wave Echo Cave", "source": ["LMoP"]},
                       headers=auth_headers)
    assert resp.status_code == 200, resp.text[:200]
    results = resp.json()["results"]
    assert results, "campaign-scoped search returned nothing"
    assert {r["book"] for r in results} == {"LMoP"}
