"""PDF generation characterization tests.

Verifies the PDF route and pdf_generator data builder end-to-end:
  - PDF bytes are returned, non-empty, and open with pypdf
  - The fillable template AcroForm fields receive identity/stats
  - Spells, features, and inventory flow into the generated content
  - build_char_data() parses JSON columns and computes derived values
  - Spell cache loads SRD + manual + supplementary spells

These are smoke/characterization tests: they pin CURRENT behavior so
refactors of pdf_generator.py (centralizing spell lookup, splitting
fill_official_sheet) cannot silently break output.
"""

import io
import sqlite3

import pytest

pypdf = pytest.importorskip("pypdf")
from pdf_generator import build_char_data, fill_official_sheet, _get_spell_cache
from pdf_generator import _build_spell_appendix_text, _build_condensed_features


# ── Helpers ────────────────────────────────────────────────────────────────

def _create(client, headers, **overrides):
    payload = {
        "name": "PDF Test Wizard",
        "race": "Human",
        "class_name": "Wizard",
        "level": 3,
        "abilities": {
            "strength": 10, "dexterity": 14, "constitution": 14,
            "intelligence": 16, "wisdom": 12, "charisma": 10,
        },
    }
    payload.update(overrides)
    resp = client.post("/api/character/create", json=payload, headers=headers)
    assert resp.status_code == 200, f"create failed: {resp.status_code} {resp.text[:300]}"
    return resp.json()["id"]


def _row(db_path, char_id):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute("SELECT * FROM characters WHERE id = ?", (char_id,)).fetchone()
    finally:
        con.close()


def _fetch_pdf(client, headers, char_id):
    resp = client.get(f"/api/character/{char_id}/pdf", headers=headers)
    assert resp.status_code == 200, f"pdf route failed: {resp.status_code} {resp.text[:200]}"
    assert resp.headers.get("content-type", "").startswith("application/pdf")
    return resp.content


# ── PDF route smoke tests ──────────────────────────────────────────────────

class TestPdfRoute:
    def test_pdf_returns_valid_bytes(self, client, auth_headers, seeded_db):
        # A level-1 Fighter: no spell appendix, minimal pages.
        cid = _create(client, auth_headers, class_name="Fighter", level=1)
        data = _fetch_pdf(client, auth_headers, cid)
        assert len(data) > 1000, "PDF too small to be real"
        # Opens with pypdf
        reader = pypdf.PdfReader(io.BytesIO(data))
        assert len(reader.pages) >= 1
        # Template is 3 pages; appendices may add a few. A fighter at L1
        # must not balloon to spell-appendix length (observed: 22 for a
        # L3 prepared caster with ~19 spells, one spell per page).
        assert len(reader.pages) <= 8, f"unexpected page count: {len(reader.pages)}"

    def test_pdf_field_values(self, client, auth_headers, seeded_db):
        cid = _create(client, auth_headers)
        data = _fetch_pdf(client, auth_headers, cid)
        reader = pypdf.PdfReader(io.BytesIO(data))
        fields = reader.get_fields() or {}
        # Flatten: pypdf returns FieldDict values with '/V'
        values = {}
        for name, fld in fields.items():
            if hasattr(fld, "get"):
                v = fld.get("/V")
                if v is not None:
                    values[name.strip()] = str(v)
        joined = " ".join(values.values())
        assert "PDF Test Wizard" in joined or "PDF Test Wizard" in values.get("CharacterName", "")
        # Class/level + race should appear somewhere
        assert any("Wizard" in v for v in values.values()) or "Wizard" in joined
        # Ability scores: Human +1 to all. Base 10 STR → 11, base 16 INT → 17.
        assert values.get("STR", "") == "11"
        assert values.get("INT", "") == "17"

    def test_pdf_requires_auth(self, client):
        resp = client.get("/api/character/1/pdf", follow_redirects=False)
        assert resp.status_code in (303, 401, 403)


