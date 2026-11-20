"""Feat tables and feature enrichment: KNOWN_FEATS/FEAT_DETAILS built
from data/ at import, the defense map, and the helpers that fold class/subclass
feature text into what the sheet renders.

Split out of the single-module sheet.py; behaviour and URLs are unchanged.
"""

from core.data import FEATS
from data import RIDER_CARDS
from services.leveling import (
    FEATURE_DESCRIPTIONS,
    INVOCATION_LEVELS,
    SRD_FEATURES,
    get_uses_for_level,
)
from services.rules import ability_mod

from ._shared import _INVOCATION_NAMES


def _build_known_feats() -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    title_names = {f["name"].lower(): f["name"] for f in FEATS.values()}
    for f in FEATS.values():
        name = f["name"]
        if not name or name in _INVOCATION_NAMES:
            continue
        # Skip ALL-CAPS names that aren't proper title case
        # (OCR-garbled imports like "ATTACKER" for "Savage Attacker",
        #  or "MOBILE" when "Mobile" already exists as a key)
        if name.isupper():
            title_version = name.title()
            if title_version in title_names or title_version.lower() in title_names:
                continue
        if name.lower() not in seen:
            seen.add(name.lower())
            result.append(name)
    result.sort()
    return result
KNOWN_FEATS: list[str] = _build_known_feats()

# Feat name → {desc, prereq, source} for the ASI picker preview
FEAT_DETAILS: dict[str, dict] = {}
# Case-insensitive name → FEATS value for enrichment lookups
FEAT_BY_NAME: dict[str, dict] = {}
for _f in FEATS.values():
    _name = _f.get("name", "")
    if _name in KNOWN_FEATS:
        FEAT_DETAILS[_name] = {
            "desc": _f.get("description") or _f.get("desc", ""),
            "prereq": _f.get("prerequisite") or _f.get("prereq", ""),
            "source": _f.get("source", ""),
        }
    FEAT_BY_NAME[_name.lower()] = _f


# ── Feature → Defense Mappings (PHB 2014) ──
# Maps feature names to resistances/immunities they grant.
# Format: {"resist": [...], "immune": [...], "note": "while raging"}
FEATURE_DEFENSES = {
    "Rage": {"resist": ["Bludgeoning", "Piercing", "Slashing"], "note": "while raging"},
    "Totem Spirit: Bear": {"resist": ["All except Psychic"], "note": "while raging"},
    "Empty Body": {"resist": ["All except Force"], "note": "while invisible (4 ki, 1 min)"},
}


