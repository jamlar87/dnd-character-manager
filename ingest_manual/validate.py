from __future__ import annotations
from .core import VALID_ABILITIES, VALID_RARITIES, VALID_SIZES, VALID_SKILLS, VALID_SPELL_SCHOOLS


def _empty_result() -> dict:
    return {
        "races": [], "spells": [], "magic_items": [], "equipment": [],
        "monsters": [], "npcs": [], "feats": [], "backgrounds": [],
        "subclasses": [], "traps": [],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Validation
# ═══════════════════════════════════════════════════════════════════════════════

def _normalize_ability_key(k: str) -> str:
    """Normalize ability score key: 'str'/'strength' → 'strength'."""
    k = k.lower().strip()
    mapping = {
        "str": "strength", "dex": "dexterity", "con": "constitution",
        "int": "intelligence", "wis": "wisdom", "cha": "charisma",
        "strength": "strength", "dexterity": "dexterity",
        "constitution": "constitution", "intelligence": "intelligence",
        "wisdom": "wisdom", "charisma": "charisma",
    }
    return mapping.get(k, k)


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-contamination guard — feature → correct subclass mapping
# ═══════════════════════════════════════════════════════════════════════════════

# Maps feature names to their CORRECT subclass. Any feature appearing in a
# different subclass will be stripped during validation. Covers the most common
# LLM cross-contamination patterns (adjacent subclasses of the same class).
_FEATURE_OWNER: dict[str, str] = {
    # Cleric domains (PHB)
    "warding flare": "Light Domain",
    "radiance of the dawn": "Light Domain",
    "improved flare": "Light Domain",
    "corona of light": "Light Domain",
    "acolyte of nature": "Nature Domain",
    "charm animals and plants": "Nature Domain",
    "dampen elements": "Nature Domain",
    "master of nature": "Nature Domain",
    "blessings of knowledge": "Knowledge Domain",
    "knowledge of the ages": "Knowledge Domain",
    "read thoughts": "Knowledge Domain",
    "visions of the past": "Knowledge Domain",
    "wrath of the storm": "Tempest Domain",
    "destructive wrath": "Tempest Domain",
    "thunderbolt strike": "Tempest Domain",
    "stormborn": "Tempest Domain",
    "blessing of the trickster": "Trickery Domain",
    "invoke duplicity": "Trickery Domain",
    "cloak of shadows": "Trickery Domain",
    "improved duplicity": "Trickery Domain",
    "war priest": "War Domain",
    "guided strike": "War Domain",
    "war god's blessing": "War Domain",
    "avatar of battle": "War Domain",
    # Barbarian paths (PHB)
    "frenzy": "Path of the Berserker",
    "mindless rage": "Path of the Berserker",
    "intimidating presence": "Path of the Berserker",
    "retaliation": "Path of the Berserker",
    "totem spirit": "Path of the Totem Warrior",
    "aspect of the beast": "Path of the Totem Warrior",
    "spirit walker": "Path of the Totem Warrior",
    "totemic attunement": "Path of the Totem Warrior",
    # Wizard schools — features that clearly identify their school
    "arcane ward": "School of Abjuration",
    "projected ward": "School of Abjuration",
    "improved abjuration": "School of Abjuration",
    "spell resistance": "School of Abjuration",
    "minor conjuration": "School of Conjuration",
    "benign transposition": "School of Conjuration",
    "focused conjuration": "School of Conjuration",
    "durable summons": "School of Conjuration",
    "portent": "School of Divination",
    "expert divination": "School of Divination",
    "the third eye": "School of Divination",
    "greater portent": "School of Divination",
    "hypnotic gaze": "School of Enchantment",
    "instinctive charm": "School of Enchantment",
    "split enchantment": "School of Enchantment",
    "alter memories": "School of Enchantment",
    "sculpt spells": "School of Evocation",
    "potent cantrip": "School of Evocation",
    "empowered evocation": "School of Evocation",
    "overchannel": "School of Evocation",
    "improved minor illusion": "School of Illusion",
    "malleable illusions": "School of Illusion",
    "illusory self": "School of Illusion",
    "illusory reality": "School of Illusion",
    "grim harvest": "School of Necromancy",
    "undead thralls": "School of Necromancy",
    "inured to undeath": "School of Necromancy",
    "command undead": "School of Necromancy",
    "minor alchemy": "School of Transmutation",
    "transmuter's stone": "School of Transmutation",
    "shapechanger": "School of Transmutation",
    "master transmuter": "School of Transmutation",
}


def _strip_wrong_features(subclass_name: str, parent_class: str, features: list) -> list:
    """Remove features that clearly belong to a different subclass of the same class.
    Uses _FEATURE_OWNER mapping + heuristic checks."""
    if not features:
        return features

    cleaned = []
    sc_lower = subclass_name.lower()
    removed = []

    for feat in features:
        if not isinstance(feat, dict):
            continue
        fname = feat.get("name", "").lower()

        # Check: does this feature belong to a DIFFERENT subclass?
        owner = _FEATURE_OWNER.get(fname)
        if owner and owner.lower() != sc_lower:
            removed.append(feat.get("name", fname))
            continue

        # Heuristic: feature name contains a different domain/path/school name
        # e.g., "Channel Divinity: Radiance of the Dawn" in Nature Domain
        if "channel divinity:" in fname and parent_class.lower() == "cleric":
            # Known cleric CD options — check if they match any domain
            cd_check = fname.replace("channel divinity:", "").strip()
            cd_owner = _FEATURE_OWNER.get(cd_check)
            if cd_owner and cd_owner.lower() != sc_lower:
                removed.append(feat.get("name", fname))
                continue

        cleaned.append(feat)

    if removed:
        print(f"      ⚠ Stripped {len(removed)} cross-contaminated feature(s) from {subclass_name}: {removed}")

    return cleaned


def validate_extraction(data: dict, book_slug: str) -> dict:
    """Post-process and validate extracted data. Removes invalid entries."""
    cleaned = {
        "races": [], "spells": [], "magic_items": [], "equipment": [],
        "monsters": [], "npcs": [], "feats": [], "backgrounds": [],
        "subclasses": [], "traps": [],
    }
    issues = []

    def _ensure_dict(item, default_name="Unknown") -> dict:
        """Guard: convert string entries to dicts."""
        if isinstance(item, str):
            return {"name": item}
        if not isinstance(item, dict):
            return None
        return item

    # ── Races ──
    for r in data.get("races", []):
        r = _ensure_dict(r)
        if not r or not r.get("name"):
            continue
        # Normalize ASI keys
        if "asi" in r and isinstance(r["asi"], dict):
            r["asi"] = {_normalize_ability_key(k): v for k, v in r["asi"].items()
                        if _normalize_ability_key(k) in VALID_ABILITIES}
        # Validate numeric fields
        for fld in ("speed", "darkvision"):
            if fld in r and not isinstance(r.get(fld), (int, float)):
                try:
                    r[fld] = int(r[fld])
                except (ValueError, TypeError):
                    issues.append(f"Race {r['name']}: invalid {fld}={r.get(fld)}")
        # Flag races with zero traits AND zero subraces (likely incomplete)
        traits = r.get("traits", [])
        subraces = r.get("subraces", [])
        if not traits and not subraces:
            issues.append(f"Race {r['name']}: no traits or subraces — may be incomplete")
        r.setdefault("source", book_slug)
        cleaned["races"].append(r)

    # ── Spells ──
    for s in data.get("spells", []):
        s = _ensure_dict(s)
        if not s or not s.get("name"):
            continue
        # Reject spells with empty descriptions (partial/chunked entries)
        if not s.get("description", "").strip():
            issues.append(f"Spell {s['name']}: empty description — rejected")
            continue
        if "level" in s and not isinstance(s.get("level"), int):
            try:
                s["level"] = int(s["level"])
            except (ValueError, TypeError):
                issues.append(f"Spell {s['name']}: invalid level={s.get('level')}")
        if s.get("school", "").lower() not in VALID_SPELL_SCHOOLS:
            issues.append(f"Spell {s['name']}: unknown school={s.get('school')}")
        s.setdefault("source", book_slug)
        cleaned["spells"].append(s)

    # ── Magic Items ──
    for item in data.get("magic_items", []):
        item = _ensure_dict(item)
        if not item or not item.get("name"):
            continue
        # Clean up garbled item names
        name = item.get("name", "")
        if len(name) > 60:
            # Try to extract a shorter name from the first sentence of description
            desc = item.get("description", "")
            if desc and "." in desc:
                item["name"] = desc.split(".")[0].strip()[:60]
                issues.append(f"Item name cleaned: '{name[:50]}...' → '{item['name']}'")
            else:
                item["name"] = name[:60].rsplit(" ", 1)[0]  # Truncate to last full word
                issues.append(f"Item name truncated: '{name[:50]}...'")
        if item.get("rarity", "").lower() not in VALID_RARITIES:
            issues.append(f"Item {item['name']}: unknown rarity={item.get('rarity')}")
        item.setdefault("source", book_slug)
        cleaned["magic_items"].append(item)

    # ── Equipment ──
    for eq in data.get("equipment", []):
        if isinstance(eq, str):
            eq = {"name": eq, "type": "adventuring gear"}
        if not eq.get("name"):
            continue
        # Flag equipment with no cost or weight (likely incomplete extraction)
        if not eq.get("cost") and eq.get("weight", 0) == 0:
            issues.append(f"Equipment {eq['name']}: missing cost/weight")
        eq.setdefault("source", book_slug)
        cleaned["equipment"].append(eq)

    # ── Monsters ──
    for m in data.get("monsters", []):
        m = _ensure_dict(m)
        if not m or not m.get("name"):
            continue
        # Tag siege equipment / vehicles by their type
        mtype = str(m.get("type", "")).lower()
        if mtype in ("object", "vehicle", "siege equipment", "siege weapon"):
            m["type"] = "vehicle (siege)"
        # Reject monsters without stat blocks (name-only mentions)
        ac = m.get("armor_class")
        hp = m.get("hit_points")
        abilities = m.get("ability_scores", {})
        if not ac or not hp:
            issues.append(f"Monster {m['name']}: rejected (missing AC={ac}, HP={hp})")
            continue
        if not isinstance(abilities, dict) or len(abilities) < 3:
            issues.append(f"Monster {m['name']}: rejected (insufficient ability scores)")
            continue
        if m.get("size") and m["size"] not in VALID_SIZES:
            issues.append(f"Monster {m['name']}: unknown size={m.get('size')}")
        # Validate XP matches CR per the 5e XP table
        cr = m.get("challenge_rating", "")
        xp = m.get("xp", 0)
        _XP_TABLE = {
            "0": 10, "1/8": 25, "1/4": 50, "1/2": 100, "1": 200, "2": 450,
            "3": 700, "4": 1100, "5": 1800, "6": 2300, "7": 2900, "8": 3900,
            "9": 5000, "10": 5900, "11": 7200, "12": 8400, "13": 10000,
            "14": 11500, "15": 13000, "16": 15000, "17": 18000, "18": 20000,
            "19": 22000, "20": 25000, "21": 33000, "22": 41000, "23": 50000,
            "24": 62000, "25": 75000, "26": 90000, "27": 105000, "28": 120000,
            "29": 135000, "30": 155000,
        }
        if cr and xp and str(cr) in _XP_TABLE:
            expected = _XP_TABLE[str(cr)]
            ratio = xp / expected if expected else 0
            if ratio < 0.5 or ratio > 2.0:
                issues.append(f"Monster {m['name']}: XP ({xp}) doesn't match CR {cr} (expected {expected})")
        # Normalize abilities
        if "ability_scores" in m and isinstance(m["ability_scores"], dict):
            m["ability_scores"] = {_normalize_ability_key(k): v
                                   for k, v in m["ability_scores"].items()
                                   if _normalize_ability_key(k) in VALID_ABILITIES}
        m.setdefault("source", book_slug)
        cleaned["monsters"].append(m)

    # ── NPCs ──
    for npc in data.get("npcs", []):
        npc = _ensure_dict(npc)
        if not npc or not npc.get("name"):
            continue
        if "ability_scores" in npc and isinstance(npc["ability_scores"], dict):
            npc["ability_scores"] = {_normalize_ability_key(k): v
                                     for k, v in npc["ability_scores"].items()
                                     if _normalize_ability_key(k) in VALID_ABILITIES}
        npc.setdefault("source", book_slug)
        cleaned["npcs"].append(npc)

    # ── Feats ──
    for f in data.get("feats", []):
        f = _ensure_dict(f)
        if not f or not f.get("name"):
            continue
        # Validate prerequisite field presence (LLM sometimes omits it)
        prereq = f.get("prerequisite")
        if prereq is None:
            issues.append(f"Feat {f['name']}: missing prerequisite field (added empty)")
            f["prerequisite"] = ""
        f.setdefault("source", book_slug)
        cleaned["feats"].append(f)

    # ── Backgrounds ──
    for bg in data.get("backgrounds", []):
        bg = _ensure_dict(bg)
        if not bg or not bg.get("name"):
            continue
        # Validate skill proficiencies against known D&D skills
        raw_skills = bg.get("skill_proficiencies", [])
        if raw_skills:
            cleaned_skills = [s for s in raw_skills if s in VALID_SKILLS]
            invalid = len(raw_skills) - len(cleaned_skills)
            if invalid:
                bad = [s for s in raw_skills if s not in VALID_SKILLS]
                issues.append(f"Background {bg['name']}: {invalid} unknown skill(s) stripped: {bad}")
            bg["skill_proficiencies"] = cleaned_skills
        bg.setdefault("source", book_slug)
        cleaned["backgrounds"].append(bg)

    # ── Subclasses ──
    for sc in data.get("subclasses", []):
        sc = _ensure_dict(sc)
        if not sc or not sc.get("name"):
            continue
        sc.setdefault("source", book_slug)
        # Strip features that belong to a different subclass (cross-contamination guard)
        sc["features"] = _strip_wrong_features(sc.get("name", ""), sc.get("class", ""), sc.get("features", []))
        # Flag subclasses with unusually few features (likely incomplete extraction)
        feat_count = len(sc.get("features", []))
        if feat_count < 3 and feat_count > 0:
            issues.append(f"Subclass {sc['name']}: only {feat_count} features — may be incomplete")
        cleaned["subclasses"].append(sc)

    # ── Traps ──
    for t in data.get("traps", []):
        t = _ensure_dict(t)
        if not t or not t.get("name"):
            continue
        # Validate required fields for traps
        if not t.get("trigger"):
            issues.append(f"Trap {t['name']}: missing trigger")
        if not t.get("effect") and not t.get("damage"):
            issues.append(f"Trap {t['name']}: missing effect and damage (may be incomplete)")
        # Validate danger level
        valid_dangers = {"setback", "dangerous", "deadly"}
        if t.get("danger") and t["danger"].lower() not in valid_dangers:
            issues.append(f"Trap {t['name']}: unknown danger={t.get('danger')}")
        # Validate type
        valid_types = {"mechanical", "magical", "hazard"}
        if t.get("type") and t["type"].lower() not in valid_types:
            issues.append(f"Trap {t['name']}: unknown type={t.get('type')}")
        t.setdefault("source", book_slug)
        cleaned["traps"].append(t)

    if issues:
        print(f"    ⚠ Validation issues: {len(issues)}")
        for issue in issues[:5]:
            print(f"      - {issue}")

    return cleaned
