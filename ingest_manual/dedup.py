from __future__ import annotations
import re
from .core import HERE
from .jsonio import _load_json_list


# ═══════════════════════════════════════════════════════════════════════════════
# Quality scoring for duplicate resolution
# ═══════════════════════════════════════════════════════════════════════════════

# D&D 5e domain-specific keywords — weighted by signal strength
_QUALITY_KEYWORDS: dict[str, float] = {
    # Mechanical terms (strong signal — real rules text)
    "saving throw": 2.0, "attack roll": 2.0, "bonus action": 2.0,
    "ability check": 1.5, "damage": 1.0, "resistance": 1.5,
    "immunity": 1.5, "vulnerability": 1.5, "concentration": 1.5,
    "spell slot": 1.5, "proficiency": 1.0, "advantage": 1.5,
    "disadvantage": 1.5, "hit points": 1.0, "armor class": 1.0,
    "challenge": 1.0, "initiative": 1.0, "reaction": 1.5,
    "ritual": 1.0, "cantrip": 1.0, "melee": 0.5,
    "ranged": 0.5, "touch": 0.5, "self": 0.3,
    # Structural signals (boilerplate = weak signal)
    "you can": 0.3, "you gain": 0.5, "at higher levels": 1.0,
    "per rest": 1.5, "per day": 1.0, "once per": 1.5,
    "you must": 0.5, "until you": 0.5,
}

# Penalty keywords — things that indicate LLM hallucination or generic filler
_QUALITY_PENALTY: dict[str, float] = {
    "i don't": 5.0, "i cannot": 5.0, "as an ai": 10.0,
    "might be": 3.0, "probably": 3.0, "typically": 2.0,
    "generally": 2.0, "in some cases": 2.0, "often": 1.5,
}


def _quality_score(entry: dict, category: str) -> float:
    """Score an extracted entry on detail/completeness. Higher = better.

    Categories: races, spells, magic_items, equipment, monsters, npcs,
                feats, backgrounds, subclasses.
    """
    score = 0.0
    text_fields: list[str] = []

    # ── 1. Gather all textual content ──
    desc = entry.get("description", "") or ""
    if isinstance(desc, str):
        text_fields.append(desc)

    # Sub-entity descriptions
    for key in ("traits", "features", "actions", "reactions", "legendary_actions"):
        for sub in entry.get(key, []) or []:
            if isinstance(sub, dict):
                sd = sub.get("description", "") or ""
                if isinstance(sd, str) and sd:
                    text_fields.append(sd)

    # Subraces carry their own nested content
    for sr in entry.get("subraces", []) or []:
        if isinstance(sr, dict):
            sd = sr.get("description", "") or ""
            if isinstance(sd, str) and sd:
                text_fields.append(sd)
            for st in sr.get("traits", []) or []:
                if isinstance(st, dict):
                    td = st.get("description", "") or ""
                    if isinstance(td, str) and td:
                        text_fields.append(td)

    combined = " ".join(text_fields).lower()

    # ── 2. Description length (log-scale, caps at ~2000 chars) ──
    total_len = len(" ".join(text_fields))
    if total_len == 0:
        score -= 20.0  # Heavy penalty for empty content
    else:
        score += min(total_len / 200, 10.0)  # 200 chars = 1 pt, max 10 pts

    # ── 3. Keyword density (D&D mechanics) ──
    for keyword, weight in _QUALITY_KEYWORDS.items():
        count = combined.count(keyword)
        if count:
            score += min(count * weight, 5.0)  # Cap per-keyword contribution

    # ── 4. Penalty for LLM hallmarks ──
    for penalty_word, penalty in _QUALITY_PENALTY.items():
        if penalty_word in combined:
            score -= penalty

    # ── 5. Category-specific structural completeness ──
    score += _structural_bonus(entry, category)

    return score