def _recalc_limited_uses(feature_data: list, class_name: str = "", level: int = 0,
                         mods: dict = None, class_levels: dict = None,
                         subclass: str = "") -> None:
    """Recompute uses_max/uses/recharge on already-enriched dict features.
    Needed for multiclass characters where stored feature_data was enriched
    before the class_levels fix — features like Action Surge end up with
    the wrong source class and no limited-use tracking."""
    from core.data import LIMITED_USE
    if not feature_data:
        return
    import re
    for feat in feature_data:
        if not isinstance(feat, dict):
            continue
        name = feat.get("name", "")
        key = name.lower()
        _strip_key = re.sub(r'\s*\([^)]*\)\s*$', '', key).strip()

        # Infer source class + level (same logic as enrich_features)
        source_class = None
        source_level = 0
        if class_levels and len(class_levels) > 1:
            for cls_name in class_levels:
                if cls_name.lower() in _strip_key or _strip_key in cls_name.lower():
                    source_class = cls_name
                    source_level = class_levels[cls_name]
                    break
            if not source_class:
                for lkey, lu in LIMITED_USE.items():
                    if lkey == _strip_key or lkey in _strip_key or _strip_key.startswith(lkey) or lkey.startswith(_strip_key):
                        lu_class = lu.get("class", "")
                        if lu_class and lu_class in class_levels:
                            source_class = lu_class
                            source_level = class_levels[lu_class]
                            break
        if not source_class:
            source_class = class_name
            source_level = class_levels.get(class_name, level) if class_levels else level

        # Compute limited-use values
        if source_class and source_level > 0:
            _NON_LIMITED = {"wild magic surge", "bend luck", "controlled chaos",
                "spell bombardment", "totem spirit", "aspect of the beast",
                "totemic attunement", "thunderbolt strike", "stormborn",
                "dragon wings", "awakened mind", "the third eye", "greater portent",
                "minor conjuration", "hypnotic gaze", "alter memories",
                "improved minor illusion", "illusory reality",
                "minor alchemy", "transmuter's stone",
                "mage hand legerdemain", "magical ambush", "versatile trickster"}
            if _strip_key not in _NON_LIMITED:
                _FEAT_ALIASES = {"font of magic": "sorcery points"}
                for lkey, lu in LIMITED_USE.items():
                    _match_key = _FEAT_ALIASES.get(_strip_key, _strip_key)
                    if lkey in _match_key or _match_key.startswith(lkey) or lkey.startswith(_match_key):
                        uses_max = get_uses_for_level(lkey, source_class, source_level)
                        if uses_max > 0:
                            if lkey == "divine sense":
                                cha_mod = (mods or {}).get("charisma", 0)
                                uses_max = max(1, uses_max + cha_mod)
                            if lkey == "cleansing touch":
                                cha_mod = (mods or {}).get("charisma", 0)
                                uses_max = max(1, uses_max + cha_mod - 1)
                            if lu.get("per") == "wis":
                                wis_mod = (mods or {}).get("wisdom", 0)
                                uses_max = max(1, wis_mod)
                            feat["uses_max"] = uses_max
                            feat["uses"] = uses_max
                            feat["recharge"] = lu["recharge"]
                            if lu.get("pool_kind"):
                                feat["pool_kind"] = lu["pool_kind"]
                        break


def _add_cd_sub_options(feature_data: list[dict]) -> None:
    """Mutate feature_data in-place: add sub_options to composite Channel Divinity entries
    that lack them. Safe to call on already-enriched data — no-ops if sub_options exist."""
    for feat in feature_data:
        name = feat.get("name", "")
        if "channel divinity" not in name.lower():
            continue
        if " | " not in name:
            continue
        if feat.get("sub_options"):
            continue  # Already enriched
        # Parse composite name into sub-options
        segments = name.split(" | ")
        sub_options = []
        for seg in segments:
            seg = seg.strip()
            sub_name = seg
            if ": " in seg:
                maybe_lvl, rest = seg.split(": ", 1)
                if maybe_lvl.startswith("L") and maybe_lvl[1:].replace("-", "").replace("+", "").isdigit():
                    sub_name = rest
            sub_key = sub_name.lower()
            sub_desc = FEATURE_DESCRIPTIONS.get(sub_key, "")
            sub_options.append({"name": sub_name, "description": sub_desc})
        feat["sub_options"] = sub_options
        # Update description to list available options
        option_names = [so["name"] for so in sub_options if "channel divinity:" in so["name"].lower()]
        if option_names:
            existing_desc = feat.get("description", "")
            if "Available options:" not in existing_desc:
                feat["description"] = f"{existing_desc}\n\nAvailable options: {', '.join(option_names)}."


