"""Limited-use registry fill-in: homebrew classes, then race and NPC catch-up.

The main merge only registers limited-use features it sees while walking the
SRD/manual records; this pass covers homebrew classes and records that were
already present (SRD races, subrace-migrated races, extracted NPCs).
"""

from __future__ import annotations

import re

from services.loaders.files import _load_manual_json

__all__ = ["register_limited_use"]


def register_limited_use() -> None:
    """Register limited-use features the main merge pass misses."""
    from core.data import LIMITED_USE

    # ── Register homebrew class limited-use features not in SRD ──
    _HOMEBREW_LIMITED_USE = {
        # Scholar: 1 Healing Die per level, short rest
        "hands of the healer": {"min": 1, "max": 99, "recharge": "short",
                                "class": "Scholar", "per": "level"},
    }
    for _hk, _hv in _HOMEBREW_LIMITED_USE.items():
        if _hk not in LIMITED_USE:
            LIMITED_USE[_hk] = _hv

    # ── Catch-up: register limited-use race traits that were missed by the main
    #    loop (SRD races already in RACES, subrace-migrated races, etc.) ──
    def _normalize_recharge_light(_r: str) -> str:
        _r = _r.lower().strip()
        if "short" in _r and "long" in _r:
            return "short"
        if "short" in _r:
            return "short"
        if "long" in _r:
            return "long"
        if "special" in _r:
            return "special"
        return _r
    for _race in _load_manual_json("races.json"):
        for _t in _race.get("traits", []):
            _tname = _t.get("name", "") if isinstance(_t, dict) else ""
            _tuses = _t.get("uses", 0) if isinstance(_t, dict) else 0
            _trech = _t.get("recharge", "") if isinstance(_t, dict) else ""
            if _tname and (_tuses or _trech):
                _key = _tname.lower()
                if _key not in LIMITED_USE:
                    LIMITED_USE[_key] = {
                        "min": _tuses, "max": _tuses,
                        "recharge": _normalize_recharge_light(_trech) if _trech else "long",
                        "class": "", "per": "fixed",
                    }
        for _sr in _race.get("subraces", []):
            for _st in _sr.get("traits", []):
                _stname = _st.get("name", "") if isinstance(_st, dict) else ""
                _stuses = _st.get("uses", 0) if isinstance(_st, dict) else 0
                _strech = _st.get("recharge", "") if isinstance(_st, dict) else ""
                if _stname and (_stuses or _strech):
                    _key = _stname.lower()
                    if _key not in LIMITED_USE:
                        LIMITED_USE[_key] = {
                            "min": _stuses, "max": _stuses,
                            "recharge": _normalize_recharge_light(_strech) if _strech else "long",
                            "class": "", "per": "fixed",
                        }

    # ── Catch-up: register NPC features with uses/recharge ──
    # First pass: structured uses/recharge fields from extraction
    # Second pass: scan feature descriptions for usage patterns
    _USAGE_PATTERNS = [
        (re.compile(r'(?:can be used|usable|use)\s*(?:\w+\s+)*?(\d+)\s*(?:times\s*)?per\s+(short|long)\s+rest', re.I), None),
        (re.compile(r'(\d+)\s*(?:times?\s*)?per\s+(short|long)\s+rest', re.I), None),
        (re.compile(r'(\d+)/(?:day|long rest)', re.I), None),
        (re.compile(r'(\d+)/(?:short rest|encounter)', re.I), 'short'),
        (re.compile(r'recharges?\s*(?:on|after|upon)?\s*(?:a\s+)?(\d)[-–]\s*(\d)', re.I), None),
        (re.compile(r'recharges?\s*(?:on|after|upon)?\s*a\s+(short|long)\s+rest', re.I), None),
        (re.compile(r'once per day', re.I), 'long'),
        (re.compile(r'once per short rest', re.I), 'short'),
        (re.compile(r'(\d+)\s*use', re.I), None),
    ]
    for _npc in _load_manual_json("npcs.json"):
        for _f in _npc.get("features", []):
            if not isinstance(_f, dict):
                continue
            _fn = _f.get("name", "")
            _fu = _f.get("uses", 0) or 0
            _fr = _f.get("recharge", "") or ""
            _desc = _f.get("description", "") or ""
            # First pass: explicit structured fields
            if _fn and (_fu or _fr):
                _key = _fn.lower()
                if _key not in LIMITED_USE:
                    _cls = _npc.get("class_name", "")
                    LIMITED_USE[_key] = {
                        "min": _fu, "max": _fu,
                        "recharge": _normalize_recharge_light(_fr) if _fr else "long",
                        "class": _cls, "per": "fixed",
                    }
            # Second pass: scan description for usage patterns
            if _fn and _desc and not _fu and not _fr:
                _key = _fn.lower()
                if _key in LIMITED_USE:
                    continue  # Already registered by structured fields
                _found_uses = 0
                _found_recharge = ""
                for _pat, _default_recharge in _USAGE_PATTERNS:
                    _m = _pat.search(_desc)
                    if _m:
                        if _default_recharge:
                            _found_recharge = _default_recharge
                            _found_uses = int(_m.group(1)) if _m.lastindex is not None and _m.lastindex >= 1 and _m.group(1).isdigit() else 1
                        elif len(_m.groups()) == 1:
                            _g1 = _m.group(1)
                            if _g1.lower() in ('short', 'long'):
                                _found_recharge = _g1.lower()
                                _found_uses = 1
                            elif _g1.isdigit():
                                _found_uses = int(_g1)
                                _found_recharge = "long"
                        elif len(_m.groups()) >= 2:
                            # Recharge range (e.g., "recharges on a 5-6")
                            _found_recharge = "short"
                            _found_uses = 1  # treat dice-recharge as 1 use per short
                        break
                if _found_uses and _found_recharge:
                    _cls = _npc.get("class_name", "")
                    LIMITED_USE[_key] = {
                        "min": _found_uses, "max": _found_uses,
                        "recharge": _found_recharge,
                        "class": _cls, "per": "fixed",
                    }