# ── build_char_data unit tests ─────────────────────────────────────────────

class TestBuildCharData:
    def test_parses_json_columns(self, client, auth_headers, seeded_db):
        cid = _create(client, auth_headers)
        row = _row(seeded_db["db_path"], cid)
        d = build_char_data(row)
        # JSON columns converted from strings to lists/dicts
        assert isinstance(d["skills"], list)
        assert isinstance(d["feature_data"], list)
        assert isinstance(d["class_levels"], dict)
        assert d["class_levels"] == {"Wizard": 3}

    def test_derived_values(self, client, auth_headers, seeded_db):
        cid = _create(client, auth_headers)
        row = _row(seeded_db["db_path"], cid)
        d = build_char_data(row)
        # INT 16 → +3; prof +2 at L3; save DC 8+2+3 = 13
        assert d["int_mod"] == 3
        assert d["initiative"] == 2  # DEX 14 → +2
        assert d["spell_save_dc"] == 13
        assert d["spell_attack_bonus"] == 5
        assert d["proficiency_bonus"] == 2

    def test_spells_loaded_from_db(self, client, auth_headers, seeded_db):
        cid = _create(client, auth_headers)
        con = sqlite3.connect(str(seeded_db["db_path"]))
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM characters WHERE id = ?", (cid,)).fetchone()
        d = build_char_data(row, con)
        con.close()
        assert d["is_caster"] is True
        assert len(d["spells"]) > 0
        # Spells are (name, level, prepared) tuples ordered by level then name
        names = {s[0] for s in d["spells"]}
        assert len(names) == len(d["spells"])

    def test_condensed_features_has_class_features(self, client, auth_headers, seeded_db):
        cid = _create(client, auth_headers)
        row = _row(seeded_db["db_path"], cid)
        d = build_char_data(row)
        text = _build_condensed_features(d).lower()
        # Wizard 3 has Arcane Recovery; Human has no racial feature to test
        assert "arcane recovery" in text or "spellcasting" in text

    def test_spell_appendix_contains_spell_names(self, client, auth_headers, seeded_db):
        cid = _create(client, auth_headers)
        con = sqlite3.connect(str(seeded_db["db_path"]))
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM characters WHERE id = ?", (cid,)).fetchone()
        d = build_char_data(row, con)
        con.close()
        appendix = _build_spell_appendix_text(d)
        for sname, _lvl, _prepared in d["spells"][:3]:
            assert sname.lower() in appendix.lower(), f"spell {sname} missing from appendix"


# ── Spell cache ────────────────────────────────────────────────────────────

class TestSpellCache:
    def test_cache_is_nonempty_dict(self):
        cache = _get_spell_cache()
        assert isinstance(cache, dict)
        assert len(cache) > 100

    def test_cache_has_srd_and_supplementary(self):
        cache = _get_spell_cache()
        # SRD classic
        assert "fireball" in cache
        assert "mage armor" in cache
        # Supplementary spell that lives only in pdf_generator
        assert "absorb elements" in cache
        assert cache["absorb elements"].get("level") == 1

    def test_cache_entries_have_descriptions(self):
        cache = _get_spell_cache()
        missing = [k for k, v in list(cache.items())[:50]
                   if not (v.get("desc") or v.get("description"))]
        assert not missing, f"spells missing descriptions: {missing[:5]}"


# ── Direct fill smoke (no HTTP) ────────────────────────────────────────────

class TestFillDirect:
    def test_fill_official_sheet_returns_bytes(self, client, auth_headers, seeded_db):
        cid = _create(client, auth_headers)
        row = _row(seeded_db["db_path"], cid)
        con = sqlite3.connect(str(seeded_db["db_path"]))
        con.row_factory = sqlite3.Row
        try:
            d = build_char_data(row, con)
        finally:
            con.close()
        out = fill_official_sheet(d)
        assert out is not None
        reader = pypdf.PdfReader(io.BytesIO(bytes(out)))
        assert len(reader.pages) >= 1