def _add_source_to_features(feature_data: list[dict]) -> None:
    """Mutate feature_data in-place: add 'source' field from SRD_FEATURES lookup.
    Tries exact name match first, then strips class suffix for composite names.
    Safe to call on already-enriched data — no-ops if source already present.
    Skips 'SRD 5.1' and bare 'PHB 2014' (no page) — fallback handles those."""
    for feat in feature_data:
        src = feat.get("source", "")
        if src and src != "SRD 5.1" and src != "PHB 2014":
            continue  # Already has a real source
        name = feat.get("name", "")
        key = name.lower()
        # Try exact match
        _src = next((f.get("source", "") for f in SRD_FEATURES if f.get("name", "").lower() == key), "")
        # Try stripping class suffix: "Spellcasting: Cleric" → try base "Spellcasting"
        if not _src and ": " in name:
            base_name = name.split(": ", 1)[0].strip().lower()
            _src = next((f.get("source", "") for f in SRD_FEATURES if f.get("name", "").lower() == base_name), "")
        # For composite names with " | ", try each segment
        if not _src and " | " in name:
            for seg in name.split(" | "):
                seg = seg.strip()
                # Strip level prefix like "L2: "
                if ": " in seg:
                    maybe_lvl, rest = seg.split(": ", 1)
                    if maybe_lvl.startswith("L") and maybe_lvl[1:].replace("-","").replace("+","").isdigit():
                        seg = rest
                seg_key = seg.lower()
                _src = next((f.get("source", "") for f in SRD_FEATURES if f.get("name", "").lower() == seg_key), "")
                if _src:
                    break
        if _src:
            feat["source"] = _src


def _add_reaction_type_to_features(feature_data: list[dict]) -> None:
    """Auto-detect reaction features and set action_type='Reaction' on them.
    Scans feature descriptions for reaction keywords so unlimited-use
    reactions (Uncanny Dodge, Deflect Missiles, Slow Fall, etc.) appear
    in the Actions in Combat section alongside limited-use features."""
    import re
    _pattern = re.compile(
        r'(use|using|as|with|spend|expend)\s+(your\s+)?(a\s+)?reaction',
        re.IGNORECASE
    )
    for feat in feature_data:
        if feat.get("action_type"):
            continue  # already has a type
        desc = feat.get("description", "") or ""
        # Only flag if the description explicitly says it uses a reaction
        # (avoid false positives from passing mentions)
        if _pattern.search(desc):
            feat["action_type"] = "Reaction"


def collapse_variant_features(feature_data: list[dict]) -> list[dict]:
    """Drop duplicate level-variant entries (e.g. leveling stored both
    "Brutal Critical (1 die)" @L9 and "(2 dice)" @L13): keep the highest-level
    variant of any feature whose name differs only by a parenthetical suffix.
    Repeatable picks (Expertise, ASIs, invocations, etc.) have no suffix so
    they are untouched by this rule. Also sets feat["base_name"] to the
    suffix-stripped name so template dice badges and level checks match even
    when a suffixed variant of a known feature was stored."""
    for _feat in feature_data:
        if isinstance(_feat, dict) and _feat.get("name"):
            _feat["base_name"] = _feat["name"].split(" (")[0]
    _variant_groups: dict[str, list[dict]] = {}
    for _feat in feature_data:
        if isinstance(_feat, dict) and _feat.get("base_name"):
            _variant_groups.setdefault(_feat["base_name"], []).append(_feat)
    _level_num = lambda _f: int(str(_f.get("level", "L0")).replace("L", "") or 0)
    _drop_ids: set[int] = set()
    for _base, _members in _variant_groups.items():
        if len(_members) < 2:
            continue
        _suffixed = [m for m in _members if " (" in (m.get("name") or "")]
        _plain = [m for m in _members if " (" not in (m.get("name") or "")]
        if not _suffixed or len(_plain) >= 1:
            # no variant group, or a plain base entry exists (distinct feature) — skip
            continue
        _best = max(_members, key=lambda m: (_level_num(m), len(m.get("name") or "")))
        for _m in _members:
            if _m is _best:
                continue
            if _level_num(_m) < _level_num(_best):
                _drop_ids.add(id(_m))
    if _drop_ids:
        return [f for f in feature_data if id(f) not in _drop_ids]
    return feature_data


