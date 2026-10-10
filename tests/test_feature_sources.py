"""Class/subclass feature provenance in the nav search's internal index.

Feature rows used to render with no source badge at all: unlike races, spells,
traits and items, features had no page map (there is now a generated
`data/page_maps/feature_page_map.json`), and the manual merge dropped the record's
source on the floor when it wrote the description text (there is now
`data.FEATURE_SOURCES`).

Two defects are pinned here:

* 1,037 rows named "Subclass::feature" — internal disambiguation keys from the
  merge — were listed as if they were entities, each a duplicate of a plain row;
* every feature row was sourceless, so the detail panel showed no book and the
  "filter by manual" search had nothing to match.
"""

import json
from pathlib import Path

from routes.characters.helpers import slug_for_source
from services import entity_search as es

REPO = Path(__file__).resolve().parent.parent
MAP_PATH = REPO / "data" / "page_maps" / "feature_page_map.json"


def _feature_rows():
    return [r for r in es._get_index() if r["kind"] == "feature"]


def test_internal_disambiguation_keys_are_not_entities():
    """`Subclass::feature` is a lookup key for the merge, not something to list."""
    offenders = [r["name"] for r in _feature_rows() if "::" in r["name"]]
    assert not offenders, f"{len(offenders)} internal keys leaked into the index: {offenders[:5]}"


def test_feature_rows_carry_book_and_page():
    rows = {r["name_lower"]: r for r in _feature_rows()}
    expected = {
        "frenzy": ("PHB 2014", 49),            # Barbarian: Path of the Berserker
        "action surge": ("PHB 2014", 70),      # Fighter class feature
        "wild shape": ("PHB 2014", 64),        # Druid class feature
        "brave": ("PHB 2014", 26),             # Halfling racial trait
        "hex warrior": (None, 55),             # Xanathar's Guide, Hexblade
    }
    for name, (book, page) in expected.items():
        row = rows.get(name)
        assert row is not None, f"{name!r} missing from the index"
        assert row["source"], f"{name!r} has no source"
        assert row["slug"], f"{name!r} source {row['source']!r} resolved to no book slug"
        assert row["page"] == page, f"{name!r} page {row['page']} != {page}"
        if book:
            assert row["slug"] == slug_for_source(book), f"{name!r} -> {row['slug']}"


def test_page_map_values_all_resolve_to_a_book():
    """A generated map with an unusable display string would give unlinked badges."""
    mapping = json.loads(MAP_PATH.read_text())
    assert len(mapping) > 900, f"page map shrank to {len(mapping)} entries"
    unresolvable = {k: v for k, v in mapping.items() if not slug_for_source(v)}
    assert not unresolvable, f"{len(unresolvable)} map values name no known book: {list(unresolvable.items())[:5]}"


def test_sourced_feature_rows_all_have_a_slug():
    misplaced = [r["name"] for r in _feature_rows() if (r["source"] or "").strip() and not r["slug"]]
    assert not misplaced, f"{len(misplaced)} rows show a source but no book: {misplaced[:5]}"


def test_manual_merge_records_feature_provenance():
    """The merge writes the record's source alongside the description it writes."""
    import data as app_data
    recorded = getattr(app_data, "FEATURE_SOURCES", {})
    assert len(recorded) > 500, f"only {len(recorded)} features recorded a source"
    assert "hex warrior" in recorded, "Hexblade's feature did not record its source"
    assert slug_for_source(recorded["hex warrior"]) == "XGE"
