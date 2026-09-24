"""Regression tests: "filter by manual" book-scope feature.

Covers the three book-scope helpers (slug_for_source / parse_source_filter /
source_matches), the source-scoped search helpers (_search_json_data /
_search_manuals), and the optional `source=` filter on the five search
endpoints (items, DM monsters, DM monster search, DM monsters by-cr,
DM search-manuals).

The endpoint tests run against the real database (data/characters.db); every
user they register over the /register form is deleted in the module teardown.
"""
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app
from routes.characters.helpers import (
    slug_for_source,
    parse_source_filter,
    source_matches,
    _search_json_data,
    _search_manuals,
)

DB_PATH = Path(__file__).parent.parent / "data" / "characters.db"
FGFD_DISPLAY = "(Field Guide to Floral Dragons, p.14)"
MM_DISPLAY = "(Monster Manual, p.100)"


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """Authenticated TestClient; registers one throwaway npc-* user."""
    with TestClient(app, follow_redirects=False) as c:
        email = f"npc-{uuid.uuid4().hex[:8]}@example.com"
        resp = c.post("/register", data={"email": email, "password": "TestPass123!"})
        assert resp.status_code in (200, 303), f"register failed: {resp.status_code}"
        yield c


@pytest.fixture(scope="module")
def dm_headers(client):
    csrf = client.cookies.get("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": csrf}


@pytest.fixture(scope="module", autouse=True)
def _purge_test_users():
    """Delete every npc-* user (and its sessions) created by this module."""
    yield
    if not DB_PATH.exists():
        return
    con = sqlite3.connect(str(DB_PATH))
    try:
        con.execute(
            "DELETE FROM sessions WHERE user_id IN "
            "(SELECT id FROM users WHERE email LIKE '%npc-%')"
        )
        con.execute("DELETE FROM users WHERE email LIKE '%npc-%'")
        con.commit()
    finally:
        con.close()


# ── slug_for_source ─────────────────────────────────────────────────────────

def test_slug_for_source_display_name_with_page():
    assert slug_for_source(FGFD_DISPLAY) == "FGFD"


def test_slug_for_source_bare_display_name():
    assert slug_for_source("Monster Manual") == "MM"
    assert slug_for_source("Player's Handbook") == "PHB"


def test_slug_for_source_short_code_with_edition_and_page():
    assert slug_for_source("PHB 2014 p.170") == "PHB"


def test_slug_for_source_srd_prefix():
    assert slug_for_source("SRD 5.1") == "SRD"


def test_slug_for_source_empty_returns_empty():
    assert slug_for_source("") == ""
    assert slug_for_source(None) == ""
    assert slug_for_source("   ") == ""


def test_slug_for_source_unknown_display_name_returns_empty():
    assert slug_for_source("Zzzyxx Manual of Nothing") == ""
    assert slug_for_source("(Zzzyxx Manual of Nothing, p.4)") == ""


def test_slug_for_source_strips_whitespace():
    assert slug_for_source(f"   {FGFD_DISPLAY}  ") == "FGFD"


# ── parse_source_filter ─────────────────────────────────────────────────────

def test_parse_source_filter_comma_separated():
    assert parse_source_filter("FGFD,TTP") == {"FGFD", "TTP"}


def test_parse_source_filter_list_input_is_uppercased():
    assert parse_source_filter(["FGFD", "ttp"]) == {"FGFD", "TTP"}


def test_parse_source_filter_lowercase_and_whitespace_slugs():
    assert parse_source_filter(" fgfd , ttp ") == {"FGFD", "TTP"}
    assert parse_source_filter("fgfd") == {"FGFD"}


@pytest.mark.parametrize("raw", ["", None, "all", "ALL", " all ", "*", " , "])
def test_parse_source_filter_empty_means_no_filter(raw):
    assert parse_source_filter(raw) == set()


def test_parse_source_filter_ignores_blank_and_all_entries():
    assert parse_source_filter("FGFD,,all,TTP") == {"FGFD", "TTP"}


# ── source_matches ──────────────────────────────────────────────────────────

def test_source_matches_empty_slug_set_matches_everything():
    assert source_matches(FGFD_DISPLAY, set()) is True
    assert source_matches(MM_DISPLAY, set()) is True
    assert source_matches("", set()) is True


def test_source_matches_scopes_to_chosen_books():
    assert source_matches(FGFD_DISPLAY, {"FGFD"}) is True
    assert source_matches(FGFD_DISPLAY, {"FGFD", "TTP"}) is True
    assert source_matches(MM_DISPLAY, {"FGFD"}) is False
    assert source_matches(MM_DISPLAY, {"FGFD", "TTP"}) is False


def test_source_matches_unresolvable_source_is_excluded():
    # A blank/unknown source can never satisfy a positive book scope.
    assert source_matches("", {"FGFD"}) is False
    assert source_matches("Zzzyxx Manual of Nothing", {"FGFD"}) is False


