"""Regression tests for "Unknown Source" attributions in the DM-tools reference data.

The reported bug: NPCs in the DM tools list rendered a badge reading
"📚 (Unknown Source, p.222)", and clicking it alerted "Could not find the source book".

Root cause pinned here: such a record had no `_source_manual` slug, so
data_loader._normalize_manual_source() had nothing to rebuild the book from, and the
placeholder's shape passed the source validator unnoticed.
"""
import json
import re
from pathlib import Path

import pytest

from services.sources import clean_source_display, is_placeholder_source

REPO = Path(__file__).resolve().parent.parent
MANUAL = REPO / "data" / "manual_data"

# Records left as-is on purpose: no book can be evidenced for them, and the loader
# replaces the displayed source anyway. Anything else is a regression.
KNOWN_GAPS = {
    ("races.json", "Xvart"),
    ("subclasses.json", "Tempest Domain"),
    ("subclasses.json", "Trickery Domain"),
}

# Every phrasing of "we never worked out the book" that has appeared in the data.
PLACEHOLDERS = [
    "(Unknown Source, p.222)",
    "(Unknown source)",
    "Unknown sourcebook p.15",
    "Unknown source (page not determinable)",
    "(Unknown, p.55)",
    "(N/A)",
    "(TBD)",
]

REAL_SOURCES = [
    "(Tomb of Annihilation, p.55)",
    "(Waterdeep: Dragon Heist, p.218)",
    "(Call of the Netherdeep)",
    "(The Wild Sheep Chase, p.2)",
    "PHB 2014 p.55",
    "SRD p.1",
]


@pytest.mark.parametrize("src", PLACEHOLDERS)
def test_placeholder_sources_are_recognised(src):
    assert is_placeholder_source(src) is True
    assert clean_source_display(src, "Manual") == "Manual"
    assert clean_source_display(src) == ""


@pytest.mark.parametrize("src", REAL_SOURCES)
def test_real_sources_pass_through_untouched(src):
    assert is_placeholder_source(src) is False
    assert clean_source_display(src, "Manual") == src


def test_empty_source_is_not_a_placeholder_but_still_takes_the_fallback():
    assert is_placeholder_source("") is False
    assert is_placeholder_source(None) is False
    assert clean_source_display("", "Manual") == "Manual"


def test_reported_npcs_carry_a_real_book():
    """The three names from the report, pinned to the evidence-backed attributions."""
    want = {
        "Finethir Shinebright": ("(The Wild Sheep Chase, p.2)", "WSC"),
        "Nar'I Xibrindas": ("(Waterdeep: Dragon Heist, p.21)", "WDH"),
        "Victoro Cassalanter": ("(Waterdeep: Dragon Heist, p.218)", "WDH"),
    }
    npcs = {n["name"]: n for n in json.loads((MANUAL / "npcs.json").read_text())}
    for name, (source, slug) in want.items():
        assert name in npcs, f"{name} vanished from npcs.json"
        assert npcs[name]["source"] == source
        assert npcs[name].get("_source_manual") == slug


def test_no_shipped_reference_record_carries_a_placeholder():
    """A new unresolved source must fail here, not reach a user's badge."""
    offenders = []
    for path in sorted(MANUAL.glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        records = doc if isinstance(doc, list) else list(doc.values())
        for rec in records:
            if isinstance(rec, dict) and is_placeholder_source(rec.get("source", "")):
                if (path.name, rec.get("name")) not in KNOWN_GAPS:
                    offenders.append(f"{path.name}: {rec.get('name')} -> {rec.get('source')!r}")
    assert offenders == [], "unresolved sources: " + "; ".join(offenders)


def test_page_bearing_npc_source_always_names_its_book():
    """The root cause, as an invariant: a page reference without a `_source_manual`
    slug is exactly the shape that could not be rebuilt into a book."""
    npcs = json.loads((MANUAL / "npcs.json").read_text())
    without_slug = [n["name"] for n in npcs
                    if re.search(r"p\.\s*\d+", str(n.get("source", ""))) and not n.get("_source_manual")]
    assert without_slug == []


def test_dm_payload_never_ships_a_placeholder():
    """Even if a bad record is reintroduced, the payload the badge renders is clean."""
    from routes.dm import _manual_npc_payload, _monster_card_payload

    bad = {"name": "Placeholder Test", "source": "(Unknown Source, p.9)"}
    assert _manual_npc_payload(bad)["src"] == ""
    assert _monster_card_payload(bad)["src"] == ""


def test_generated_dm_library_asset_has_no_placeholder():
    """The asset is written lazily on the first /dm-tools render."""
    asset = REPO / "static" / "dm-library.js"
    if not asset.exists():
        pytest.skip("dm-library.js not generated yet (first DM-tools render)")
    assert "Unknown Source" not in asset.read_text()
