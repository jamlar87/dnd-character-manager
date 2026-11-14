"""Import surface for the data loaders, plus the startup manual-data merge.

Every caller keeps importing from services.data_loader. The implementation sits
in services/loaders/*, one module per responsibility:

    files        disk JSON reads + cache
    sources      normalisation / enrichment rules
    races        manual race + subrace merge
    spells       manual spell merge
    indexes      manual magic items -> SRD_MAGIC_ITEMS + ITEM_INDEX
    feats        manual feat merge
    backgrounds  manual background merge
    subclasses   manual subclass + feature merge
    traps        manual traps
    limited_use  limited-use registry fill-in

This module itself holds only the re-exports, the manual source-page table
(tests/test_app_page_table_matches_pdfs.py reads `max_pages` from this file) and
load_manual_data(), the order the merges run in.
"""

from __future__ import annotations

import re
import time as _time

from services.loaders.backgrounds import merge_manual_backgrounds
from services.loaders.feats import merge_manual_feats
from services.loaders.files import (
    _load_json_cache,
    _load_manual_json,
    _load_srd_class_data,
    _load_spell_dice,
    cache_info,
    clear_cache,
    load_json,
)
from services.loaders.indexes import merge_manual_items
from services.loaders.limited_use import register_limited_use
from services.loaders.races import merge_manual_races
from services.loaders.sources import (
    _count_keywords,
    _existing_subclass_name,
    _normalize_item_rarity,
    _normalize_manual_source,
    _normalize_manual_spell,
    _normalize_recharge,
    _should_replace_description,
    _subclass_name_key,
)
from services.loaders.spells import merge_manual_spells
from services.loaders.subclasses import merge_manual_subclasses
from services.loaders.traps import merge_manual_traps
from services.sources import is_placeholder_source

_SOURCE_VALID_CLEAN = re.compile(r'^\([A-Za-z][^)]+\)$')


_SOURCE_VALID_PAGE = re.compile(r'^\(([^,]+),\s*p\.(\d+)\)$')




def _validate_manual_sources() -> None:
    """Check all manual data files for clean source format at startup.
    Logs warnings for bad formats, out-of-range pages, or slug mismatches.
    This does not fix — it only warns so the operator can investigate."""
    from core.config import DATA_DIR
    from core.data import _get_source_slug_map
    import json
    
    data_dir = DATA_DIR / "manual_data"
    files = ["races.json", "spells.json", "magic_items.json", "equipment.json",
             "monsters.json", "npcs.json", "feats.json", "backgrounds.json", "subclasses.json",
             "traps.json"]
    
    # PDF page ranges (compact)
    max_pages = {
        "MM": 354, "DMG": 320, "PHB": 322, "XGE": 195, "MTF": 258, "VGM": 226,
        "GGR": 258, "EGW": 307, "COTN": 226, "TCSR": 283, "TOA": 260, "LMoP": 64,
        "WDH": 228, "HOTDQ": 97, "ROT": 98, "TTP": 28, "SCAG": 161, "TCE": 256,
        "CC": 426, "EBT": 257, "CSF": 151, "TMFRV": 206, "MPG": 64, "TFS": 196,
        "SME": 22, "SOM": 100, "WRKF": 70, "WLL": 180, "W2": 30, "W5": 32,
        "W7": 38, "EIA": 37, "TLT": 32, "SDQ": 26, "DD": 25, "RRG": 144,
        "ERIA": 144, "AIPG": 256, "BLRG": 160, "LMRG": 160, "EREA": 200,
        "RVR": 160, "WLA": 200, "MWC": 200, "KW": 100, "DPM1": 100, "AW": 50,
        "W": 30, "W1": 30, "W3": 34, "W4": 32, "W6": 32, "MOM": 15, "WSC": 50,
        "WS": 9, "W8": 7, "W9": 13, "LMG": 256, "RAT": 50, "RGEO": 100,
        "ETR": 50, "DDP": 50, "SSK": 50, "DPM": 100, "BGDIA": 256, "GOS": 256,
    }
    
    # Slug → display (use existing slug map from the app)
    slug_map = _get_source_slug_map()
    
    warnings = []
    for fn in files:
        path = data_dir / fn
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        items = list(data.values()) if isinstance(data, dict) else data
        for entry in items:
            name = entry.get("name", "?")
            src = entry.get("source", "")
            slug = (entry.get("_source_manual") or "").strip()
            if not src:
                continue
            if is_placeholder_source(src):
                warnings.append(f"  ⚠ {fn}: {name} — placeholder source {src[:60]!r}: the book "
                                "was never determined, so nothing upstream can resolve it")
                continue
            if not _SOURCE_VALID_CLEAN.match(src):
                warnings.append(f"  ⚠ {fn}: {name} — bad format: '{src[:80]}'")
                continue
            m = _SOURCE_VALID_PAGE.match(src)
            if m:
                page = int(m.group(2))
                display = m.group(1)
                max_p = max_pages.get(slug.upper())
                if max_p and page > max_p:
                    warnings.append(f"  ⚠ {fn}: {name} — page {page} > {slug} max {max_p}")
                if page <= 0:
                    warnings.append(f"  ⚠ {fn}: {name} — page {page} (≤0)")
                # Check slug→display match
                slug_info = slug_map.get(slug.upper()) or slug_map.get(slug)
                if slug_info:
                    expected = slug_info.get("display", "")
                    if expected and display != expected:
                        warnings.append(f"  ⚠ {fn}: {name} — display '{display}' ≠ expected '{expected}'")
    
    if warnings:
        print(f"[source_validation] {len(warnings)} warning(s):")
        for w in warnings:
            print(w)
    else:
        print("[source_validation] All sources clean ✅")

def load_manual_data():
    """Merge extracted manual data into runtime structures. Called at startup."""
    _loader_start = _time.time()

    meta = _load_manual_json("_meta.json")
    if not meta or isinstance(meta, list):
        return  # No manual data yet

    print(f"[manual_data] Loading from {meta.get('source_manuals', [])}")

    # ── Startup source validation ────────────────────────────────────────
    _validate_manual_sources()
    # ──────────────────────────────────────────────────────────────────────

    merge_manual_races(meta)
    merge_manual_spells()
    merge_manual_items(meta)
    merge_manual_feats()
    merge_manual_backgrounds()
    merge_manual_subclasses(meta)
    merge_manual_traps()

    print(f"  Manual data loaded: {meta.get('totals', {})}")
    print(f"  [timing] load_manual_data: {(_time.time() - _loader_start) * 1000:.0f}ms")

    register_limited_use()


__all__ = [
    "load_json", "clear_cache", "cache_info",
    "_load_srd_class_data", "_load_spell_dice", "_load_json_cache",
    "_count_keywords", "_should_replace_description", "_load_manual_json",
    "_normalize_manual_source", "_validate_manual_sources", "_normalize_recharge",
    "_normalize_item_rarity", "_normalize_manual_spell",
    "_subclass_name_key", "_existing_subclass_name",
    "load_manual_data",
    "merge_manual_races", "merge_manual_spells", "merge_manual_items",
    "merge_manual_feats", "merge_manual_backgrounds", "merge_manual_subclasses",
    "merge_manual_traps", "register_limited_use",
]
