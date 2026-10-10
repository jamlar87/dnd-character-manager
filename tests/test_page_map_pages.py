"""Race and trait page maps must cite the page the books actually have.

The maps were hand-curated and drifted from the app's own race records: 14 races
shipped a page their own record disagreed with in the same book (Deep Gnome said
EEPC p.11 where the record and the book text say p.7, Shadar-kai MTF p.62 vs p.61),
and traits carried their own hand-written page, so a race row and its traits could
cite different books for the same race.

Where the record's page is contradicted by the book text it is NOT applied —
Vedalken's record claims GGR p.2, which is nowhere near the race — and that is
pinned here too, so a future regeneration cannot quietly "fix" it to page 2.
"""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RACES = json.loads((REPO / "data" / "page_maps" / "race_page_map.json").read_text())
TRAITS = json.loads((REPO / "data" / "page_maps" / "trait_page_map.json").read_text())


def _src(mapping, key):
    entry = mapping.get(key)
    return (entry.get("source_str") or "") if isinstance(entry, dict) else (entry or "")


REPAIRED = {
    "deep gnome": "EEPC p.7",          # was p.11 (record + text agree on p.7)
    "shadar-kai": "MTF p.61",          # was p.62
    "glasya tiefling": "MTF p.22",     # was p.21, like every bloodline
    "mephistopheles tiefling": "MTF p.23",
    "minotaur": "GGR p.20",            # was p.18
    "centaur": "GGR p.16",             # was p.15
    "goblin": "EGW p.175",             # the record's printing, text-backed
    "tortle": "MPMM p.34",
    "duergar": "MTF p.83",
}
KEPT_BECAUSE_TEXT_DISAGREES = {
    "vedalken": "GGR p.21",            # record claims p.2; nothing there names it
    "triton": "VGM p.115",             # record claims MTF p.8; not there either
}


def test_repaired_race_pages():
    wrong = {k: (_src(RACES, k), want) for k, want in REPAIRED.items() if _src(RACES, k) != want}
    assert not wrong, f"page map drifted: {wrong}"


def test_contradicted_record_pages_are_not_applied():
    wrong = {k: (_src(RACES, k), want)
             for k, want in KEPT_BECAUSE_TEXT_DISAGREES.items() if _src(RACES, k) != want}
    assert not wrong, f"the map took a page the book text contradicts: {wrong}"


def test_the_monster_race_keeps_its_entry_and_its_real_page():
    """Tlincalli is a playable race in main.RICH_RACE_DESCS; VGM p.113 is Yuan-ti."""
    assert _src(RACES, "tlincalli") == "VGM p.194"


def test_traits_cite_the_same_source_as_their_race():
    for trait, race in (("Duergar Resilience", "duergar"), ("Duergar Magic", "duergar"),
                        ("Necrotic Resistance", "shadar-kai"),
                        ("Sea Elf Training", "sea elf")):
        assert _src(TRAITS, trait) == _src(RACES, race), (
            f"{trait} ({_src(TRAITS, trait)}) disagrees with its race row ({_src(RACES, race)})")


def test_hand_curated_trait_pages_are_gone():
    """These all cited a page the race was never on."""
    assert _src(TRAITS, "Duergar Resilience") != "SCAG p.20"
    assert _src(TRAITS, "Silent Speech") == "SCAG p.110"