# ── _search_json_data / _search_manuals scoping ─────────────────────────────

def test_search_json_data_source_scope_is_subset():
    words = ["dragon"]
    unscoped = _search_json_data("dragon", words, max_results=50)
    scoped = _search_json_data("dragon", words, max_results=50, sources={"FGFD"})
    assert scoped, "expected FGFD JSON hits for 'dragon'"
    assert all(r["book"] == "FGFD" for r in scoped)
    keys = {(r["book"], r["line"]) for r in unscoped}
    assert all((r["book"], r["line"]) in keys for r in scoped)


def test_search_json_data_empty_scope_keeps_everything():
    a = _search_json_data("dragon", ["dragon"], max_results=20)
    b = _search_json_data("dragon", ["dragon"], max_results=20, sources=set())
    assert a == b


def test_search_manuals_source_scope_limits_books():
    scoped = _search_manuals("dragon", max_results=25, sources={"FGFD"})
    assert scoped, "expected FGFD manual hits for 'dragon'"
    assert all(r["book"] == "FGFD" for r in scoped)


# ── Endpoint: GET /api/items/search ─────────────────────────────────────────

def test_items_search_unfiltered_total(client):
    resp = client.get("/api/items/search")
    assert resp.status_code == 200
    data = resp.json()
    # Deliberately not an exact count — the index grows with every ingested
    # manual. The scoped tests below pin the FGFD numbers, which only move if
    # that book is re-ingested.
    assert data["total"] > 500
    assert len(data["results"]) <= data["total"]


