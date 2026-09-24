"""Every record's `_source_manual` must name a book the app can open.

A magic item cited "(Baldur's Gate: Descent into Avernus)" in a library that holds no such PDF.
Nothing checked the slug, and the page map overrode the displayed source, so the record and the map
disagreed underneath and the app looked right. The existing source tests cover exported races and
the *displayed* text; records whose slug is not an openable book slipped between them.

Two slugs are deliberately stale and allowed: WS (Shadows Envy, which the app holds as WSE) and TLT
(The Tortured Land, held as TTLT). Left flagged on purpose — their cited page cannot be placed
without guessing.
"""
from __future__ import annotations

import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent.parent
DATA = HERE / "data" / "manual_data"

FILES = [
    "races.json", "spells.json", "magic_items.json", "equipment.json", "monsters.json",
    "npcs.json", "feats.json", "backgrounds.json", "subclasses.json", "traps.json",
]

# Not library books: hand-made content and the SRD, which have no PDF to open.
NON_BOOK = {"", "Homebrew", "SRD"}
KNOWN_STALE = {"WS", "TLT"}


def _slugs():
    for filename in FILES:
        path = DATA / filename
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        items = list(data.values()) if isinstance(data, dict) else data
        for entry in items:
            if isinstance(entry, dict):
                yield filename, str(entry.get("name") or "?"), (entry.get("_source_manual") or "").strip()


def test_every_source_slug_is_a_library_book():
    pdf_map = set(json.loads((DATA / "_meta.json").read_text()).get("pdf_map", {}))
    assert pdf_map, "pdf_map is empty — the meta file did not load"
    offenders = [
        (fn, name, slug)
        for fn, name, slug in _slugs()
        if slug and slug not in pdf_map and slug not in NON_BOOK and slug not in KNOWN_STALE
    ]
    assert not offenders, f"records cite books the app cannot open: {offenders[:6]}"


def test_the_stale_slug_allowance_is_not_widened_by_accident():
    """The allowance is a deliberate, user-confirmed exception — not a place to park new failures."""
    assert KNOWN_STALE == {"WS", "TLT"}, (
        "the stale-slug allowance changed; widen it only with a decision, never to silence a failure"
    )
