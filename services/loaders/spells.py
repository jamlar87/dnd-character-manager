"""Manual spells -> SRD_SPELLS (normalised classes/schools + page sources)."""

from __future__ import annotations

import json

from services.loaders.files import _load_manual_json
from services.loaders.sources import _normalize_manual_spell

__all__ = ["merge_manual_spells"]


def merge_manual_spells() -> None:
    """Append spells.json to the spell registry."""
    from core.config import DATA_DIR
    from core.data import SRD_SPELLS, _spell_page_map

    # ── Spells ── append to SRD_SPELLS (normalize classes/school to dict format)
    manual_spells = _load_manual_json("spells.json")
    # Backfill missing classes/schools from reference map
    _scm_path = DATA_DIR / "spell_classes_map.json"
    if _scm_path.exists():
        with open(_scm_path) as _f:
            _spell_classes = json.load(_f)
        _scm_fixed = 0
        _scm_enhanced = 0
        for _s in manual_spells:
            _name = _s.get("name", "")
            _lookup = _name.replace('\u2019', "'").replace('\u2018', "'").lower()
            if _lookup in _spell_classes:
                _mapped = _spell_classes[_lookup]
                _existing = _s.get("classes") or []
                if not _existing:
                    _s["classes"] = _mapped
                    _scm_fixed += 1
                elif len(_mapped) > len(_existing):
                    _s["classes"] = _mapped
                    _scm_enhanced += 1
        if _scm_fixed:
            print(f"  Spell classes backfilled: {_scm_fixed}")
        if _scm_enhanced:
            print(f"  Spell classes enhanced: {_scm_enhanced}")
    existing_spell_names = {s.get("name", "").replace('\u2019', "'").replace('\u2018', "'").lower() for s in SRD_SPELLS}
    # Track which index in SRD_SPELLS corresponds to each normalized name (for replacement)
    _spell_index: dict[str, int] = {}
    for _i, _s in enumerate(SRD_SPELLS):
        _n = _s.get("name", "").replace('\u2019', "'").replace('\u2018', "'").lower()
        if _n not in _spell_index:
            _spell_index[_n] = _i
    for spell in manual_spells:
        _normalize_manual_spell(spell)
        norm_name = spell.get("name", "").replace('\u2019', "'").replace('\u2018', "'")
        key = norm_name.lower()
        if key not in existing_spell_names:
            # Normalize classes: strings → {name: ...} dicts matching SRD format
            raw_classes = spell.get("classes", [])
            if raw_classes and isinstance(raw_classes[0], str):
                spell["classes"] = [{"name": c, "index": c.lower().replace(" ", "-")} for c in raw_classes]
            # Normalize school: string → {name: ...} dict
            school = spell.get("school")
            if isinstance(school, str):
                spell["school"] = {"name": school.title()}
            SRD_SPELLS.append(spell)
            existing_spell_names.add(key)
            _spell_index[key] = len(SRD_SPELLS) - 1
        elif spell.get("components") and key in _spell_index:
            # Replace existing entry if it lacks components but this one has them
            _existing = SRD_SPELLS[_spell_index[key]]
            if not _existing.get("components"):
                SRD_SPELLS[_spell_index[key]] = spell
                # Re-normalize classes/school for the replacement
                raw_classes = spell.get("classes", [])
                if raw_classes and isinstance(raw_classes[0], str):
                    spell["classes"] = [{"name": c, "index": c.lower().replace(" ", "-")} for c in raw_classes]
                school = spell.get("school")
                if isinstance(school, str):
                    spell["school"] = {"name": school.title()}
    if manual_spells:
        print(f"  + Spells: {len(manual_spells)}")

    # Also apply spell_classes_map to SRD spells (not just manual ones)
    _scm_path2 = DATA_DIR / "spell_classes_map.json"
    if _scm_path2.exists():
        with open(_scm_path2) as _f:
            _scm_data = json.load(_f)
        _scm_patched = 0
        for _i, _s in enumerate(SRD_SPELLS):
            _n = _s.get("name", "").replace('\u2019', "'").replace('\u2018', "'").lower()
            if _n in _scm_data:
                _mapped = _scm_data[_n]
                _existing_classes = [c.get("name", "") for c in (_s.get("classes") or [])]
                _missing = [c for c in _mapped if c not in _existing_classes]
                if _missing:
                    _s["classes"] = (_s.get("classes") or []) + [{"name": c, "index": c.lower().replace(" ", "-")} for c in _missing]
                    _scm_patched += 1
        if _scm_patched:
            print(f"  Spell classes enriched (SRD): {_scm_patched}")

    # Dedup SRD_SPELLS: smart-quote duplicates (e.g. Aganazzar\u2019s vs Aganazzar's)
    _seen_names: dict[str, int] = {}  # normalized_name -> index of best entry
    _to_remove: list[int] = []
    for _i, _s in enumerate(SRD_SPELLS):
        _n = _s.get("name", "").replace('\u2019', "'").replace('\u2018', "'").lower()
        if _n in _seen_names:
            _existing = SRD_SPELLS[_seen_names[_n]]
            # Keep the one with components, or the one with more classes
            if not _s.get("components") and _existing.get("components"):
                _to_remove.append(_i)
            elif not _existing.get("components") and _s.get("components"):
                _to_remove.append(_seen_names[_n])
                _seen_names[_n] = _i
            else:
                _to_remove.append(_i)  # duplicate, remove later one
        else:
            _seen_names[_n] = _i
    for _i in reversed(_to_remove):
        SRD_SPELLS.pop(_i)
    if _to_remove:
        print(f"  Spell duplicates removed: {len(_to_remove)}")

    # Re-apply spell page map to catch newly-added manual spells
    _spm_enriched2 = 0
    for _spell in SRD_SPELLS:
        if "p." not in (_spell.get("source") or ""):
            _name = _spell.get("name", "").lower()
            _mapped = _spell_page_map.get(_name)
            if _mapped:
                _spell["source"] = _mapped
                _spm_enriched2 += 1
    if _spm_enriched2:
        print(f"  Spell sources enriched (manual): {_spm_enriched2}")
