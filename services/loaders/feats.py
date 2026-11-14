"""Manual feats -> FEATS / FEAT_BY_NAME, with page sources from the page map."""

from __future__ import annotations

import json

from services.loaders.files import _load_manual_json

__all__ = ["merge_manual_feats"]


def merge_manual_feats() -> None:
    """Merge feats.json into the feat registry."""
    from core.config import PAGE_MAP_DIR
    from core.data import FEATS, FEAT_BY_NAME

    # ── Feats ── merge into FEATS dict
    manual_feats = _load_manual_json("feats.json")
    _feat_name_to_key = {f["name"].lower(): k for k, f in FEATS.items()}
    for feat in manual_feats:
        name = feat.get("name", "")
        if not name:
            continue
        key = _feat_name_to_key.get(name.lower(), name)
        manual_desc = feat.get("description", "")
        if key not in FEATS:
            FEATS[key] = {
                "name": name,
                "prerequisite": feat.get("prerequisite", ""),
                "description": manual_desc,
                "source": feat.get("source", ""),
            }
            # Also register in FEAT_BY_NAME so feat picker finds it
            _lkey = name.lower()
            if _lkey not in FEAT_BY_NAME:
                FEAT_BY_NAME[_lkey] = FEATS[key]
        elif manual_desc and len(manual_desc) > len(FEATS[key].get("description", "")):
            # Skip OCR-garbled descriptions — never replace clean text with garbage
            _ocr_markers = [
                "Vou ", "vou ", "Vour ", "lhe ", "lhal ", "lhis ", "lhan ",
                "lhrough ", "lhrow ", "lurn ", "lhe ", "crealure", "dalllage",
                "aclion", "effecl", "reaclion", "benelils", "benelit",
                "proleclion", "rnaximum", "rnake", "RolI", "Whcn",
                "beeornes", "discordam", "fillthe", "notjust", "bdore",
                "levei ", "leveI ", "proliciency", "proticiency",
                "olheI'", "ralheI'", "wilhin", "PART I CUSTOMIZ",
                "maslered", "disadvanlage", "prolicienl",
            ]
            if any(m in manual_desc for m in _ocr_markers):
                continue
            # Update existing feat with full PHB description (manual data is richer)
            FEATS[key]["description"] = manual_desc
            FEATS[key]["desc"] = manual_desc
            if not FEATS[key].get("prerequisite") and feat.get("prerequisite"):
                FEATS[key]["prerequisite"] = feat["prerequisite"]
    if manual_feats:
        print(f"  + Feats: {len(manual_feats)}")

    # ── Enrich feat sources with page numbers ──
    _feat_page_map: dict[str, str] = {}
    try:
        _fpm_path = PAGE_MAP_DIR / "feat_page_map.json"
        if _fpm_path.exists():
            with open(_fpm_path) as _f:
                _feat_page_map = json.load(_f)
        _feat_enriched = 0
        for _key, _feat in FEATS.items():
            _mapped = _feat_page_map.get(_key.lower())
            if not _mapped:
                # Also try by name
                _fname = _feat.get("name", "").lower().replace(" ", "_")
                _mapped = _feat_page_map.get(_fname)
            if _mapped and "p." in _mapped:
                _feat["source"] = _mapped
                _feat_enriched += 1
        if _feat_enriched:
            print(f"  Feat sources enriched: {_feat_enriched}/{len(FEATS)}")
    except Exception as _e:
        print(f"  (feat page map unavailable: {_e})")
