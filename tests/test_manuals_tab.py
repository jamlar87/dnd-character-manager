"""Regression tests: the 📚 Manuals tab must list each book once.

The manual tree reaches one PDF through several routes — the top-level
convenience symlink in manuals/, the DnD-Manuals library symlink, and a nested
Manuals/ copy for a few core books — so a naive glob listed 14 books two or
three times (Field_Guide_to_Floral_Dragons showed up twice in the tab).
"""
import re
import sqlite3
import uuid
from collections import Counter
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from main import app, _scan_manual_pdfs

DB_PATH = Path(__file__).parent.parent / "data" / "characters.db"


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
    return {"X-CSRF-Token": client.cookies.get("csrf_token", "test-csrf-token")}


@pytest.fixture(scope="module", autouse=True)
def cleanup_npc_users():
    """Delete every throwaway npc-* user registered by this module."""
    yield
    db = sqlite3.connect(DB_PATH)
    for (uid,) in db.execute("SELECT id FROM users WHERE email LIKE '%npc-%'").fetchall():
        db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        db.execute("DELETE FROM users WHERE id=?", (uid,))
    db.commit()
    db.close()


# ── _scan_manual_pdfs() ─────────────────────────────────────────────────────

def test_scan_manual_pdfs_dedupes_symlink_and_nested_copy(tmp_path, monkeypatch):
    """One row per book: a symlink alias and a same-named copy collapse."""
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "Book_One.pdf").write_bytes(b"%PDF-1.4 one")
    (lib / "Book_Two.pdf").write_bytes(b"%PDF-1.4 two")
    nested = lib / "Manuals"
    nested.mkdir()
    (nested / "Book_One.pdf").write_bytes(b"%PDF-1.4 one")  # duplicate copy

    root = tmp_path / "manuals"
    root.mkdir()
    (root / "library").symlink_to(lib, target_is_directory=True)
    (root / "Book_Two.pdf").symlink_to(lib / "Book_Two.pdf")  # alias of same file

    monkeypatch.setattr(main, "MANUALS_BASE", root)
    found = _scan_manual_pdfs()
    names = sorted(p.stem for p in found)
    assert names == ["Book_One", "Book_Two"], names
    # The shallowest path wins, so the alias at the root is the one kept
    kept_two = [str(p.relative_to(root)) for p in found if p.stem == "Book_Two"]
    assert kept_two == ["Book_Two.pdf"], kept_two


def test_scan_manual_pdfs_keeps_distinct_books(tmp_path, monkeypatch):
    """Different books in different folders all survive."""
    root = tmp_path / "manuals"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir(parents=True)
    (root / "a" / "Alpha.pdf").write_bytes(b"%PDF-1.4")
    (root / "b" / "Beta.pdf").write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(main, "MANUALS_BASE", root)
    assert sorted(p.stem for p in _scan_manual_pdfs()) == ["Alpha", "Beta"]


def test_scan_manual_pdfs_missing_base_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "MANUALS_BASE", tmp_path / "nope")
    assert _scan_manual_pdfs() == []


# ── Rendered surfaces ───────────────────────────────────────────────────────

def test_manuals_tab_lists_each_book_once(client, dm_headers):
    html = client.get("/dm-tools", headers=dm_headers).text
    assert html.count('data-tab="manuals"') == 1  # the tab itself exists

    names = re.findall(r'📄</span>\s*<span[^>]*title="([^"]+)"', html)
    assert len(names) > 40, f"expected the manual list to render, got {len(names)} rows"
    dupes = {k: v for k, v in Counter(names).items() if v > 1}
    assert not dupes, f"duplicate rows in the Manuals tab: {dupes}"

    # Exactly the books the scan reports, no more and no less
    assert len(names) == len(set(names))


def test_manuals_tab_has_no_floral_dragon_duplicate(client, dm_headers):
    """The reported bug: Field_Guide_to_Floral_Dragons appeared twice."""
    html = client.get("/dm-tools", headers=dm_headers).text
    assert html.count('title="Field_Guide_to_Floral_Dragons"') == 1


def test_reference_manuals_api_names_are_unique(client, dm_headers):
    data = client.get("/api/reference/manuals", headers=dm_headers).json()
    names = data["manuals"]
    assert data["count"] == len(names)
    dupes = {k: v for k, v in Counter(names).items() if v > 1}
    assert not dupes, f"duplicate manual names from the API: {dupes}"