def test_items_search_fgfd_scope(client):
    unfiltered = client.get("/api/items/search").json()
    resp = client.get("/api/items/search", params={"source": "FGFD"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 14
    assert data["total"] <= unfiltered["total"]
    assert all("Field Guide to Floral Dragons" in it["source"] for it in data["results"])


def test_items_search_unknown_book_returns_nothing(client):
    data = client.get("/api/items/search", params={"source": "NOPE"}).json()
    assert data["total"] == 0
    assert data["results"] == []


def test_items_search_source_and_query_combined(client):
    data = client.get("/api/items/search", params={"source": "FGFD", "q": "wisteria"}).json()
    assert data["total"] == 1
    assert len(data["results"]) == 1
    assert "Field Guide to Floral Dragons" in data["results"][0]["source"]


def test_items_search_source_all_is_no_filter(client):
    unfiltered = client.get("/api/items/search").json()
    allscope = client.get("/api/items/search", params={"source": "all"}).json()
    assert allscope["total"] == unfiltered["total"]


# ── Endpoint: GET /api/dm/monsters/search ───────────────────────────────────

def test_dm_monster_search_unfiltered_count(client, dm_headers):
    data = client.get("/api/dm/monsters/search", headers=dm_headers).json()
    assert data["count"] > 500
    assert data["total"] == data["count"]


def test_dm_monster_search_fgfd_scope(client, dm_headers):
    unfiltered = client.get("/api/dm/monsters/search", headers=dm_headers).json()
    data = client.get(
        "/api/dm/monsters/search", params={"source": "FGFD"}, headers=dm_headers
    ).json()
    assert data["count"] == 30
    assert data["count"] <= unfiltered["count"]
    assert all("(Field Guide to Floral Dragons" in m["source"] for m in data["monsters"])


def test_dm_monster_search_multi_book_scope(client, dm_headers):
    fgfd = client.get(
        "/api/dm/monsters/search", params={"source": "FGFD"}, headers=dm_headers
    ).json()
    both = client.get(
        "/api/dm/monsters/search", params={"source": "FGFD,TTP"}, headers=dm_headers
    ).json()
    assert both["count"] == 36
    assert both["count"] > fgfd["count"]


# ── Endpoint: GET /api/dm/monsters ──────────────────────────────────────────

def test_dm_monsters_fgfd_scope(client, dm_headers):
    unfiltered = client.get("/api/dm/monsters", headers=dm_headers).json()
    data = client.get("/api/dm/monsters", params={"source": "FGFD"}, headers=dm_headers).json()
    assert data["count"] == 30
    assert data["count"] <= unfiltered["count"]
    assert all("(Field Guide to Floral Dragons" in m["source"] for m in data["monsters"])


def test_dm_monsters_source_all_is_no_filter(client, dm_headers):
    unfiltered = client.get("/api/dm/monsters", headers=dm_headers).json()
    allscope = client.get(
        "/api/dm/monsters", params={"source": "all"}, headers=dm_headers
    ).json()
    assert allscope["count"] == unfiltered["count"]


# ── Endpoint: GET /api/dm/monsters/by-cr ────────────────────────────────────

def test_dm_monsters_by_cr_fgfd_scope(client, dm_headers):
    unfiltered = client.get("/api/dm/monsters/by-cr", headers=dm_headers).json()
    data = client.get(
        "/api/dm/monsters/by-cr", params={"source": "FGFD"}, headers=dm_headers
    ).json()

    total = sum(len(v) for v in unfiltered.values() if isinstance(v, list))
    scoped_total = sum(len(v) for v in data.values() if isinstance(v, list))
    assert scoped_total == 30
    assert scoped_total <= total
    for tier in unfiltered:
        if isinstance(data.get(tier), list):
            assert all("(Field Guide to Floral Dragons" in m["source"] for m in data[tier])


# ── Endpoint: POST /api/dm/search-manuals ───────────────────────────────────

def test_search_manuals_unfiltered_spans_books(client, dm_headers):
    resp = client.post("/api/dm/search-manuals", json={"query": "dragon"}, headers=dm_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"], "expected 'dragon' hits across the manuals"
    assert data["sources"] == []
    assert len({r["book"] for r in data["results"]}) > 1


def test_search_manuals_fgfd_scope(client, dm_headers):
    resp = client.post(
        "/api/dm/search-manuals",
        json={"query": "dragon", "source": "FGFD"},
        headers=dm_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["sources"] == ["FGFD"]
    assert data["results"], "expected FGFD hits for 'dragon'"
    assert all(r["book"] == "FGFD" for r in data["results"])


def test_search_manuals_scope_is_subset_of_unscoped(client, dm_headers):
    unscoped = client.post(
        "/api/dm/search-manuals", json={"query": "dragon"}, headers=dm_headers
    ).json()
    scoped = client.post(
        "/api/dm/search-manuals",
        json={"query": "dragon", "source": "FGFD"},
        headers=dm_headers,
    ).json()
    assert scoped["total"] <= unscoped["total"]
    scoped_books = {r["book"] for r in scoped["results"]}
    unscoped_books = {r["book"] for r in unscoped["results"]}
    assert scoped_books <= unscoped_books


# ── Endpoint: GET /api/reference/manual-titles (picker list) ────────────────
# The picker must offer real manuals with clean labels: no chapter/appendix
# aliases, no map/screen PDFs, no duplicate files, no mangled filename titles.

def test_manual_titles_only_real_manuals_with_clean_labels(client):
    manuals = client.get("/api/reference/manual-titles").json()["manuals"]
    assert len(manuals) > 40

    titles = [m["title"] for m in manuals]
    lowered = [t.lower() for t in titles]
    for needle in ("chapter", "appendix", " index", "map", "screen", "final", " v2"):
        assert not any(needle in t for t in lowered), f"junk title containing {needle!r}"

    # Deduped, and no leftover filename noise
    assert len(titles) == len(set(titles))
    assert all("_" not in t for t in titles)
    assert all(t.strip() == t and t for t in titles)


def test_manual_titles_labels_known_books(client):
    manuals = {m["slug"]: m for m in client.get("/api/reference/manual-titles").json()["manuals"]}
    assert manuals["FGFD"]["title"] == "Field Guide to Floral Dragons"
    assert manuals["PHB"]["title"] == "Player's Handbook"
    assert manuals["MM"]["title"] == "Monster Manual"
    # Regression: slug "W" is Warlock-007.pdf but used to display another book's
    # title ("Wrath of the Bramble King"), which WWOTBK legitimately holds.
    assert manuals["W"]["title"] == "Warlock 7"
    assert manuals["WWOTBK"]["title"] == "Wrath of the Bramble King"
    # TCE and DTCOE are the same PDF file -> one entry
    assert ("TCE" in manuals) is not ("DTCOE" in manuals)


def test_manual_titles_match_covers_data_slug_aliases(client):
    manuals = {m["slug"]: m for m in client.get("/api/reference/manual-titles").json()["manuals"]}
    # Loremaster's Guide: its file is shared by slugs LMG2 and LMG, and the data references both.
    # Which of the two survives the by-path de-duplication depends on which one carries data, so
    # find the entry by its alias list instead of assuming the survivor is LMG2.
    entry = next((m for m in manuals.values() if "LMG2" in (m.get("match") or [])), None)
    assert entry, "no manual-titles entry covers the LMG2 alias of the Loremaster's Guide"
    assert "LMG" in entry["match"] and "LMG2" in entry["match"]
    assert all(isinstance(m["match"], list) and m["match"] for m in manuals.values())
    assert all(m["slug"] in m["match"] for m in manuals.values())


def test_manual_titles_is_a_subset_of_the_source_map(client):
    full = client.get("/api/reference/source-map").json()
    manuals = client.get("/api/reference/manual-titles").json()["manuals"]
    assert len(manuals) < len(full)  # source-map carries the chapter aliases
    assert all(m["slug"] in full for m in manuals)