def _build_rider_cards(feature_data: list[dict], char: dict, class_levels: dict) -> list[dict]:
    """Assemble always-on combat rider callout cards (the Sneak Attack family:
    features adding damage/effects on hit/attack/cast with no action cost).

    Uses the RIDER_CARDS registry keyed by collapsed base_name; a card is
    emitted for every registry entry the character actually has. Level-scaled
    tokens ({ds_dice}, {bc_tag}, {pb_dice}, {init}) are filled from class_levels
    and ability scores."""
    _mod = lambda _s: ability_mod(_s) if isinstance(_s, int) else 0
    _cl = {k: int(v) for k, v in (class_levels or {}).items() if str(v).isdigit()}
    _base_names = {}
    for _feat in feature_data:
        if not isinstance(_feat, dict):
            continue
        _bn = (_feat.get("base_name") or _feat.get("name") or "").strip()
        if _bn:
            _base_names.setdefault(_bn.lower(), _feat)
    _tokens = {
        "init": f"{_mod(char.get('wisdom', 10)):+d}",
        "ds_dice": "2d8" if _cl.get("Cleric", 0) >= 14 else "1d8",
        "bc_tag": "3 dice" if _cl.get("Barbarian", 0) >= 17 else
                 ("2 dice" if _cl.get("Barbarian", 0) >= 13 else "1 die"),
        "pb_dice": ("8d6" if _cl.get("Bard", 0) >= 15 else
                    "5d6" if _cl.get("Bard", 0) >= 10 else
                    "3d6" if _cl.get("Bard", 0) >= 5 else "2d6"),
    }
    _cards = []
    for _key, _entry in RIDER_CARDS.items():
        _feat = _base_names.get(_key)
        if not _feat:
            continue
        _body = _entry.get("body", "")
        _tag = _entry.get("tag", "")
        for _tok, _val in _tokens.items():
            _body = _body.replace("{" + _tok + "}", _val)
            _tag = _tag.replace("{" + _tok + "}", _val)
        _cards.append({
            "name": _feat.get("name") or _entry.get("name", _key.title()),
            "base_name": _feat.get("base_name"),
            "icon": _entry.get("icon", "⚔️"),
            "tag": _tag,
            "body": _body,
            "level": _feat.get("level", ""),
        })
    # Keep registry order but stable for identical feature sets
    return _cards


def _add_dice_to_features(feature_data: list[dict]) -> None:
    """Mutate feature_data in-place: add 'dice' field from FEATS lookup.
    Only adds if feat doesn't already have a dice field (preserves hardcoded values)."""
    for feat in feature_data:
        if feat.get("dice"):
            continue  # Already has dice (hardcoded or from previous enrichment)
        name = feat.get("name", "")
        key = name.lower()
        # Look up in FEATS dict
        _feat = FEATS.get(key)
        if _feat and _feat.get("dice"):
            feat["dice"] = _feat["dice"]

def _add_invocation_levels(feature_data: list[dict], class_name: str, char_level: int) -> None:
    """Inject Eldritch Invocation level cards for Warlocks.

    The SRD only lists 'Eldritch Invocations' at level 2, but warlocks gain
    additional invocations at 5, 7, 9, 12, 15, and 18. This adds a feature
    card at each level where invocations are gained, so per-level choices
    can be displayed separately."""
    if class_name != "Warlock":
        return
    inv_levels = INVOCATION_LEVELS.get("Warlock", [])
    existing_levels = {
        int(f["level"].replace("L", ""))
        for f in feature_data
        if f.get("name") == "Eldritch Invocations"
    }
    for inv_lvl in inv_levels:
        if inv_lvl <= char_level and inv_lvl not in existing_levels:
            # Insert at correct position
            insert_at = 0
            for i, f in enumerate(feature_data):
                fl = int(f.get("level", "L0").replace("L", ""))
                if fl > inv_lvl:
                    insert_at = i
                    break
                insert_at = i + 1
            feature_data.insert(insert_at, {
                "name": "Eldritch Invocations",
                "level": f"L{inv_lvl}",
                "description": f"Gain additional Eldritch Invocations at level {inv_lvl}.",
            })