def _structural_bonus(entry: dict, category: str) -> float:
    """Reward entries that have all expected structural fields for their category."""
    bonus = 0.0
    fields = {
        "races": ["asi", "speed", "size", "languages", "traits"],
        "spells": ["level", "school", "casting_time", "range", "components", "duration"],
        "magic_items": ["type", "rarity"],
        "equipment": ["type", "cost", "weight"],
        "monsters": ["size", "type", "armor_class", "hit_points", "ability_scores", "challenge_rating"],
        "npcs": ["race", "armor_class", "hit_points", "ability_scores"],
        "feats": ["prerequisite"],
        "backgrounds": ["skill_proficiencies", "equipment"],
        "subclasses": ["class", "features"],
        "traps": ["trigger", "effect", "damage", "save_dc"],
    }
    expected = fields.get(category, [])
    if not expected:
        return 0.0

    present = sum(1 for f in expected if entry.get(f))
    bonus += (present / len(expected)) * 3.0  # Max 3 pts for full structure

    # Sub-entities bonus
    sub_lists = {
        "races": "subraces",
        "subclasses": "features",
        "monsters": "actions",
        "backgrounds": "skill_proficiencies",
    }
    sub_key = sub_lists.get(category)
    if sub_key:
        sub_val = entry.get(sub_key)
        if isinstance(sub_val, list) and len(sub_val) > 0:
            bonus += 2.0  # Has sub-entities

    return bonus


def _pick_better(existing: dict, new: dict, category: str) -> dict:
    """Compare two entries and return the higher-quality one.
    If new is measurably better (>15% score delta), return new.
    Otherwise keep existing (stability bias)."""
    existing_score = _quality_score(existing, category)
    new_score = _quality_score(new, category)

    # If new is significantly better (>15% margin), use it
    if existing_score <= 0 and new_score > 0:
        return new
    if new_score > existing_score * 1.15:
        return new
    return existing


# ═══════════════════════════════════════════════════════════════════════════════
# Dedup against known base data
# ═══════════════════════════════════════════════════════════════════════════════

def _load_base_names() -> dict[str, set]:
    """Load known names from SRD cache and hardcoded data to avoid duplicates."""
    names = {k: set() for k in ["races", "spells", "magic_items", "equipment",
                                  "monsters", "npcs", "feats", "backgrounds",
                                  "subclasses", "traps"]}

    srd_dir = HERE / "data" / "srd_cache"

    # Races from SRD cache
    for fname in ["races", "subraces"]:
        data = _load_json_list(srd_dir / f"{fname}.json")
        for entry in data:
            names["races"].add(entry.get("name", "").lower())

    # Also hardcoded RACES from main.py
    hardcoded_races = ["Dwarf", "Elf", "Halfling", "Human", "Dragonborn",
                       "Gnome", "Half-Elf", "Half-Orc", "Tiefling"]
    for r in hardcoded_races:
        names["races"].add(r.lower())

    # Spells from SRD
    spells = _load_json_list(srd_dir / "spells.json")
    for s in spells:
        names["spells"].add(s.get("name", "").lower())

    # Monsters from SRD
    monsters = _load_json_list(srd_dir / "monsters.json")
    for m in monsters:
        names["monsters"].add(m.get("name", "").lower())

    # Magic items from SRD
    items = _load_json_list(srd_dir / "magic-items.json")
    for i in items:
        names["magic_items"].add(i.get("name", "").lower())

    # Equipment from SRD
    equip = _load_json_list(srd_dir / "equipment.json")
    for e in equip:
        names["equipment"].add(e.get("name", "").lower())

    # Feats from SRD
    feats = _load_json_list(srd_dir / "feats.json")
    for f in feats:
        names["feats"].add(f.get("name", "").lower())

    # Backgrounds from SRD
    bgs = _load_json_list(srd_dir / "backgrounds.json")
    for bg in bgs:
        names["backgrounds"].add(bg.get("name", "").lower())

    return names


