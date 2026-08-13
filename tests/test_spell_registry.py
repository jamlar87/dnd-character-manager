"""Regression tests for the DM Tools spell registry integrity.

The Spells tab renders one card per SRD_SPELLS entry and opens a detail
modal via /api/dm/spell/{index}. Every spell MUST carry a unique index,
an SRD-shaped desc list, and resolvable detail. Manual spells.json entries
previously lacked index/desc/higher_level/component normalization, which
rendered them as showSpell('None') cards (modal 404).
"""

import json

from services.data_loader import _normalize_manual_spell


def test_normalize_manual_spell_shapes_to_srd_format():
    spell = {
        "name": "Arms of Hadar",
        "level": 1,
        "school": "conjuration",
        "casting_time": "1 action",
        "components": "V, S, M (a drop of your blood)",
        "description": "Tendrils of dark energy erupt from you.",
        "higher_levels": "The damage increases by 1d6.",
    }
    out = _normalize_manual_spell(dict(spell))
    assert out["index"] == "arms-of-hadar"
    assert out["desc"] == ["Tendrils of dark energy erupt from you."]
    assert out["higher_level"] == ["The damage increases by 1d6."]
    assert out["components"] == ["V", "S", "M"]
    assert out["material"] == "a drop of your blood"


def test_normalize_manual_spell_index_slug_rules():
    cases = {
        "Arcanist's Magic Aura": "arcanists-magic-aura",
        "Heroes' Feast": "heroes-feast",
        "Antipathy/Sympathy": "antipathy-sympathy",
        "Melf’s Minute Meteors": "melfs-minute-meteors",
    }
    for name, expected in cases.items():
        out = _normalize_manual_spell({"name": name})
        assert out["index"] == expected


def test_normalize_manual_spell_keeps_existing_index():
    out = _normalize_manual_spell({"name": "Fireball", "index": "fireball"})
    assert out["index"] == "fireball"


def test_merged_registry_has_unique_resolvable_indices():
    """Every spell in the merged SRD_SPELLS registry must have a unique index.

    Mirrors the DM Tools spell card contract: card onclick uses s.index and
    /api/dm/spell/{index} looks entries up by it.
    """
    import main  # triggers full data load (SRD cache + manual merge)
    spells = main.SRD_SPELLS
    assert len(spells) >= 600  # sanity: nothing silently dropped
    indices = [s.get("index") for s in spells]
    assert all(indices), f"spells missing index: {[s['name'] for s in spells if not s.get('index')][:5]}"
    assert len(set(indices)) == len(indices), "duplicate spell indices found"
    # every spell must have a resolvable description (modal requires it)
    missing_desc = [s["name"] for s in spells if not s.get("desc") and not s.get("description")]
    assert not missing_desc, f"spells missing desc: {missing_desc[:5]}"
    # components must be a list (modal joins components_raw)
    str_comps = [s["name"] for s in spells if isinstance(s.get("components"), str)]
    assert not str_comps, f"spells with string components: {str_comps[:5]}"