def _normalize_name(name: str) -> str:
    """Normalize for dedup: lowercase, normalize dashes,
    strip trailing 's' (singular/plural), remove punctuation.
    Parentheticals are NOT stripped — handled by dedup logic at a higher level
    so "Criminal" and "Criminal (Myriad Operative)" stay distinct."""
    n = name.lower().strip()
    # Strip leading "the " for dedup: "The Great Old One" ≈ "Great Old One"
    if n.startswith('the '):
        n = n[4:]
    # Normalize dashes to spaces: "Half-Elf" → "half elf"
    n = n.replace('-', ' ').replace('–', ' ').replace('—', ' ')
    # Collapse multiple spaces
    n = re.sub(r'\s+', ' ', n).strip()
    # Strip trailing punctuation
    n = n.rstrip('.,;:!?')
    # Strip trailing 's' for plural dedup (Geonid/Geonids, Cannibal/Cannibals)
    # But not if it would leave a too-short word or the word ends in 'ss'
    if n.endswith('s') and not n.endswith('ss') and len(n) > 4:
        n = n[:-1]
    return n


def dedup_extraction(data: dict, base_names: dict[str, set]) -> dict:
    """Remove entries whose name already exists in base data. Uses fuzzy matching.
    Does NOT mutate base_names — works on a copy to prevent cross-chapter contamination."""
    new_data = {}
    total_removed = 0

    for category in data:
        items = data[category]
        # Copy to avoid mutating the caller's base_names (critical for chapter-by-chapter mode)
        known = set(base_names.get(category, set()))
        known_normalized = {_normalize_name(n) for n in known}
        new_items = []
        for item in items:
            name = item.get("name", "")
            name_lower = name.lower()
            name_norm = _normalize_name(name)
            # Exact match
            if name_lower and name_lower in known:
                total_removed += 1
                continue
            # Fuzzy match (singular/plural)
            if name_norm and name_norm in known_normalized:
                # False-positive guard: if current name is plain (no parenthetical)
                # and every matching known entry HAS a parenthetical, keep it.
                # e.g. "Criminal" (WGE) is distinct from "Criminal (Myriad Operative)" (EGW)
                if '(' not in name:
                    matching_known = [kn for kn in known if _normalize_name(kn) == name_norm]
                    if matching_known and all('(' in kn for kn in matching_known):
                        pass  # false positive — keep as a distinct plain-name entry
                    else:
                        total_removed += 1
                        continue
                else:
                    total_removed += 1
                    continue
            new_items.append(item)
            if name_lower:
                known.add(name_lower)
                known_normalized.add(name_norm)
        new_data[category] = new_items

    if total_removed:
        print(f"    Deduped {total_removed} entries already in base data")
    return new_data


def _dedup_within_extraction(data: dict) -> dict:
    """Remove duplicates *within* a single extraction (same book, different chunks).
    Uses the same _normalize_name logic for fuzzy matching.
    When two entries for the same name exist, keeps the higher-quality one."""
    deduped = {}
    total_removed = 0
    total_upgraded = 0
    for category, items in data.items():
        if category.startswith("_"):
            continue
        seen: dict[str, tuple[dict, int]] = {}  # name_norm → (entry, index)
        new_items = []
        for item in items:
            name = item.get("name", "")
            name_norm = _normalize_name(name)
            if not name_norm:
                new_items.append(item)
                continue
            if name_norm in seen:
                existing, _ = seen[name_norm]
                better = _pick_better(existing, item, category)
                if better is item:
                    # New entry is better — replace the old one
                    total_upgraded += 1
                    # Find and replace the old entry in new_items
                    for i, ni in enumerate(new_items):
                        if _normalize_name(ni.get("name", "")) == name_norm:
                            new_items[i] = item
                            seen[name_norm] = (item, i)
                            break
                    total_removed += 1  # Count the old one as removed
                else:
                    total_removed += 1  # Discard new, keep existing
                continue
            idx = len(new_items)
            seen[name_norm] = (item, idx)
            new_items.append(item)
        deduped[category] = new_items
    if total_removed:
        parts = [f"removed {total_removed} duplicate(s)"]
        if total_upgraded:
            parts.append(f"upgraded {total_upgraded} with better version")
        print(f"    Intra-book dedup: {', '.join(parts)}")
    return deduped
