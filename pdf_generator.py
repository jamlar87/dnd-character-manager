"""
D&D 5e Official Character Sheet PDF Generator — v12 (section gaps increased, label overlap fixed).
- Page 1: 3-column rigid grid with 15pt horizontal gutters between columns.
- Page 2: 5 locked narrative bounding boxes, no feature overflows.
- Page 3: Spell matrix — 3 isolated columns (1-2 | 3-5 | 6-9), each level a self-contained card.
- Page 4: Class Feature Appendix (full rulebook text), generated only when features overflow.
"""
import json
import os
import sys
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Register Cinzel Bold for D&D title font
_FONTS_DIR = os.path.dirname(os.path.abspath(__file__))
try:
    pdfmetrics.registerFont(TTFont("CinzelBold", os.path.join(_FONTS_DIR, "fonts", "Cinzel-Bold.ttf")))
    TITLE_FONT = "CinzelBold"
except Exception:
    TITLE_FONT = "Times-Bold"  # fallback

# ═══════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════
PAGE_W, PAGE_H = letter  # 612 x 792
FONT = "Times-Roman"  # D&D-style serif
FONT_BOLD = "Times-Bold"
FONT_OBL = "Times-Italic"
MARGIN = 18
GUTTER = 15  # horizontal padding between columns


def yb(y_tl):
    """Convert top-left y to reportlab bottom-left y."""
    return PAGE_H - y_tl


# ═══════════════════════════════════════════════════════════════
#  SPELL CACHE (lightweight — only used for slot lookups)
# ═══════════════════════════════════════════════════════════════
_SPELL_CACHE = None


def _get_spell_cache():
    global _SPELL_CACHE
    if _SPELL_CACHE is not None:
        return _SPELL_CACHE
    try:
        sys.path.insert(0, "/home/james/dnd-campaign-expert")
        from engine.spells import _load_spell_cache
        raw = _load_spell_cache()
        _SPELL_CACHE = {}
        for s in raw:
            _SPELL_CACHE[s["name"].lower()] = s
    except Exception:
        _SPELL_CACHE = {}
    # Also load from app's manual_data/spells.json (catches TCoE/SCAG spells not in SRD)
    try:
        manual_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "data", "manual_data", "spells.json"
        )
        if os.path.exists(manual_path):
            with open(manual_path) as f:
                manual_spells = json.load(f)
            for s in manual_spells:
                name = s.get("name", "").lower()
                if name and name not in _SPELL_CACHE:
                    _SPELL_CACHE[name] = s
    except Exception:
        pass
    # Supplementary spells not in any data source (Fizban's, etc.)
    _SUPPLEMENTARY_SPELLS = {
        "absorb elements": {
            "name": "Absorb Elements", "level": 1, "school": "Abjuration",
            "casting_time": "1 reaction, which you take when you take acid, cold, fire, lightning, or thunder damage",
            "range": "Self",
            "components": "S",
            "duration": "1 round", "concentration": False,
            "description": "The spell captures some of the incoming energy, lessening its effect on you and storing it for your next melee attack. You have resistance to the triggering damage type until the start of your next turn. Also, the first time you hit with a melee attack on your next turn, the target takes an extra 1d6 damage of the triggering type, and the spell ends. At Higher Levels. When you cast this spell using a spell slot of 2nd level or higher, the extra damage increases by 1d6 for each slot level above 1st.",
            "_source": "Xanathar's Guide to Everything",
        },
        "aganazzar's scorcher": {
            "name": "Aganazzar's Scorcher", "level": 2, "school": "Evocation",
            "casting_time": "1 action", "range": "30 feet",
            "components": "V, S, M (a red dragon's scale)",
            "duration": "Instantaneous", "concentration": False,
            "description": "A line of roaring flame 30 feet long and 5 feet wide emanates from you in a direction you choose. Each creature in the line must make a Dexterity saving throw. A creature takes 3d8 fire damage on a failed save, or half as much damage on a successful one. At Higher Levels. When you cast this spell using a spell slot of 3rd level or higher, the damage increases by 1d8 for each slot level above 2nd.",
            "_source": "Xanathar's Guide to Everything",
        },
        "beast bond": {
            "name": "Beast Bond", "level": 1, "school": "Divination",
            "casting_time": "1 action", "range": "Touch",
            "components": "V, S, M (a bit of fur wrapped in a cloth)",
            "duration": "Concentration, up to 10 minutes", "concentration": True,
            "description": "You establish a telepathic link with one beast you touch that is friendly to you or charmed by you. The spell fails if the beast's Intelligence score is 4 or higher. Until the spell ends, the link is active while you and the beast are within line of sight of each other. Through the link, the beast can understand your telepathic messages to it, and it can telepathically communicate simple emotions and concepts back to you. While the link is active, the beast gains advantage on attack rolls against any creature within 5 feet of you that you can see.",
            "_source": "Xanathar's Guide to Everything",
        },
        "bones of the earth": {
            "name": "Bones of the Earth", "level": 6, "school": "Transmutation",
            "casting_time": "1 action", "range": "120 feet",
            "components": "V, S",
            "duration": "Instantaneous", "concentration": False,
            "description": "You cause up to six pillars of stone to burst from places on the ground that you can see within range. Each pillar is a cylinder that has a 5-foot radius and is up to 30 feet tall. The specific shape of each pillar is up to you, but each must occupy a space on solid ground, and you can't create a pillar in the same space as a creature. The ground where a pillar appears must be able to support it. A creature in the area where a pillar appears must succeed on a Dexterity saving throw or be lifted by the pillar. A creature can choose to fail the save. If a pillar is prevented from reaching its full height because of a ceiling or other obstacle, a creature on the pillar takes 6d6 bludgeoning damage and is restrained, pinched between the pillar and the obstacle. The restrained creature can use an action to make a Strength or Dexterity check (its choice) against the spell's save DC. On a success, it is no longer restrained and must either move off the pillar or fall off it. At Higher Levels. When you cast this spell using a spell slot of 7th level or higher, you can create two additional pillars for each slot level above 6th.",
            "_source": "Elemental Evil / Xanathar's Guide to Everything",
        },
        "catapult": {
            "name": "Catapult", "level": 1, "school": "Transmutation",
            "casting_time": "1 action", "range": "60 feet",
            "components": "S",
            "duration": "Instantaneous", "concentration": False,
            "description": "Choose one object weighing 1 to 5 pounds within range that isn't being worn or carried. The object flies in a straight line up to 90 feet in a direction you choose before falling to the ground, stopping early if it impacts against a solid surface. If the object would strike a creature, that creature must make a Dexterity saving throw. On a failed save, the object strikes the target and stops moving. When the object strikes something, the object and what it strikes take 3d8 bludgeoning damage. At Higher Levels. When you cast this spell using a spell slot of 2nd level or higher, the maximum weight of objects that you can target increases by 5 pounds, and the damage increases by 1d8, for each slot level above 1st.",
            "_source": "Xanathar's Guide to Everything",
        },
        "snilloc's snowball swarm": {
            "name": "Snilloc's Snowball Swarm", "level": 2, "school": "Evocation",
            "casting_time": "1 action", "range": "90 feet",
            "components": "V, S, M (a piece of ice or a small white rock chip)",
            "duration": "Instantaneous", "concentration": False,
            "description": "A swarm of magical snowballs erupts from a point you choose within range. Each creature in a 5-foot-radius sphere centered on that point must make a Dexterity saving throw. A creature takes 3d6 cold damage on a failed save, or half as much damage on a successful one. At Higher Levels. When you cast this spell using a spell slot of 3rd level or higher, the damage increases by 1d6 for each slot level above 2nd.",
            "_source": "Xanathar's Guide to Everything",
        },
        "cloying darkness": {
            "name": "Cloying Darkness", "level": 1, "school": "Necromancy",
            "casting_time": "1 action", "range": "30 feet",
            "components": "V, S",
            "duration": "1 round", "concentration": False,
            "description": "You reach out with a hand of decaying shadows. Make a ranged spell attack. If it hits, the target takes 2d8 necrotic damage and must make a Constitution saving throw. If it fails, its visual organs are enveloped in shadow until the start of your next turn, causing it to treat all lighting as if it's one step lower in intensity (it treats bright light as dim, dim light as darkness, and darkness as magical darkness). At Higher Levels. When you cast this spell using a spell slot of 2nd level or higher, the damage increases by 1d8 for each slot level above 1st.",
            "_source": "Kobold Press OGL",
        },
        "summon draconic spirit": {
            "name": "Summon Draconic Spirit", "level": 5, "school": "Conjuration",
            "casting_time": "1 action", "range": "60 feet",
            "components": "V, S, M (an object with an image of a dragon worth at least 500 gp)",
            "duration": "Concentration, up to 1 hour", "concentration": True,
            "description": "You call forth a draconic spirit. It manifests in an unoccupied space that you can see within range. This corporeal form uses the Draconic Spirit stat block. When you cast this spell, choose a family of dragon: chromatic, gem, or metallic. The creature resembles a dragon of the chosen family, which determines certain traits in its stat block. The creature disappears when it drops to 0 hit points or when the spell ends. The creature is an ally to you and your companions. In combat, the creature shares your initiative count, but it takes its turn immediately after yours. It obeys your verbal commands (no action required by you). If you don't issue any, it takes the Dodge action and uses its move to avoid danger. At Higher Levels. When you cast this spell using a spell slot of 6th level or higher, use the higher level wherever the spell's level appears in the stat block.",
            "_source": "Fizban's Treasury of Dragons",
        },
    }
    for name, data in _SUPPLEMENTARY_SPELLS.items():
        existing = _SPELL_CACHE.get(name, {})
        existing_desc = existing.get("desc", existing.get("description", ""))
        if isinstance(existing_desc, list):
            existing_desc = " ".join(existing_desc)
        # Overwrite if missing, or if existing description is a stub (< 50 chars)
        if name not in _SPELL_CACHE or len(existing_desc.strip()) < 50:
            _SPELL_CACHE[name] = data
    return _SPELL_CACHE


# ═══════════════════════════════════════════════════════════════
#  EQUIPMENT DESCRIPTION CACHE — from main.py ITEM_INDEX
# ═══════════════════════════════════════════════════════════════
_ITEM_INDEX = None


def _get_item_description(item_name):
    """Look up an item's PHB description from the app's ITEM_INDEX."""
    global _ITEM_INDEX
    if _ITEM_INDEX is None:
        try:
            sys.path.insert(0, "/home/james/dnd-character-manager")
            from main import ITEM_INDEX
            _ITEM_INDEX = ITEM_INDEX
        except Exception:
            _ITEM_INDEX = {}
    name_lower = (item_name or "").lower()
    entry = _ITEM_INDEX.get(name_lower, {})
    return entry.get("description", "") or entry.get("type", "") or ""


# ═══════════════════════════════════════════════════════════════
#  DATA BUILDER
# ═══════════════════════════════════════════════════════════════
def build_char_data(row, db_cursor=None, racial_traits=None):
    if hasattr(row, "keys"):
        d = dict(row)
    else:
        raise TypeError("build_char_data requires sqlite3.Row")

    json_fields = [
        "skills", "tool_proficiencies", "weapon_proficiencies",
        "armor_proficiencies", "languages", "features", "inventory",
        "equipped", "feature_data", "attacks_data", "spell_slot_data",
        "save_proficiencies", "damage_resistances", "damage_immunities",
        "damage_vulnerabilities", "condition_immunities", "class_levels",
        "attuned_items", "background_data", "spell_slots_used",
        "personality_data", "summons",
    ]
    for field in json_fields:
        val = d.get(field)
        if isinstance(val, str) and val:
            try:
                d[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass

    for field in json_fields:
        if d.get(field) is None:
            d[field] = {} if field in ("spell_slots_used", "spell_slot_data", "background_data", "personality_data") else []

    for f in ["personality", "backstory", "alignment", "subrace", "subclass"]:
        d.setdefault(f, "")

    pd = d.get("personality_data", {}) or {}
    bg_data = d.get("background_data", {}) or {}
    # Personality Traits: personality_data.traits → old personality field
    d["personality_traits"] = pd.get("traits", "") or d.get("personality", "")
    d["ideals"] = pd.get("ideals", "") or bg_data.get("ideals", "") or d.get("ideals", "") or ""
    d["bonds"] = pd.get("bonds", "") or bg_data.get("bonds", "") or d.get("bonds", "") or ""
    d["flaws"] = pd.get("flaws", "") or bg_data.get("flaws", "") or d.get("flaws", "") or ""
    # Physical appearance
    for fld in ("age", "height", "weight", "eyes", "skin", "hair"):
        d[fld] = pd.get(fld, "")
    # Allies & Organizations
    d["allies"] = pd.get("allies", "")

    d["initiative"] = mod_int(d.get("dexterity", 10))
    d["passive_perception"] = d.get("passive_perception", 10) or 10
    d["inspiration"] = d.get("inspiration", 0) or 0
    d["proficiency_bonus"] = d.get("proficiency_bonus", 2) or 2
    d["str_mod"] = mod_int(d.get("strength", 10))
    d["dex_mod"] = mod_int(d.get("dexterity", 10))
    d["con_mod"] = mod_int(d.get("constitution", 10))
    d["int_mod"] = mod_int(d.get("intelligence", 10))
    d["wis_mod"] = mod_int(d.get("wisdom", 10))
    d["cha_mod"] = mod_int(d.get("charisma", 10))

    hd = d.get("hit_dice", "1d8") or "1d8"
    d["hit_dice_type"] = hd
    d["hit_dice_total"] = d.get("level", 1)
    d["hit_dice_used"] = d.get("hit_dice_used", 0) or 0

    ss = d.get("spell_slot_data", {}) or {}
    d["spell_slots"] = ss.get("by_level", {})
    d["spell_slots_used"] = d.get("spell_slots_used", {}) or {}
    d["cp"] = d.get("cp", 0) or 0
    d["gp"] = d.get("gp", 0) or 0

    d["spells"] = []
    d["is_caster"] = False
    if db_cursor:
        spells = db_cursor.execute(
            "SELECT spell_name, spell_level, prepared FROM character_spells WHERE character_id=? ORDER BY spell_level, spell_name",
            (d["id"],),
        ).fetchall()
        if spells:
            d["spells"] = [(s[0], s[1], s[2]) for s in spells]
            d["is_caster"] = True

    class_to_ability = {
        "Bard": "CHA", "Cleric": "WIS", "Druid": "WIS",
        "Paladin": "CHA", "Ranger": "WIS", "Sorcerer": "CHA",
        "Warlock": "CHA", "Wizard": "INT",
    }
    d["spell_ability"] = d.get("spell_ability") or class_to_ability.get(d.get("class_name", ""), "")

    ab_scores = {
        "STR": d.get("strength", 10), "DEX": d.get("dexterity", 10),
        "CON": d.get("constitution", 10), "INT": d.get("intelligence", 10),
        "WIS": d.get("wisdom", 10), "CHA": d.get("charisma", 10),
    }
    sa = d["spell_ability"]
    spell_ab_mod = mod_int(ab_scores.get(sa, 10)) if sa else 0
    prof = d["proficiency_bonus"]
    d["spell_save_dc"] = 8 + prof + spell_ab_mod
    d["spell_attack_bonus"] = prof + spell_ab_mod

    # Merge racial traits into features and feature_data for the PDF
    if racial_traits:
        features_list = d.get("features", []) or []
        feature_data_list = d.get("feature_data", []) or []
        seen_rt = {fd.get("name", "").lower() for fd in feature_data_list}
        seen_rt.update((f.get("name", "") if isinstance(f, dict) else "").lower() for f in features_list if isinstance(f, dict))
        for rt in racial_traits:
            rt_name = rt.get("name", "")
            if rt_name.lower() not in seen_rt:
                features_list.append({"name": rt_name, "source": rt.get("source", "Race")})
                feature_data_list.append({"name": rt_name, "description": rt.get("desc", "")})
                seen_rt.add(rt_name.lower())
        d["features"] = features_list
        d["feature_data"] = feature_data_list

    d["condensed_features"] = _build_condensed_features(d)
    d["full_feature_text"] = _build_full_feature_text(d)
    d["page1_features"] = _build_page1_features_text(d)
    d["short_features"] = _build_short_features_text(d)
    d["spell_appendix"] = _build_spell_appendix_text(d)
    d["has_long_features"] = len(d.get("full_feature_text", "")) > 140
    d["equipment_appendix"] = _build_equipment_appendix_text(d)
    d["has_equipment_appendix"] = len(d.get("equipment_appendix", "")) > 0
    d["summons_appendix"] = _build_summons_appendix_text(d)
    d["has_summons_appendix"] = len(d.get("summons_appendix", "")) > 0

    return d


def mod_int(score):
    return (score - 10) // 2


def _build_condensed_features(d):
    lines = []
    feature_data = d.get("feature_data", []) or []
    usage_hints = {
        "divine sense": "1+CHA/day", "lay on hands": f"{d.get('level', 1) * 5} HP pool",
        "channel divinity": "1/rest", "second wind": "1d10+Lvl", "action surge": "1/rest",
        "rage": f"{2 + (d.get('level', 1) - 1) // 4}/day",
        "bardic inspiration": f"{d.get('cha_mod', 0)}/day", "wild shape": "2/rest",
        "ki": f"{d.get('level', 1)} pts", "sneak attack": f"{(d.get('level', 1) + 1) // 2}d6",
        "divine smite": "spell slots",
    }
    for fd in feature_data:
        name = fd.get("name", "")
        if not name:
            continue
        key = name.lower()
        hint = ""
        for k, v in usage_hints.items():
            if k in key:
                hint = f" ({v})"
                break
        lines.append(f"• {name}{hint}")
    features = d.get("features", []) or []
    seen = {fd.get("name", "").lower() for fd in feature_data}
    for f in features:
        if isinstance(f, str) and ":" in f:
            name_part = f.split(":", 1)[1].strip().split("|")[0].strip()
            if name_part.lower() not in seen:
                lines.append(f"• {name_part}")
                seen.add(name_part.lower())
    return "\n".join(lines)


def _build_full_feature_text(d):
    lines = []
    feature_data = d.get("feature_data", []) or []
    for fd in feature_data:
        name = fd.get("name", "")
        if not name:
            continue
        
        feat_title = name.upper()
        
        # If ASI has a chosen feat, show that instead of generic description
        asi_feat_name = fd.get("asi_feat_name", "")
        asi_feat_desc = fd.get("asi_feat_desc", "")
        
        if asi_feat_name:
            # Use the feat name as title, not "Ability Score Improvement"
            feat_title = f"{name} — {asi_feat_name}".upper()
        
        lines.append(feat_title)
        
        desc = fd.get("description", "")
        
        if asi_feat_name and asi_feat_desc:
            # Show the feat description instead of the generic ASI text
            lines.append(asi_feat_desc)
        elif desc:
            lines.append(desc)
        
        # Append actual chosen values for Magic Initiate etc.
        mi = fd.get("magic_initiate", {})
        if mi and isinstance(mi, dict):
            parts = []
            cls = mi.get("class", "")
            if cls:
                parts.append(f"Class: {cls}")
            cantrips = mi.get("cantrips", [])
            if cantrips:
                parts.append(f"Cantrips: {', '.join(cantrips)}")
            spell = mi.get("spell", "")
            if spell:
                parts.append(f"1st-level spell: {spell}")
            sa = mi.get("spellcasting_ability", "")
            if sa:
                parts.append(f"Spellcasting ability: {sa}")
            if parts:
                lines.append("")
                for p in parts:
                    lines.append(p)
        
        # Any sub-options (e.g. Channel Divinity choices, Fighting Style)
        sub_opts = fd.get("sub_options", "")
        if sub_opts:
            if isinstance(sub_opts, list):
                for opt in sub_opts:
                    if isinstance(opt, dict):
                        oname = opt.get("name", "")
                        odesc = opt.get("description", "")
                        # Skip sub-option that matches the parent feature core name
                        if oname and name:
                            parent_core = name.split(" |")[0].strip()
                            # Show only actual named sub-options (with ":" separator)
                            # Skip base-level duplicates like (1/rest), (2/rest)
                            if ":" not in oname.strip():
                                continue
                        if oname:
                            lines.append(oname.upper())
                        if odesc:
                            lines.append(odesc)
                    else:
                        lines.append(str(opt))
            elif isinstance(sub_opts, str):
                lines.append(sub_opts)
        
        lines.append("")
    return "\n".join(lines)


def _build_page1_features_text(d):
    """Feature name + description for page 1 Features & Traits box."""
    lines = []
    feature_data = d.get("feature_data", []) or []
    for fd in feature_data:
        name = fd.get("name", "")
        desc = fd.get("description", "")
        if not name:
            continue
        lines.append(name.upper())
        if desc:
            # Take first ~500 chars; break at sentence boundary
            short = desc[:500]
            last_period = max(short.rfind("."), short.rfind("…"))
            if last_period > 100:
                short = short[:last_period + 1]
            lines.append(short)
        lines.append("")
    return "\n".join(lines)


def _build_spell_short_desc(spell_name):
    """Build a compact one-liner from spell cache: '1d8 fire, 120 ft, conc'"""
    cache = _get_spell_cache()
    sd = cache.get((spell_name or "").lower(), {})
    if not sd:
        return ""
    
    parts = []
    desc = " ".join(sd.get("desc", [])) if isinstance(sd.get("desc"), list) else ""
    desc_lower = desc.lower()
    
    # Extract damage / heal dice from description
    import re
    # Pattern: "1d8 fire damage", "d4", "2d6 healing", etc.
    dice_match = re.search(r'((?:\d+)?d\d+(?:\s*[\+\-]\s*(?:\d+|your spellcasting|your \w+ \w+))?)', desc_lower)
    if dice_match:
        parts.append(dice_match.group(1))
    
    # Damage type
    for dtype in ['fire', 'cold', 'lightning', 'thunder', 'acid', 'poison', 'necrotic', 
                  'radiant', 'force', 'psychic', 'bludgeoning', 'piercing', 'slashing']:
        if dtype in desc_lower:
            parts.append(dtype)
            break
    
    # Range
    range_str = sd.get("range", "")
    if range_str and range_str.lower() not in ("self", "special", "unlimited", "sight"):
        parts.append(range_str.lower())
    elif "touch" in range_str.lower():
        parts.append("touch")
    
    # Concentration
    if sd.get("concentration"):
        parts.append("conc")
    
    # Ritual
    if sd.get("ritual"):
        parts.append("ritual")
    
    # Save if not a straight attack
    if "saving throw" in desc_lower:
        # Look for "Dexterity saving throw", "a DC X Wisdom saving throw", etc.
        save_match = re.search(r'\b(Dexterity|Strength|Constitution|Intelligence|Wisdom|Charisma)\s+saving throw', 
                              desc, re.IGNORECASE)
        if save_match:
            parts.append(save_match.group(1)[:3].upper() + " save")
    
    return ", ".join(parts[:4]) if parts else ""


def _extract_mechanical_summary(desc, max_chars=300):
    """Extract the most mechanically-relevant sentence from a description.
    Scores sentences by: dice (NdN, dN) > DC/AC > choose/select > numbers."""
    if not desc:
        return ""
    # Split into sentences
    sentences = []
    current = ""
    for ch in desc:
        current += ch
        if ch in ".!?" and len(current) > 20:
            sentences.append(current.strip())
            current = ""
    if current.strip():
        sentences.append(current.strip())
    if not sentences:
        return desc[:max_chars]

    import re
    def score(s):
        s_lower = s.lower()
        points = 0
        # Dice expressions: 1d6, 2d8, d20, 1d10+Lvl, etc.
        if re.search(r'\b\d*d\d+\b', s):
            points += 10
        # DC / save references
        if re.search(r'\bDC\b', s) or 'save dc' in s_lower or 'saving throw' in s_lower:
            points += 8
        # AC references
        if re.search(r'\bAC\b', s) or 'armor class' in s_lower:
            points += 7
        # HP, healing, damage — strong mechanical
        if any(w in s_lower for w in ('hit points', 'hit point', 'damage', 'heal', 'regain')):
            points += 6
        # Math expressions: "2 + spell level", "equal to your", etc.
        if re.search(r'\d+\s*[\+\-\*]\s*', s):
            points += 5
        # Selection / choices
        if any(w in s_lower for w in ('choose', 'select', 'pick one', 'your choice')):
            points += 5
        # Numerical ranges with units
        if re.search(r'\b\d+[-\s]*(ft|feet|minutes?|hours?|rounds?|level)', s_lower):
            points += 3
        # "you can" — active ability, better than pure flavor
        if 'you can' in s_lower:
            points += 1
        # Bonus for having any number at all
        if re.search(r'\b\d+\b', s):
            points += 1
        # Penalize pure flavor openers
        if s_lower.startswith(('beginning at', 'starting at', 'during your', 'you know how', 'also starting')):
            points -= 3
        return points

    scored = [(score(s), s) for s in sentences]
    # Sort by score desc, then by length desc (prefer meatier sentences at same score)
    scored.sort(key=lambda x: (-x[0], -len(x[1])))

    # Take the best sentence, cap at max_chars
    best = scored[0][1]
    if len(best) > max_chars:
        best = best[:max_chars]
        # Try to break at last period
        last_period = max(best.rfind("."), best.rfind("…"))
        if last_period > 40:
            best = best[:last_period + 1]
    return best


def _build_short_features_text(d):
    """Feature name + best mechanical sentence + usage hint."""
    # Usage hints for dice/pools (same as condensed_features)
    usage_hints = {
        "divine sense": "1+CHA/day", "lay on hands": "%d HP pool" % (d.get('level', 1) * 5),
        "channel divinity": "1/rest", "second wind": "1d10+Lvl", "action surge": "1/rest",
        "rage": "%d/day" % (2 + (d.get('level', 1) - 1) // 4),
        "bardic inspiration": "%d/day" % d.get('cha_mod', 0), "wild shape": "2/rest",
        "ki": "%d pts" % d.get('level', 1),
        "sneak attack": "%dd6" % ((d.get('level', 1) + 1) // 2),
        "divine smite": "spell slots",
    }
    lines = []
    feature_data = d.get("feature_data", []) or []
    for fd in feature_data:
        name = fd.get("name", "")
        desc = fd.get("description", "")
        if not name:
            continue
        # Look up usage hint
        key = name.lower()
        hint = ""
        for k, v in usage_hints.items():
            if k in key:
                hint = " [%s]" % v
                break
        if desc:
            summary = _extract_mechanical_summary(desc)
            lines.append("%s%s: %s" % (name, hint, summary))
        else:
            lines.append(name + hint)
        lines.append("")
    return "\n".join(lines)


def _build_spell_appendix_text(d):
    """Build full spell details for the appendix."""
    spells = d.get("spells", [])
    if not spells:
        return ""
    cache = _get_spell_cache()
    lines = []
    # Sort by level then name
    sorted_spells = sorted(spells, key=lambda s: (s[1], s[0].lower()))
    for sp_name, sp_level, prepared in sorted_spells:
        sd = cache.get(sp_name.lower(), {})
        if not sd:
            lines.append(sp_name.upper())
            lines.append(f"Level {sp_level} — (details not in cache)")
            lines.append("")
            continue

        lines.append(sp_name.upper())
        # Level + school line
        school_raw = sd.get("school", {})
        school = school_raw.get("name", school_raw) if isinstance(school_raw, dict) else school_raw
        ritual = " (ritual)" if sd.get("ritual") else ""
        level_str = {0: "Cantrip", 1: "1st-level", 2: "2nd-level", 3: "3rd-level"}.get(
            sp_level, f"{sp_level}th-level")
        lines.append(f"{level_str} {school}{ritual}")

        lines.append(f"Casting Time: {sd.get('casting_time', '—')}")
        lines.append(f"Range: {sd.get('range', '—')}")
        raw_comp = sd.get("components", [])
        if isinstance(raw_comp, list):
            comp_str = ", ".join(raw_comp) if raw_comp else "—"
            materials = sd.get("material")
            if materials:
                comp_str += f" ({materials})"
        else:
            comp_str = raw_comp if raw_comp else "—"
        lines.append(f"Components: {comp_str}")
        dur = sd.get("duration", "—")
        if sd.get("concentration"):
            dur_clean = dur.lower()
            if dur_clean.startswith("concentration"):
                # Already has concentration prefix (manual data format)
                pass
            elif dur_clean.startswith("up to "):
                dur = f"Concentration, {dur}"
            else:
                dur = f"Concentration, up to {dur}"
        lines.append(f"Duration: {dur}")

        # Description — handle both list (SRD) and string (manual) formats
        desc = sd.get("desc", sd.get("description", ""))
        full_desc = ""
        if desc:
            lines.append("")
            if isinstance(desc, list):
                full_desc = " ".join(desc)
            else:
                full_desc = desc
            # Check if "At Higher Levels" is embedded in the description
            if "at higher levels" in full_desc.lower():
                idx = full_desc.lower().index("at higher levels")
                main = full_desc[:idx].strip()
                higher_text = full_desc[idx:].strip()
                if main:
                    lines.append(main)
                if higher_text:
                    lines.append("")
                    lines.append(higher_text)
            else:
                lines.append(full_desc)

        # Separate higher_level field (SRD format)
        higher = sd.get("higher_level", [])
        if higher:
            if isinstance(higher, list):
                higher_text = " ".join(higher)
            else:
                higher_text = str(higher)
            if higher_text.strip():
                # Only add if not already appended via embedded detection
                if "at higher levels" not in (full_desc.lower() if desc else ""):
                    lines.append("")
                    lines.append(f"At Higher Levels: {higher_text}")

        lines.append("")
    return "\n".join(lines)


def _build_equipment_appendix_text(d):
    """Build full equipment descriptions for the appendix."""
    items = []
    for item in (d.get("equipped", []) or []):
        if isinstance(item, dict):
            items.append(item)
    for item in (d.get("inventory", []) or []):
        if isinstance(item, dict):
            items.append(item)

    lines = []
    seen = set()
    for item in items:
        name = item.get("name", "")
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())

        desc = _get_item_description(name)
        qty = item.get("qty", 1)
        header = name.upper()
        if qty > 1:
            header += f"  (x{qty})"
        lines.append(header)
        if desc:
            lines.append(desc)
        lines.append("")
    return "\n".join(lines)


def _build_summons_appendix_text(d):
    """Build full summon details for the appendix."""
    summons = d.get("summons", []) or []
    if isinstance(summons, str):
        import json
        summons = json.loads(summons) if summons else []
    if not summons:
        return ""

    # Load summon templates for feature descriptions
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from summon_templates import SUMMON_TEMPLATES
    except Exception:
        SUMMON_TEMPLATES = {}

    lines = []
    for summon in summons:
        name = summon.get("name", "Unnamed")
        lines.append(name.upper())

        # Size / type line
        size = summon.get("size", "")
        speed = summon.get("speed", "")
        s_type = summon.get("type", summon.get("category", ""))
        meta = []
        if size:
            meta.append(size)
        if speed:
            meta.append(speed)
        if meta:
            lines.append(" | ".join(meta))

        # Core stats
        ac = summon.get("ac", "—")
        hp = summon.get("hp_max", "—")
        hp_note = summon.get("hp_note", "")
        ac_str = f"Armor Class: {ac}"
        hp_str = f"Hit Points: {hp}"
        if hp_note:
            hp_str += f"  ({hp_note})"
        lines.append(ac_str)
        lines.append(hp_str)

        # Ability scores
        stats = summon.get("stats", {})
        if stats and isinstance(stats, dict):
            stat_line = f"STR {stats.get('str',0):>2d}  DEX {stats.get('dex',0):>2d}  CON {stats.get('con',0):>2d}  INT {stats.get('int',0):>2d}  WIS {stats.get('wis',0):>2d}  CHA {stats.get('cha',0):>2d}"
            lines.append(stat_line)

        # Skills + Senses
        skills = summon.get("skills", "")
        senses = summon.get("senses", "")
        if skills:
            lines.append(f"Skills: {skills}")
        if senses:
            lines.append(f"Senses: {senses}")

        # Look up template for feature descriptions
        source = summon.get("source", "")
        form = summon.get("form", "")
        sname = summon.get("name", "")
        template = None
        if source in SUMMON_TEMPLATES:
            template = SUMMON_TEMPLATES[source]
        elif form and form in SUMMON_TEMPLATES:
            template = SUMMON_TEMPLATES[form]
        # Try vehicle_{name} pattern (e.g. vehicle_airship)
        if not template:
            vehicle_key = f"vehicle_{sname.lower().replace(' ', '_')}"
            if vehicle_key in SUMMON_TEMPLATES:
                template = SUMMON_TEMPLATES[vehicle_key]
        # Try familiar_{name} pattern (e.g. familiar_hawk)
        if not template:
            familiar_key = f"familiar_{sname.lower()}"
            if familiar_key in SUMMON_TEMPLATES:
                template = SUMMON_TEMPLATES[familiar_key]

        # Features
        features = summon.get("features", [])
        if features and isinstance(features, list):
            lines.append("")
            for feat in features:
                feat_name = feat if isinstance(feat, str) else feat.get("name", "")
                if not feat_name:
                    continue
                lines.append(f"{feat_name}.")
                # Get description from template or from summon data
                desc = ""
                if template:
                    fdescs = template.get("feature_descs", {})
                    desc = fdescs.get(feat_name, "")
                if not desc:
                    desc = feat.get("description", "") if isinstance(feat, dict) else ""
                # Known common trait descriptions not in templates
                if not desc:
                    _KNOWN_TRAIT_DESCS = {
                        "Keen Sight": "The creature has advantage on Wisdom (Perception) checks that rely on sight.",
                        "Keen Hearing": "The creature has advantage on Wisdom (Perception) checks that rely on hearing.",
                        "Keen Smell": "The creature has advantage on Wisdom (Perception) checks that rely on smell.",
                        "Amphibious": "The creature can breathe air and water.",
                    }
                    desc = _KNOWN_TRAIT_DESCS.get(feat_name, "")
                if desc:
                    lines.append(desc)
                lines.append("")

        # Attacks
        attacks = summon.get("attacks", [])
        if attacks and isinstance(attacks, list):
            lines.append("Attacks:")
            for atk in attacks:
                atk_name = atk.get("name", "")
                atk_bonus = atk.get("bonus", atk.get("atk_bonus", ""))
                atk_dmg = atk.get("damage", "")
                atk_line = f"  {atk_name}"
                if atk_bonus:
                    atk_line += f"  +{atk_bonus}" if str(atk_bonus).isdigit() else f"  {atk_bonus}"
                if atk_dmg:
                    atk_line += f"  {atk_dmg}"
                lines.append(atk_line)
            lines.append("")

        # Spacer between summons
        lines.append("")
    return "\n".join(lines)


def trunc(text, max_len):
    text = str(text) if text else ""
    return text[:max_len]


# ═══════════════════════════════════════════════════════════════
#  DRAWING PRIMITIVES
# ═══════════════════════════════════════════════════════════════
def _label(c, x, y_tl, text, size=6):
    """Draw a section title label. Size default 6 (was 5, +25%)."""
    c.setFont(TITLE_FONT, size)
    c.drawString(x, yb(y_tl), str(text).upper())


def _value(c, x, y_tl, w, h, text, size=9, bold=True, center=False):
    y = yb(y_tl) - h
    c.setStrokeColor((0, 0, 0))
    c.rect(x, y, w, h)
    font = FONT_BOLD if bold else FONT
    c.setFont(font, size)
    c.setFillColor((0, 0, 0))
    txt = trunc(str(text), 40)
    if center:
        c.drawCentredString(x + w / 2, y + (h - size) / 2 + 2, txt)
    else:
        c.drawString(x + 3, y + (h - size) / 2 + 2, txt)


def _checkbox(c, x, y_tl, sz=8, checked=False):
    y = yb(y_tl) - sz
    c.setStrokeColor((0, 0, 0))
    c.rect(x, y, sz, sz)
    if checked:
        c.line(x + 1, y + 1, x + sz - 1, y + sz - 1)
        c.line(x + sz - 1, y + 1, x + 1, y + sz - 1)


def _bubble(c, x, y_tl, r=5, filled=False):
    cy = yb(y_tl)
    c.setStrokeColor((0, 0, 0))
    c.circle(x, cy, r)
    if filled:
        c.setFillColor((0, 0, 0))
        c.circle(x, cy, r, fill=1, stroke=0)


def _text_box(c, x, y_tl, w, h, text, size=6, label_text=None, min_size=4):
    """Fixed bounding box with auto-sizing. Shrinks font until all text fits, then draws."""
    y_bottom = yb(y_tl) - h
    c.setStrokeColor((0, 0, 0))
    c.rect(x, y_bottom, w, h)
    if label_text:
        c.setFont(TITLE_FONT, 5)
        c.setFillColor((0.2, 0.2, 0.2))
        c.drawString(x + 2, yb(y_tl) - 7, str(label_text).upper())
        c.setFillColor((0, 0, 0))
    if not text:
        return
    offset_top = 10 if label_text else 4
    # Try font sizes from size down to min_size, pick the biggest that fits all text
    chosen_size = size
    for try_size in range(size, min_size - 1, -1):
        line_h = try_size + 2
        max_lines = int((h - offset_top) / line_h)
        if max_lines < 1:
            continue
        lines = simpleSplit(str(text), FONT, try_size, w - 6)
        if len(lines) <= max_lines:
            chosen_size = try_size
            break
    # Draw at chosen size
    line_h = chosen_size + 2
    max_lines = int((h - offset_top) / line_h)
    if max_lines < 1:
        return
    lines = simpleSplit(str(text), FONT, chosen_size, w - 6)
    display = lines[:max_lines]
    if len(lines) > max_lines and max_lines > 1:
        display[-1] = display[-1][:int(w / 8)] + "… (cont.)"
    c.setFont(FONT, chosen_size)
    c.setFillColor((0, 0, 0))
    for i, line in enumerate(display):
        ly = yb(y_tl) - offset_top - (i + 1) * line_h
        c.drawString(x + 3, ly, line)


# ═══════════════════════════════════════════════════════════════
#  PAGE 1 COLUMN GRID — strict vertical encapsulation
#  Each column is rendered top-to-bottom COMPLETELY before the
#  X-coordinate advances to the next column.  No horizontal
#  interleaving across column boundaries.
# ═══════════════════════════════════════════════════════════════
PAGE_1_COLUMN_GRID = {
    "Column_1_Left": {
        "X_Boundary_Start": 45,
        "Vertical_Blocks": [
            "Ability Scores", "Saving Throws", "Skills List",
            "Passive Perception", "Other Proficiencies & Languages",
        ],
    },
    "Column_2_Center": {
        "X_Boundary_Start": 245,
        "Vertical_Blocks": [
            "Armor Class / Initiative / Speed", "Hit Points Widget",
            "Hit Dice & Death Saves", "Attacks & Spellcasting Box",
            "Equipment Box & Currency Stack",
        ],
    },
    "Column_3_Right": {
        "X_Boundary_Start": 445,
        "Vertical_Blocks": [
            "Personality Blocks (Traits, Ideals, Bonds, Flaws)",
            "Features & Traits Frame",
        ],
    },
}

COL1_X, COL1_W = 45, 140
COL2_X, COL2_W = 245, 180
COL3_X, COL3_W = 445, 150
GRID_Y = 165


# ═══════════════════════════════════════════════════════════════
#  PAGE 1 — 3-COLUMN GRID
# ═══════════════════════════════════════════════════════════════
def draw_page1(c, d):
    """Render page 1 in strict column-major order.
    
    Execution constraint: each column function locks its X-coordinate
    and draws ALL its vertical blocks top-to-bottom before returning.
    The X-coordinate NEVER steps to an adjacent column until the
    current column loop closes.
    """
    draw_header(c, d)                # spans all columns (row-major header)
    _draw_col1_stats(c, d)           # COL1 locked at X=45, top→bottom
    _draw_col2_combat(c, d)          # COL2 locked at X=245, top→bottom  
    _draw_col3_personality(c, d)     # COL3 locked at X=445, top→bottom


def draw_header(c, d):
    y0, box_h, gap = 30, 16, 38  # increased gap from 28 to 38
    # Row 1: Character Name | Race | Class & Level | Background
    for x, w, lbl, val in [
        (COL1_X, COL1_W, "Character Name", d.get("name", "")),
        (COL2_X, 100, "Race", d.get("race", "")),
        (COL2_X + 106, 90, "Class & Level", f"{d.get('class_name','')} {d.get('level','')}"),
        (COL3_X, COL3_W, "Background", d.get("background", "")),
    ]:
        _label(c, x, y0 - 9, lbl)
        _value(c, x, y0, w, box_h, val, size=8)
    # Row 2: Player Name | (empty) | Experience Points | Alignment
    y2 = y0 + gap
    for x, w, lbl, val in [
        (COL1_X, 76, "Player Name", ""),
        (COL1_X + 82, 54, "", ""),  # faction slot — leave blank
        (COL2_X, 90, "Experience Points", ""),
        (COL2_X + 100, COL2_W - 100, "Alignment", d.get("alignment", "")),
    ]:
        _label(c, x, y2 - 9, lbl)
        _value(c, x, y2, w, box_h, val, size=8)
    # Row 3
    y3 = y2 + gap
    _label(c, COL1_X, y3 - 9, "Inspiration")
    _checkbox(c, COL1_X + 4, y3 + 4, 8, checked=bool(d.get("inspiration", 0)))
    _label(c, COL1_X + 44, y3 - 9, "Proficiency Bonus")
    _value(c, COL1_X + 44, y3, 30, box_h, str(d.get("proficiency_bonus", 2)), size=10, center=True)


def _draw_col1_stats(c, d):
    y = GRID_Y
    abilities = [
        ("STR", "strength", d.get("str_mod", 0)), ("DEX", "dexterity", d.get("dex_mod", 0)),
        ("CON", "constitution", d.get("con_mod", 0)), ("INT", "intelligence", d.get("int_mod", 0)),
        ("WIS", "wisdom", d.get("wis_mod", 0)), ("CHA", "charisma", d.get("cha_mod", 0)),
    ]
    save_profs = [s.lower() for s in (d.get("save_proficiencies", []) or [])]
    prof = d.get("proficiency_bonus", 2)
    block_w, block_h = 56, 70
    sub_x = [COL1_X, COL1_X + 62]
    gap_y = 6
    for i, (abbr, key, mod) in enumerate(abilities):
        col = i % 2
        row_idx = i // 2
        x = sub_x[col]
        y_tl = y + row_idx * (block_h + gap_y)
        score = d.get(key, 10)
        is_prof = abbr.lower() in save_profs
        save_mod = mod + (prof if is_prof else 0)
        y_b = yb(y_tl) - block_h
        c.setStrokeColor((0, 0, 0))
        c.rect(x, y_b, block_w, block_h)
        c.setFont(FONT_BOLD, 6)
        c.drawCentredString(x + block_w / 2, yb(y_tl + 8), abbr)
        c.setFont(FONT_BOLD, 15)
        c.drawCentredString(x + 20, yb(y_tl + 36), str(score))
        mx, mw = x + 34, 20
        c.rect(mx, yb(y_tl + 14) - 24, mw, 24)
        c.setFont(FONT_BOLD, 8)
        c.drawCentredString(mx + mw / 2, yb(y_tl + 36), f"{mod:+d}")
        c.setFont(FONT, 3.5)
        c.drawString(x + 2, yb(y_tl + 54), "SAVE")
        _checkbox(c, x + 29, y_tl + 48, 7, checked=is_prof)
        c.setFont(FONT_BOLD, 6)
        c.drawCentredString(mx + mw / 2, yb(y_tl + 58), f"{save_mod:+d}")

    # Skills
    y_skills = y + 3 * (block_h + gap_y) + 14  # increased from +4
    skills = d.get("skills", []) or []
    scores_map = {"strength": d.get("strength", 10), "dexterity": d.get("dexterity", 10),
                  "constitution": d.get("constitution", 10), "intelligence": d.get("intelligence", 10),
                  "wisdom": d.get("wisdom", 10), "charisma": d.get("charisma", 10)}
    abbr_map = {"Str": "strength", "Dex": "dexterity", "Con": "constitution",
                 "Int": "intelligence", "Wis": "wisdom", "Cha": "charisma"}
    all_skills = [
        ("Acrobatics", "Dex"), ("Animal Handling", "Wis"), ("Arcana", "Int"),
        ("Athletics", "Str"), ("Deception", "Cha"), ("History", "Int"),
        ("Insight", "Wis"), ("Intimidation", "Cha"), ("Investigation", "Int"),
        ("Medicine", "Wis"), ("Nature", "Int"), ("Perception", "Wis"),
        ("Performance", "Cha"), ("Persuasion", "Cha"), ("Religion", "Int"),
        ("Sleight of Hand", "Dex"), ("Stealth", "Dex"), ("Survival", "Wis"),
    ]
    row_h = 14
    _label(c, COL1_X, y_skills - 9, "Skills")
    for i, (name, abbr) in enumerate(all_skills):
        col = i // 9
        row_idx = i % 9
        sx = COL1_X + col * 72
        sy = y_skills + row_idx * row_h
        is_prof = name in skills
        ab_mod = mod_int(scores_map[abbr_map[abbr]])
        skill_mod = ab_mod + (prof if is_prof else 0)
        _checkbox(c, sx, sy + 3, 6, checked=is_prof)
        c.setFont(FONT, 5)
        c.drawString(sx + 8, yb(sy + row_h - 3), f"{name} ({abbr})")
        c.setFont(FONT_BOLD, 5.5)
        c.drawRightString(sx + 66, yb(sy + row_h - 3), f"{skill_mod:+d}")

    # Passive Perception
    y_pp = y_skills + 9 * row_h + 16  # increased from +4
    _label(c, COL1_X, y_pp - 9, "Passive Perception")
    _value(c, COL1_X, y_pp, 40, 16, str(d.get("passive_perception", 10)), size=9, center=True)

    # Other Proficiencies
    y_prof = y_pp + 34  # increased from +28
    parts = []
    for lbl, field in [("Weapons", "weapon_proficiencies"), ("Armor", "armor_proficiencies"),
                        ("Tools", "tool_proficiencies"), ("Languages", "languages")]:
        items = d.get(field, []) or []
        if items:
            parts.append(f"{lbl}: {', '.join(items[:6])}")
    _text_box(c, COL1_X, y_prof, COL1_W, 60, "\n".join(parts), size=4.5, label_text="Other Proficiencies")


def _draw_col2_combat(c, d):
    y = GRID_Y
    # AC/Init/Speed
    _label(c, COL2_X, y - 9, "Armor Class")
    _value(c, COL2_X, y, 50, 20, str(d.get("ac", 10)), size=11, center=True)
    _label(c, COL2_X + 56, y - 9, "Initiative")
    _value(c, COL2_X + 56, y, 44, 20, f"{d.get('initiative', 0):+d}", size=9, center=True)
    _label(c, COL2_X + 106, y - 9, "Speed")
    _value(c, COL2_X + 106, y, 44, 20, str(d.get("speed", 30)), size=9, center=True)
    # HP
    y_hp = y + 44  # increased from +36
    _label(c, COL2_X, y_hp - 9, "Hit Point Maximum")
    _value(c, COL2_X, y_hp, 60, 20, str(d.get("hp_max", 10)), size=11, center=True)
    _label(c, COL2_X + 66, y_hp - 9, "Current HP")
    _value(c, COL2_X + 66, y_hp, 60, 20, str(d.get("hp_current", 10)), size=11, center=True)
    _label(c, COL2_X + 132, y_hp - 9, "Temp HP")
    _value(c, COL2_X + 132, y_hp, 44, 20, str(d.get("temp_hp", 0)), size=9, center=True)
    # Hit Dice + Death Saves
    y_hd = y_hp + 38  # increased from +32
    _label(c, COL2_X, y_hd - 9, "Hit Dice")
    hd_type = d.get("hit_dice_type", "1d8")
    total = d.get("hit_dice_total", 1)
    used = d.get("hit_dice_used", 0)
    _value(c, COL2_X, y_hd, 62, 16, f"{total - used}/{total} {hd_type}", size=7, center=True)
    _label(c, COL2_X + 68, y_hd - 9, "Death Saves")
    c.setFont(FONT, 4)
    c.drawString(COL2_X + 68, yb(y_hd + 6), "SUCCESS")
    succ = d.get("death_saves_success", 0) or 0
    for i in range(3):
        _bubble(c, COL2_X + 108 + i * 16, y_hd + 6, 5, filled=(i < succ))
    c.drawString(COL2_X + 68, yb(y_hd + 20), "FAILURES")
    fail = d.get("death_saves_fail", 0) or 0
    for i in range(3):
        _bubble(c, COL2_X + 108 + i * 16, y_hd + 20, 5, filled=(i < fail))
    # Attacks
    y_atk = y_hd + 52  # increased from +44
    _label(c, COL2_X, y_atk - 9, "Attacks & Spellcasting (see Appendix)")
    col_w = [80, 34, 54]
    for j, (h, cw) in enumerate(zip(["Name", "Atk", "Damage/Type"], col_w)):
        cx = COL2_X + sum(col_w[:j])
        ry = yb(y_atk) - 12
        c.setFont(FONT_BOLD, 4.5)
        c.rect(cx, ry, cw, 12)
        c.drawString(cx + 2, ry + 3, h)
    attacks = d.get("attacks_data", []) or []
    for ri in range(5):
        ry_tl = y_atk + 12 + ri * 15
        atk = attacks[ri] if ri < len(attacks) else {}
        vals = [atk.get("name", ""),
                f"{atk.get('attack_bonus', 0):+d}" if atk.get("attack_bonus") is not None else "",
                atk.get("damage", "")]
        for j, (v, cw) in enumerate(zip(vals, col_w)):
            cx = COL2_X + sum(col_w[:j])
            ry = yb(ry_tl) - 15
            c.setFont(FONT, 5.5)
            c.rect(cx, ry, cw, 15)
            c.drawString(cx + 2, ry + 3, trunc(str(v), 20))
    y_cur = y_atk + 12 + 5 * 15 + 22  # increased from +14
    # Currency — separate section above Equipment
    _label(c, COL2_X, y_cur - 9, "Currency")
    coins = [("CP", d.get("cp", 0)), ("SP", 0), ("EP", 0), ("GP", d.get("gp", 0)), ("PP", 0)]
    coin_w, coin_h = 30, 18
    for i, (cn, cv) in enumerate(coins):
        cx = COL2_X + i * (coin_w + 2)
        # Label
        by = yb(y_cur) - coin_h
        c.setFillColor((0.88, 0.88, 0.88))
        c.rect(cx, by, coin_w, coin_h, fill=1, stroke=1)
        c.setFillColor((0, 0, 0))
        c.setStrokeColor((0, 0, 0))
        c.setFont(FONT_BOLD, 4.5)
        c.drawCentredString(cx + coin_w / 2, by + coin_h - 7, cn)
        c.setFont(FONT, 6)
        c.drawCentredString(cx + coin_w / 2, by + 2, str(cv))
    # Equipment — short descriptions (full details in Equipment Appendix)
    y_eq = y_cur + 40
    _label(c, COL2_X, y_eq - 9, "Equipment (see Appendix)")
    eq_box_h = int(yb(y_eq) - yb(PAGE_H - 40))
    items = []
    for item in (d.get("equipped", []) or []):
        if isinstance(item, dict):
            name = item.get("name", "")
            qty = item.get("qty", 1)
            if qty > 1:
                name = f"{name} x{qty}"
            desc = _get_item_description(name)
            if desc:
                # First sentence or ~80 chars
                period = desc.find('.')
                if 0 < period < 100:
                    desc = desc[:period + 1]
                else:
                    desc = desc[:80]
                items.append(f"[E] {name}: {desc}")
            else:
                items.append(f"[E] {name}")
    for item in (d.get("inventory", []) or []):
        if isinstance(item, dict):
            name = item.get("name", "")
            qty = item.get("qty", 1)
            if qty > 1:
                name = f"{name} x{qty}"
            desc = _get_item_description(name)
            if desc:
                period = desc.find('.')
                if 0 < period < 100:
                    desc = desc[:period + 1]
                else:
                    desc = desc[:80]
                items.append(f"{name}: {desc}")
            else:
                items.append(name)
    eq_text = "\n".join(items)
    c.setStrokeColor((0, 0, 0))
    c.rect(COL2_X, yb(y_eq) - eq_box_h, COL2_W - 4, eq_box_h)
    if eq_text:
        eq_lines = simpleSplit(eq_text, FONT, 5, COL2_W - 10)
        max_eq = int(eq_box_h / 9)
        c.setFont(FONT, 5)
        c.setFillColor((0, 0, 0))
        for i, line in enumerate(eq_lines[:max_eq]):
            c.drawString(COL2_X + 4, yb(y_eq) - 10 - i * 9, line)


def _draw_col3_personality(c, d):
    """Right column — starts just below Background box."""
    y = 60  # right below header Background at y=46
    block_h, gap = 50, 12
    w = COL3_W
    for i, (lbl, txt) in enumerate([
        ("Personality Traits", d.get("personality_traits", "")),
        ("Ideals", d.get("ideals", "")),
        ("Bonds", d.get("bonds", "")),
        ("Flaws", d.get("flaws", "")),
    ]):
        sy = y + i * (block_h + gap)
        _text_box(c, COL3_X, sy, w, block_h, txt, size=5, label_text=lbl)
    y_feat = y + 4 * (block_h + gap) + 14
    feat_h = PAGE_H - 36 - y_feat
    _text_box(c, COL3_X, y_feat, w, feat_h, d.get("short_features", ""), size=4.5,
              label_text="Features & Traits (see Appendix)")


# ═══════════════════════════════════════════════════════════════
#  PAGE 2 — NARRATIVE LAYOUT (5 locked blocks, NO feature text)
# ═══════════════════════════════════════════════════════════════
def draw_page2(c, d):
    y0 = 30
    # Physical properties row
    phys_w = 92
    for i, (lbl, val) in enumerate([
        ("Age", d.get("age", "")), ("Height", d.get("height", "")), ("Weight", d.get("weight", "")),
        ("Eyes", d.get("eyes", "")), ("Skin", d.get("skin", "")), ("Hair", d.get("hair", "")),
    ]):
        px = COL1_X + i * (phys_w + 4)
        _label(c, px, y0 - 9, lbl)
        _value(c, px, y0, phys_w, 16, val, size=8)
    y2 = y0 + 32
    left_x, left_w = COL1_X, 280
    right_x, right_w = left_x + left_w + 16, 260

    # Left: Character Appearance, Character Backstory
    _appearance_lines = []
    for fld in ("age", "height", "weight", "eyes", "skin", "hair"):
        v = d.get(fld, "")
        if v:
            _appearance_lines.append(f"{fld.capitalize()}: {v}")
    _appearance_txt = ", ".join(_appearance_lines) if _appearance_lines else d.get("race", "") or ""
    _text_box(c, left_x, y2, left_w, 280, _appearance_txt, size=5, label_text="Character Appearance")
    _text_box(c, left_x, y2 + 290, left_w, 400, d.get("backstory", ""), size=5,
              label_text="Character Backstory")

    # Right: Allies & Organizations, Additional Features & Traits, Treasure
    _text_box(c, right_x, y2, right_w, 180, d.get("allies", ""), size=5, label_text="Allies & Organizations")
    # Faction symbol square
    sym_x, sym_y, sym_sz = right_x + right_w - 48, y2 + 8, 44
    c.setStrokeColor((0, 0, 0))
    c.rect(sym_x, yb(sym_y) - sym_sz, sym_sz, sym_sz)

    _text_box(c, right_x, y2 + 190, right_w, 310, "", size=4.5,
              label_text="Additional Features & Traits")
    _text_box(c, right_x, y2 + 510, right_w, 150, "", size=5, label_text="Treasure")


# ═══════════════════════════════════════════════════════════════
#  PAGE 3 — SPELL MATRIX (3 columns: 1-2 | 3-5 | 6-9)
# ═══════════════════════════════════════════════════════════════
def draw_page3(c, d):
    x0 = MARGIN
    y = 22

    # Header
    sa = d.get("spell_ability", "")
    dc = d.get("spell_save_dc", 10)
    atk = d.get("spell_attack_bonus", 0)
    c.setFont(FONT_BOLD, 9)
    c.drawString(x0, yb(y + 14), f"SPELLCASTING ABILITY: {sa}")
    c.drawString(x0 + 190, yb(y + 14), f"SPELL SAVE DC: {dc}")
    c.drawString(x0 + 340, yb(y + 14), f"SPELL ATTACK BONUS: {atk:+d}")
    y += 28

    spells = d.get("spells", [])
    by_level = {}
    for sp_name, sp_level, prepared in spells:
        by_level.setdefault(sp_level, []).append((sp_name, prepared))
    spell_slots = d.get("spell_slots", {})
    slot_used = d.get("spell_slots_used", {})

    # Cantrips
    cantrips = by_level.get(0, [])
    if cantrips:
        _label(c, x0, y, "CANTRIPS (0-LEVEL)")
        for i, (name, _) in enumerate(cantrips[:12]):
            c.setFont(FONT, 5.5)
            c.drawString(x0 + 8, yb(y + 10 + i * 11), name)
        y += 10 + min(len(cantrips), 12) * 11 + 6

    # Column layout
    col_layout = [
        (45, [1, 2]),
        (245, [3, 4, 5]),
        (445, [6, 7, 8, 9]),
    ]
    col_w = 160
    y_start = y + 6

    # Track overflow across all levels
    overflow = {}  # level -> list of remaining (name, prepared)
    cursors = {}   # level -> next start index

    for cx, levels in col_layout:
        c.setFillColor((0.97, 0.97, 0.97) if cx > 45 else (1, 1, 1))
        c.setStrokeColor((0, 0, 0))
        c.rect(cx, yb(PAGE_H - MARGIN), col_w, (PAGE_H - MARGIN) - yb(y_start), fill=1, stroke=0)
        c.setFillColor((0, 0, 0))

        if cx > 45:
            c.setStrokeColor((0.5, 0.5, 0.5))
            c.setDash(2, 4)
            c.line(cx - 4, yb(y_start), cx - 4, yb(PAGE_H - MARGIN))
            c.setDash()
            c.setStrokeColor((0, 0, 0))

        cards_in_col = len(levels)
        available_h = PAGE_H - y_start - MARGIN
        card_h = (available_h - (cards_in_col - 1) * 4) / cards_in_col

        for i, lvl in enumerate(levels):
            spells_list = by_level.get(lvl, [])
            if not spells_list:
                continue
            cy_tl = y_start + i * (card_h + 4)
            start = cursors.get(lvl, 0)
            next_idx, ovf = _render_spell_card(c, cx, cy_tl, col_w, card_h, lvl,
                                spells_list, spell_slots, slot_used, start_idx=start)
            cursors[lvl] = next_idx
            if ovf:
                overflow[lvl] = ovf

    # Overflow pages — full-width cards for levels with remaining spells
    while overflow:
        c.showPage()
        y = 30
        c.setFont(FONT_BOLD, 10)
        c.drawString(MARGIN, yb(y + 14), f"SPELLS (continued) — {d.get('name', '')}")
        c.setFont(FONT, 6)
        c.drawString(MARGIN, yb(y + 26), f"{d.get('class_name', '')} {d.get('level', '')}")
        y += 36

        full_w = PAGE_W - 2 * MARGIN
        # Calculate card heights: all overflow levels share the page
        ovf_levels = sorted(overflow.keys())
        ovf_card_h = (PAGE_H - y - MARGIN - (len(ovf_levels) - 1) * 6) / len(ovf_levels)

        new_overflow = {}
        for i, lvl in enumerate(ovf_levels):
            spells_remaining = overflow[lvl]
            cy_tl = y + i * (ovf_card_h + 6)
            next_idx, ovf2 = _render_spell_card(c, MARGIN, cy_tl, full_w, ovf_card_h, lvl,
                                spells_remaining, spell_slots, slot_used, start_idx=0)
            if ovf2:
                new_overflow[lvl] = ovf2
        overflow = new_overflow


def _render_spell_card(c, x, y_tl, w, h, level, spells_list, spell_slots, slot_used, 
                      start_idx=0, compact=True):
    """Render one self-contained spell level card.
    If compact=True, uses space-efficient layout (combined header, inline bubbles).
    Returns (next_start_idx, overflow_spells) if spells don't all fit."""
    # Card border
    c.setStrokeColor((0, 0, 0))
    c.rect(x, yb(y_tl) - h, w, h)

    total_slots = int(spell_slots.get(str(level), 0))
    used_slots = int(slot_used.get(str(level), 0))
    
    if compact:
        # Compact header: "1st LEVEL — 4 slots, 2 used"
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(level, "th")
        header = "%d%s LEVEL — %d slots" % (level, suffix, total_slots)
        if used_slots:
            header += ", %d used" % used_slots
        c.setFont(FONT_BOLD, 5.5)
        c.drawString(x + 4, yb(y_tl + 7), header)
        
        # Inline slot bubbles (same line as header, right-aligned)
        for b in range(min(total_slots, 8)):
            bx = x + w - 14 - (total_slots - 1 - b) * 13
            _bubble(c, bx, y_tl + 1, 3, filled=(b < used_slots))
        
        line_y = y_tl + 14  # spells start here
        line_h = 10  # tighter line spacing
    else:
        # Legacy full layout
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(level, "th")
        c.setFont(FONT_BOLD, 6)
        c.drawString(x + 4, yb(y_tl + 8), f"{level}{suffix} LEVEL")
        c.setFont(FONT_BOLD, 5)
        c.drawString(x + 4, yb(y_tl + 20), "SLOTS TOTAL:")
        _value(c, x + 60, y_tl + 12, 24, 12, str(total_slots), size=7, center=True)
        c.drawString(x + 92, yb(y_tl + 20), "SLOTS EXPENDED:")
        _value(c, x + 160, y_tl + 12, 24, 12, str(used_slots), size=7, center=True)
        for b in range(min(total_slots, 12)):
            bx = x + 6 + (b % 6) * 12
            by = y_tl + 30 + (b // 6) * 12
            _bubble(c, bx + 4, by + 4, 3.5, filled=(b < used_slots))
        line_y = y_tl + 54
        if total_slots > 6:
            line_y += 12
        line_h = 12

    max_lines = int((h - (line_y - y_tl) - 4) / line_h)
    visible_spells = spells_list[start_idx:start_idx + max_lines]
    overflow = spells_list[start_idx + max_lines:] if len(spells_list) > start_idx + max_lines else []
    
    for i, (sp_name, prepared) in enumerate(visible_spells):
        ly = line_y + i * line_h
        _bubble(c, x + 8, ly + 2, 3, filled=prepared)
        ln_y = yb(ly + 2)
        c.setStrokeColor((0.7, 0.7, 0.7))
        c.line(x + 14, ln_y, x + w - 4, ln_y)
        c.setStrokeColor((0, 0, 0))
        c.setFont(FONT_BOLD if prepared else FONT, 5)
        # Spell name + short description
        short = _build_spell_short_desc(sp_name)
        label = "%s — %s" % (sp_name, short) if short else sp_name
        c.drawString(x + 16, ln_y + 1, trunc(label, 48))

    # Fill remaining lines with empty prep circles + ruled lines
    for i in range(len(visible_spells), max_lines):
        ly = line_y + i * line_h
        _bubble(c, x + 8, ly + 2, 3, filled=False)
        ln_y = yb(ly + 2)
        c.setStrokeColor((0.7, 0.7, 0.7))
        c.line(x + 14, ln_y, x + w - 4, ln_y)
        c.setStrokeColor((0, 0, 0))
    
    return start_idx + len(visible_spells), overflow


# ═══════════════════════════════════════════════════════════════
#  PAGE 4 — CLASS FEATURE APPENDIX (only when features overflow)
# ═══════════════════════════════════════════════════════════════
def draw_page4_appendix(c, d):
    y = 30
    c.setFont(FONT_BOLD, 12)
    c.drawString(MARGIN, yb(y + 16), f"CLASS FEATURE APPENDIX — {d.get('name', '')}")
    c.setFont(FONT, 7)
    c.drawString(MARGIN, yb(y + 28), f"{d.get('class_name', '')} {d.get('level', '')} | {d.get('race', '')} | {d.get('background', '')}")
    y += 44
    text = d.get("full_feature_text", "")
    if not text:
        return
    paragraphs = text.split("\n\n")
    for para in paragraphs:
        if not para.strip():
            continue
        lines = simpleSplit(para, FONT, 6, PAGE_W - 2 * MARGIN)
        needed_h = len(lines) * 10 + 20
        if y + needed_h > PAGE_H - MARGIN:
            c.showPage()
            y = MARGIN
        if "\n" not in para and para.isupper():
            c.setFont(FONT_BOLD, 7)
            c.drawString(MARGIN, yb(y + 12), para)
            y += 16
        else:
            for line in lines:
                if y + 10 > PAGE_H - MARGIN:
                    c.showPage()
                    y = MARGIN
                c.setFont(FONT, 6)
                c.drawString(MARGIN, yb(y + 10), line)
                y += 10
            y += 6


# ═══════════════════════════════════════════════════════════════
#  SPELL APPENDIX — full spell details
# ═══════════════════════════════════════════════════════════════
def draw_spell_appendix(c, d):
    text = d.get("spell_appendix", "")
    if not text:
        return
    y = 30
    c.setFont(FONT_BOLD, 12)
    c.drawString(MARGIN, yb(y + 16), f"SPELL APPENDIX — {d.get('name', '')}")
    c.setFont(FONT, 7)
    c.drawString(MARGIN, yb(y + 28),
                 f"{d.get('class_name', '')} {d.get('level', '')} | {d.get('race', '')} | {d.get('background', '')}")
    y += 44
    paragraphs = text.split("\n\n")
    for para in paragraphs:
        if not para.strip():
            continue
        lines = simpleSplit(para, FONT, 6, PAGE_W - 2 * MARGIN)
        needed_h = len(lines) * 10 + 20
        if y + needed_h > PAGE_H - MARGIN:
            c.showPage()
            y = MARGIN
        if "\n" not in para and para.isupper():
            c.setFont(FONT_BOLD, 7)
            c.drawString(MARGIN, yb(y + 12), para)
            y += 16
        else:
            for line in lines:
                if y + 10 > PAGE_H - MARGIN:
                    c.showPage()
                    y = MARGIN
                c.setFont(FONT, 6)
                c.drawString(MARGIN, yb(y + 10), line)
                y += 10
            y += 6


# ═══════════════════════════════════════════════════════════════
#  EQUIPMENT APPENDIX — full equipment descriptions
# ═══════════════════════════════════════════════════════════════
def draw_equipment_appendix(c, d):
    text = d.get("equipment_appendix", "")
    if not text:
        return
    y = 30
    c.setFont(FONT_BOLD, 12)
    c.drawString(MARGIN, yb(y + 16), f"EQUIPMENT APPENDIX — {d.get('name', '')}")
    c.setFont(FONT, 7)
    c.drawString(MARGIN, yb(y + 28),
                 f"{d.get('class_name', '')} {d.get('level', '')} | {d.get('race', '')} | {d.get('background', '')}")
    y += 44
    paragraphs = text.split("\n\n")
    for para in paragraphs:
        if not para.strip():
            continue
        lines = simpleSplit(para, FONT, 6, PAGE_W - 2 * MARGIN)
        needed_h = len(lines) * 10 + 20
        if y + needed_h > PAGE_H - MARGIN:
            c.showPage()
            y = MARGIN
        if "\n" not in para and para.isupper():
            c.setFont(FONT_BOLD, 7)
            c.drawString(MARGIN, yb(y + 12), para)
            y += 16
        else:
            for line in lines:
                if y + 10 > PAGE_H - MARGIN:
                    c.showPage()
                    y = MARGIN
                c.setFont(FONT, 6)
                c.drawString(MARGIN, yb(y + 10), line)
                y += 10
            y += 6


# ═══════════════════════════════════════════════════════════════
#  MAIN GENERATOR
# ═══════════════════════════════════════════════════════════════
def generate_character_sheet(char_data, output_path=None):
    import tempfile
    use_temp = output_path is None
    path = output_path or tempfile.mktemp(suffix=".pdf")
    c = canvas.Canvas(path, pagesize=letter)
    c.setTitle(f"D&D 5e Character Sheet — {char_data.get('name', 'Character')}")
    c.setAuthor("Character Manager")
    draw_page1(c, char_data)
    c.showPage()
    draw_page2(c, char_data)
    c.showPage()
    if char_data.get("is_caster"):
        draw_page3(c, char_data)
        c.showPage()
    if char_data.get("spell_appendix"):
        draw_spell_appendix(c, char_data)
    if char_data.get("has_equipment_appendix"):
        c.showPage()
        draw_equipment_appendix(c, char_data)
    if char_data.get("has_long_features"):
        c.showPage()
        draw_page4_appendix(c, char_data)
    c.save()
    if use_temp:
        with open(path, "rb") as f:
            data = f.read()
        os.unlink(path)
        return data


_TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "media", "james", "SlowDisk1tb", "home-move", "DnD-Manuals", "5E_CharacterSheet_Fillable.pdf")


def _resolve_template():
    """Find the official fillable PDF template."""
    candidates = [
        _TEMPLATE_PATH,
        "/media/james/SlowDisk1tb/home-move/DnD-Manuals/5E_CharacterSheet_Fillable.pdf",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "5E_CharacterSheet_Fillable.pdf"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(
        "Official WotC fillable sheet not found. "
        "Download from https://media.wizards.com/2016/dnd/downloads/5E_CharacterSheet_Fillable.pdf"
    )


# ── Save/skill check-box name mapping (matched by page y-position) ──
_SAVE_CHECKBOXES = {
    "Strength": "Check Box 11",
    "Dexterity": "Check Box 18",
    "Constitution": "Check Box 19",
    "Intelligence": "Check Box 20",
    "Wisdom": "Check Box 21",
    "Charisma": "Check Box 22",
}
# Also by abbreviation
_SAVE_ABBR_CHECKBOXES = {"STR": "Check Box 11", "DEX": "Check Box 18", "CON": "Check Box 19",
                         "INT": "Check Box 20", "WIS": "Check Box 21", "CHA": "Check Box 22"}

_SKILL_CHECKBOXES = {
    "Acrobatics": "Check Box 23",
    "Animal Handling": "Check Box 24",
    "Arcana": "Check Box 25",
    "Athletics": "Check Box 26",
    "Deception": "Check Box 27",
    "History": "Check Box 28",
    "Insight": "Check Box 29",
    "Intimidation": "Check Box 30",
    "Investigation": "Check Box 31",
    "Medicine": "Check Box 32",
    "Nature": "Check Box 33",
    "Perception": "Check Box 34",
    "Performance": "Check Box 35",
    "Persuasion": "Check Box 36",
    "Religion": "Check Box 37",
    "Sleight of Hand": "Check Box 38",
    "Stealth": "Check Box 39",
    "Survival": "Check Box 40",
}

# Spell fields on page 3: named Spells 10XXX where XXX encodes row.
# We fill spells level-by-level; the first few are enough for most characters.
# If more space is needed, add lower-priority cells.
_SPELL_FIELDS_BY_LEVEL = {
    0: ["Spells 1014", "Spells 1015", "Spells 1016", "Spells 1017", "Spells 1018", "Spells 1019", "Spells 1020", "Spells 1021"],
    1: ["Spells 1022", "Spells 1023", "Spells 1024", "Spells 1025", "Spells 1026", "Spells 1027", "Spells 1028", "Spells 1029"],
    2: ["Spells 1030", "Spells 1031", "Spells 1032", "Spells 1033", "Spells 1034", "Spells 1035", "Spells 1036", "Spells 1037"],
    3: ["Spells 1038", "Spells 1039", "Spells 1040", "Spells 1041", "Spells 1042", "Spells 1043", "Spells 1044", "Spells 1045"],
    4: ["Spells 1046", "Spells 1047", "Spells 1048", "Spells 1049", "Spells 1050", "Spells 1051", "Spells 1052", "Spells 1053"],
    5: ["Spells 1054", "Spells 1055", "Spells 1056", "Spells 1057", "Spells 1058", "Spells 1059", "Spells 1060", "Spells 1061"],
    6: ["Spells 1062", "Spells 1063", "Spells 1064", "Spells 1065", "Spells 1066", "Spells 1067", "Spells 1068", "Spells 1069"],
    7: ["Spells 1070", "Spells 1071", "Spells 1072", "Spells 1073", "Spells 1074", "Spells 1075", "Spells 1076", "Spells 1077"],
    8: ["Spells 1078", "Spells 1079", "Spells 1080", "Spells 1081", "Spells 1082", "Spells 1083", "Spells 1084", "Spells 1085"],
    9: ["Spells 1086", "Spells 1087", "Spells 1088", "Spells 1089", "Spells 1090", "Spells 1091", "Spells 1092", "Spells 1093"],
}

# Slot fields: SlotsTotal 19-27 = levels 1-9, SlotsRemaining 19-27 = levels 1-9
_SLOT_TOTAL_FIELDS = {i: f"SlotsTotal {18+i}" for i in range(1, 10)}
_SLOT_REMAINING_FIELDS = {i: f"SlotsRemaining {18+i}" for i in range(1, 10)}


def _skill_field_name(name):
    """Map D&D skill name to the PDF field name."""
    lookup = {
        "Animal Handling": "Animal",
        "Sleight of Hand": "SleightofHand",
    }
    return lookup.get(name, name)


def _mod_str(val):
    """Format an ability modifier as signed string."""
    if val >= 0:
        return f"+{val}"
    return str(val)


def _draw_appendix_page(c, d, title, data_key, subtitle=None):
    """Draw a single appendix page/section using the ReportLab canvas."""
    text = d.get(data_key, "")
    if not text or not text.strip():
        return
    from reportlab.lib.utils import simpleSplit
    MARGIN = 18
    PAGE_W, PAGE_H = 612, 792
    FONT = "Times-Roman"
    FONT_BOLD = "Times-Bold"

    y = 30
    # Section header
    c.setFont(FONT_BOLD, 12)
    c.drawString(MARGIN, PAGE_H - y - 16, f"{title} — {d.get('name', '')}")
    if subtitle:
        c.setFont(FONT, 7)
        c.drawString(MARGIN, PAGE_H - y - 28, subtitle)
    y += 44

    paragraphs = text.split("\n\n")
    for para in paragraphs:
        if not para.strip():
            continue
        lines = simpleSplit(para, FONT, 6, PAGE_W - 2 * MARGIN)
        needed_h = len(lines) * 10 + 20
        if y + needed_h > PAGE_H - MARGIN:
            c.showPage()
            y = MARGIN
        if "\n" not in para and para.isupper():
            c.setFont(FONT_BOLD, 7)
            c.drawString(MARGIN, PAGE_H - y - 12, para)
            y += 16
        else:
            for line in lines:
                if y + 10 > PAGE_H - MARGIN:
                    c.showPage()
                    y = MARGIN
                c.setFont(FONT, 6)
                c.drawString(MARGIN, PAGE_H - y - 10, line)
                y += 10
            y += 6


def fill_official_sheet(char_data, output_path=None):
    """Fill the official WotC fillable character sheet PDF with character data.

    Args:
        char_data: dict from build_char_data()
        output_path: optional path to save PDF. If None, returns bytes.

    Returns:
        PDF bytes if output_path is None, else None.
    """
    import tempfile
    from pypdf import PdfReader, PdfWriter

    template_path = _resolve_template()
    reader = PdfReader(template_path)
    writer = PdfWriter(clone_from=template_path)

    # ── BUILD FIELD-NAME MAPPING (normalize trailing spaces) ────────
    # The template has some field names with trailing spaces (e.g. "Race ", "Perception ")
    actual_field_names = {}
    for n in reader.get_fields() or {}:
        actual_field_names[n.strip()] = n
    _resolve_fn = lambda name: actual_field_names.get(name, name)

    d = char_data

    # ── PAGE 1 FIELDS ────────────────────────────────────────────

    fields = {}

    # Identity
    fields["CharacterName"] = d.get("name", "")
    classes = d.get("class_levels", {}) or {}
    if isinstance(classes, str):
        import json
        classes = json.loads(classes) if classes else {}
    class_str = d.get("class_name", "")
    lvl = d.get("level", 1)
    if classes and isinstance(classes, dict):
        parts = [f"{cls} {l}" for cls, l in sorted(classes.items()) if l > 0]
        if parts:
            fields["ClassLevel"] = " / ".join(parts)
        else:
            fields["ClassLevel"] = f"{class_str} {lvl}" if class_str else f"Level {lvl}"
    else:
        fields["ClassLevel"] = f"{class_str} {lvl}" if class_str else f"Level {lvl}"
    race = d.get("race", "")
    subrace = d.get("subrace", "")
    fields["Race"] = f"{subrace} {race}" if subrace else race
    fields["Background"] = d.get("background", "")
    fields["Alignment"] = d.get("alignment", "")[:15]
    fields["PlayerName"] = d.get("player_name", d.get("user_id", ""))
    fields["XP"] = str(d.get("xp", d.get("experience", "")))

    # Ability scores
    ab_map = {"STR": "strength", "DEX": "dexterity", "CON": "constitution",
              "INT": "intelligence", "WIS": "wisdom", "CHA": "charisma"}
    for abbr, attr in ab_map.items():
        score = d.get(attr, 10)
        mod = (score - 10) // 2
        fields[abbr] = str(score)
        # Template uses CHamod (lowercase 'a') not CHAmod
        mod_field = "CHamod" if abbr == "CHA" else f"{abbr}mod"
        fields[mod_field] = _mod_str(mod)

    # Saves
    save_profs = set()
    raw = d.get("save_proficiencies", []) or []
    for s in raw:
        if isinstance(s, dict):
            save_profs.add(s.get("name", "").lower())
        elif isinstance(s, str):
            save_profs.add(s.lower())

    pb = d.get("proficiency_bonus", 2)
    # Build save-to-ability mapping: save name -> abbreviation
    _SAVE_TO_ABBR = {"strength": "STR", "dexterity": "DEX", "constitution": "CON",
                     "intelligence": "INT", "wisdom": "WIS", "charisma": "CHA"}
    _ABBR_TO_SAVE = {v: k for k, v in _SAVE_TO_ABBR.items()}
    for abbr, attr in ab_map.items():
        base_mod = (d.get(attr, 10) - 10) // 2
        save_name = attr
        is_prof = save_name in save_profs or abbr.lower() in save_profs
        save_mod = base_mod + pb if is_prof else base_mod
        fields[f"ST {save_name.capitalize()}"] = _mod_str(save_mod)
        if is_prof:
            cb = _SAVE_ABBR_CHECKBOXES.get(abbr) or _SAVE_CHECKBOXES.get(save_name.capitalize())
            if cb:
                fields[cb] = "/Yes"

    # Skills
    skills_data = d.get("skills", {}) or {}
    if isinstance(skills_data, str):
        import json
        skills_data = json.loads(skills_data) if skills_data else {}
    skill_profs = {}
    if isinstance(skills_data, dict):
        skill_profs = {k.lower(): v for k, v in skills_data.items()}
    elif isinstance(skills_data, list):
        for s in skills_data:
            if isinstance(s, str):
                skill_profs[s.lower()] = 2  # proficient
            elif isinstance(s, dict):
                skill_profs[s.get("name", "").lower()] = s.get("proficiency", 2)

    skill_ability_map = {
        "Acrobatics": "DEX", "Animal Handling": "WIS", "Arcana": "INT",
        "Athletics": "STR", "Deception": "CHA", "History": "INT",
        "Insight": "WIS", "Intimidation": "CHA", "Investigation": "INT",
        "Medicine": "WIS", "Nature": "INT", "Perception": "WIS",
        "Performance": "CHA", "Persuasion": "CHA", "Religion": "INT",
        "Sleight of Hand": "DEX", "Stealth": "DEX", "Survival": "WIS",
    }

    for skill_name, abbr in skill_ability_map.items():
        attr = ab_map[abbr]  # "DEX" → "dexterity"
        ab_score = d.get(attr, 10)
        ab_mod = (ab_score - 10) // 2
        prof_val = skill_profs.get(skill_name.lower(), 0)
        if isinstance(prof_val, str):
            prof_val = 2 if prof_val.lower() in ("proficient", "expertise") else 0
        total_mod = ab_mod
        if prof_val == 2:
            total_mod += pb
        elif prof_val > 2:  # expertise
            total_mod += pb * 2
        field_name = _skill_field_name(skill_name)
        fields[field_name] = _mod_str(total_mod)
        if prof_val >= 2 and skill_name in _SKILL_CHECKBOXES:
            fields[_SKILL_CHECKBOXES[skill_name]] = "/Yes"

    # Combat stats
    fields["AC"] = str(d.get("ac", 10))
    fields["Initiative"] = _mod_str(d.get("initiative", (d.get("dexterity", 10) - 10) // 2))
    fields["Speed"] = str(d.get("speed", 30))
    fields["HPMax"] = str(d.get("hp_max", d.get("hp", 1)))
    fields["HPCurrent"] = str(d.get("hp_current", d.get("hp", 1)))
    fields["HPTemp"] = str(d.get("hp_temp", ""))
    fields["HD"] = str(d.get("hit_dice_type", "1d8"))
    hd_total = d.get("hit_dice_total", d.get("level", 1))
    hd_used = d.get("hit_dice_used", 0)
    fields["HDTotal"] = str(hd_total)
    fields["ProfBonus"] = _mod_str(pb)
    fields["Passive"] = str(d.get("passive_perception", 10))
    fields["Inspiration"] = str(d.get("inspiration", ""))

    # Weapons (up to 3 slots)
    attacks = d.get("attacks_data", []) or []
    if isinstance(attacks, str):
        import json
        attacks = json.loads(attacks) if attacks else []
    weapon_slots = ["Wpn Name", "Wpn Name 2", "Wpn Name 3"]
    atk_slots = ["Wpn1 AtkBonus", "Wpn2 AtkBonus", "Wpn3 AtkBonus"]
    dmg_slots = ["Wpn1 Damage", "Wpn2 Damage", "Wpn3 Damage"]
    for i in range(3):
        if i < len(attacks):
            atk = attacks[i]
            fields[weapon_slots[i]] = atk.get("name", "")
            atk_bonus = atk.get("attack_bonus") if atk.get("attack_bonus") is not None else atk.get("bonus", 0)
            fields[atk_slots[i]] = _mod_str(atk_bonus)
            fields[dmg_slots[i]] = atk.get("damage", "")
    # Overflow attacks into AttacksSpellcasting text area
    extra_attacks = []
    for i, atk in enumerate(attacks):
        if i < 3:
            # Already handled above
            dmg = atk.get("damage", "")
            continue
        atk_bonus = atk.get("attack_bonus") if atk.get("attack_bonus") is not None else atk.get("bonus", 0)
        extra_attacks.append(f"{atk.get('name', '?')} +{atk_bonus} {atk.get('damage', '')}")
    attack_text = "\n".join(extra_attacks) if extra_attacks else ""

    # Spellcasting info (always shown on AttacksSpellcasting area)
    sa = d.get("spell_ability", "")
    spell_save = d.get("spell_save_dc", 0)
    spell_atk = d.get("spell_attack_bonus", 0)
    if sa:
        spell_line = f"DC {spell_save} ATK +{spell_atk} ({sa})"
        if attack_text:
            attack_text = spell_line + "\n" + attack_text
        else:
            attack_text = spell_line
    fields["AttacksSpellcasting"] = attack_text

    # Money
    fields["CP"] = str(d.get("cp", 0))
    fields["SP"] = str(d.get("sp", 0))
    fields["EP"] = str(d.get("ep", 0))
    fields["GP"] = str(d.get("gp", 0))
    fields["PP"] = str(d.get("pp", 0))

    # ── PAGE 1 NARRATIVE BOXES ─────────────────────────────────────
    fields["PersonalityTraits"] = str(d.get("personality_traits", ""))
    fields["Ideals"] = str(d.get("ideals", ""))
    fields["Bonds"] = str(d.get("bonds", ""))
    fields["Flaws"] = str(d.get("flaws", ""))

    # Proficiencies & Languages
    prof_lines = []
    for key, label in [("armor_proficiencies", "Armor"), ("weapon_proficiencies", "Weapons"),
                        ("tool_proficiencies", "Tools"), ("languages", "Languages")]:
        vals = d.get(key, []) or []
        if isinstance(vals, str):
            import json
            vals = json.loads(vals) if vals else []
        if vals:
            prof_lines.append(f"{label}: {', '.join(v for v in vals if isinstance(v, str))}")
    fields["ProficienciesLang"] = "\n".join(prof_lines)

    # Equipment (condensed)
    inv = d.get("inventory", []) or []
    if isinstance(inv, str):
        import json
        inv = json.loads(inv) if inv else []
    equip_lines = [f"{i.get('name', '?')} x{i.get('quantity', 1)}" for i in inv[:30] if isinstance(i, dict)]
    equip_str = ", ".join(equip_lines)
    if d.get("has_equipment_appendix"):
        if equip_str:
            equip_str += "\n... See Appendix"
    fields["Equipment"] = equip_str

    # Features & Traits (condensed)
    has_feat_appendix = bool(d.get("full_feature_text", "").strip())
    # Use condensed_features (names only) with appendix ref — user wanted
    # short descriptions OR "See Appendix". With the ref, names + ref is enough.
    feat_text = d.get("condensed_features", "") or ""
    if has_feat_appendix and feat_text:
        feat_text = str(feat_text)
        if feat_text:
            feat_text += "\n... See Appendix"
    fields["Features and Traits"] = str(feat_text)

    # ── PAGE 2 FIELDS ──────────────────────────────────────────────
    fields["CharacterName 2"] = d.get("name", "")
    fields["Age"] = str(d.get("age", ""))
    fields["Height"] = str(d.get("height", ""))
    fields["Weight"] = str(d.get("weight", ""))
    fields["Eyes"] = str(d.get("eyes", ""))
    fields["Skin"] = str(d.get("skin", ""))
    fields["Hair"] = str(d.get("hair", ""))
    fields["FactionName"] = str(d.get("faction", ""))
    fields["Allies"] = str(d.get("allies", ""))

    # Backstory
    backstory = str(d.get("backstory", "") or "")
    pd = d.get("personality_data", {}) or {}
    if isinstance(pd, str):
        import json
        pd = json.loads(pd) if pd else {}
    backstory_extras = pd.get("backstory", "")
    if backstory_extras and backstory_extras not in backstory:
        if backstory:
            backstory += "\n\n" + backstory_extras
        else:
            backstory = backstory_extras
    fields["Backstory"] = backstory

    # Feats & Traits page 2
    feat_long = str(d.get("full_feature_text", "") or "")
    if feat_long.strip():
        if feat_long:
            feat_long += "\n... See Appendix"
    fields["Feat+Traits"] = feat_long

    # Treasure
    treasure = str(d.get("treasure", "") or "")
    fields["Treasure"] = treasure

    # ── PAGE 3: SPELL SHEET ────────────────────────────────────────
    spells = d.get("spells", []) or []
    if isinstance(spells, str):
        import json
        spells = json.loads(spells) if spells else []

    if spells:
        # Fill spellcasting info on page 3
        fields["SpellcastingAbility 2"] = d.get("spell_ability", "")
        fields["SpellSaveDC  2"] = str(d.get("spell_save_dc", 0))
        fields["SpellAtkBonus 2"] = _mod_str(d.get("spell_attack_bonus", 0))
        fields["Spellcasting Class 2"] = d.get("class_name", "")

        # Group spells by level
        by_level = {}
        for spell in spells:
            if isinstance(spell, (list, tuple)):
                name, level, prepared = spell[0], spell[1], len(spell) > 2 and spell[2]
            elif isinstance(spell, dict):
                name = spell.get("name", "")
                level = spell.get("level", 0)
                prepared = spell.get("prepared", True)
            else:
                continue
            by_level.setdefault(int(level), []).append(name)

        # Fill slot totals and used
        slot_data = d.get("spell_slot_data", {}) or {}
        if isinstance(slot_data, str):
            import json
            slot_data = json.loads(slot_data) if slot_data else {}
        slots_by_level = slot_data.get("by_level", {}) if isinstance(slot_data, dict) else {}
        slots_used = d.get("spell_slots_used", {}) or {}
        if isinstance(slots_used, str):
            import json
            slots_used = json.loads(slots_used) if slots_used else {}

        for lvl in range(1, 10):
            total = slots_by_level.get(str(lvl), 0) or 0
            fields[_SLOT_TOTAL_FIELDS[lvl]] = str(total)

        # Fill spell names into fields
        for lvl in range(0, 10):
            names = by_level.get(lvl, [])
            slot_fields = _SPELL_FIELDS_BY_LEVEL.get(lvl, [])
            for i, sname in enumerate(names):
                if i < len(slot_fields):
                    fields[slot_fields[i]] = sname

        # Tag the spellcasting class field with appendix note
        has_spell_appendix = bool(d.get("spell_appendix", "").strip())
        if has_spell_appendix:
            sc_val = fields.get("Spellcasting Class 2", "")
            if sc_val:
                fields["Spellcasting Class 2"] = sc_val + " (See Appendix)"

    # ── APPLY FIELDS TO ALL PAGES ──────────────────────────────────
    # Map stripped names to actual field names (handle trailing spaces)
    field_dict = {_resolve_fn(k): v for k, v in fields.items() if v}

    # Calculate font size for each text field so text fits its bounding box
    from pypdf.generic import TextStringObject, NameObject
    for page in writer.pages:
        for ann_ref in page.get("/Annots") or []:
            try:
                obj = ann_ref.get_object()
                ft = obj.get("/FT")
                if not (ft and str(ft) == "/Tx"):
                    continue
                t = str(obj.get("/T", "")).strip()
                val = fields.get(t)
                if not val:
                    continue
                rect = obj.get("/Rect")
                if not rect:
                    continue
                bw = float(rect[2]) - float(rect[0])
                bh = float(rect[3]) - float(rect[1])
                if bw < 1 or bh < 1:
                    continue
                # Try font sizes from 9 down to 4, pick the largest that fits all text
                text_len = len(str(val))
                chosen_size = 4
                for fs in range(9, 3, -1):
                    cpl = bw / (fs * 0.55)
                    if cpl < 1: continue
                    lines_needed = max(1, text_len / cpl)
                    h_needed = lines_needed * (fs * 1.2)
                    if h_needed <= bh:
                        chosen_size = fs
                        break
                new_da = f"/Helv {chosen_size} Tf 0 g"
                obj[NameObject("/DA")] = TextStringObject(new_da)
            except Exception:
                pass

    for page in writer.pages:
        writer.update_page_form_field_values(
            page,
            field_dict,
            auto_regenerate=True,
        )

    # Flatten: remove AcroForm so fields aren't editable
    metadata = reader.metadata
    if metadata:
        writer.add_metadata(metadata)

    # Write output
    use_temp = output_path is None
    path = output_path or tempfile.mktemp(suffix=".pdf")

    with open(path, "wb") as f:
        writer.write(f)

    # ── APPENDIX PAGES (full-feature / spell / equipment / allies overflow) ──
    # Generate appendix content with ReportLab, then merge
    appendices_needed = (
        d.get("full_feature_text", "").strip()
        or d.get("spell_appendix", "").strip()
        or d.get("equipment_appendix", "").strip()
        or d.get("summons_appendix", "").strip()
        or d.get("allies_appendix", "").strip()
    )
    if appendices_needed:
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.lib.pagesizes import letter as rl_letter
        appendices_path = tempfile.mktemp(suffix="_appendices.pdf")
        ac = rl_canvas.Canvas(appendices_path, pagesize=rl_letter)
        ac.setTitle(f"D&D Character Sheet Appendices — {d.get('name', 'Character')}")
        ac.setAuthor("Character Manager")

        if d.get("spell_appendix", "").strip():
            _draw_appendix_page(ac, d, "SPELL APPENDIX", "spell_appendix")
            ac.showPage()
        if d.get("equipment_appendix", "").strip():
            _draw_appendix_page(ac, d, "EQUIPMENT APPENDIX", "equipment_appendix")
            ac.showPage()
        if d.get("full_feature_text", "").strip():
            _draw_appendix_page(ac, d, "CLASS FEATURE APPENDIX", "full_feature_text",
                                subtitle=f"{d.get('class_name','')} {d.get('level','')} | {d.get('race','')} | {d.get('background','')}")
        if d.get("has_summons_appendix") and d.get("summons_appendix", "").strip():
            ac.showPage()
            _draw_appendix_page(ac, d, "SUMMONS APPENDIX", "summons_appendix",
                                subtitle=f"{d.get('class_name','')} {d.get('level','')} | {d.get('race','')} | {d.get('background','')}")
        if d.get("allies_appendix", "").strip():
            ac.showPage()
            _draw_appendix_page(ac, d, "ALLIES & ORGANIZATIONS", "allies_appendix")
        ac.save()

        if os.path.getsize(appendices_path) > 1000:  # non-empty
            from pypdf import PdfWriter as MergeWriter, PdfReader as MergeReader
            merger = MergeWriter()
            merger.append(path)
            merger.append(appendices_path)
            with open(path, "wb") as f:
                merger.write(f)
            merger.close()

        try:
            os.unlink(appendices_path)
        except OSError:
            pass

    if use_temp:
        with open(path, "rb") as f:
            data = f.read()
        os.unlink(path)
        return data


def generate_ai_summary_pdf(query: str, summary: str, sources: list[dict]) -> bytes:
    """Generate a clean printable PDF for an AI manual search summary."""
    import tempfile
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    from reportlab.lib.colors import HexColor
    from reportlab.lib.units import inch

    path = tempfile.mktemp(suffix=".pdf")
    doc = SimpleDocTemplate(path, pagesize=letter,
                            leftMargin=36, rightMargin=36,
                            topMargin=36, bottomMargin=36)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Title2", parent=styles["Title"],
                                 fontSize=18, spaceAfter=8, textColor=HexColor("#333333"))
    subtitle_style = ParagraphStyle("Sub", parent=styles["Normal"],
                                    fontSize=9, textColor=HexColor("#888888"), spaceAfter=14)
    body_style = ParagraphStyle("Body", parent=styles["Normal"],
                                fontSize=10, leading=15, spaceAfter=6)
    source_title = ParagraphStyle("SrcTitle", parent=styles["Normal"],
                                  fontSize=9, textColor=HexColor("#555555"),
                                  fontName="Helvetica-Bold", spaceAfter=4, spaceBefore=12)
    source_item = ParagraphStyle("SrcItem", parent=styles["Normal"],
                                 fontSize=8, leading=12, textColor=HexColor("#666666"))

    elements = []
    elements.append(Paragraph(f"AI Manual Summary: \"{query}\"", title_style))
    elements.append(Paragraph(f"Generated by D&D Character Manager", subtitle_style))
    elements.append(HRFlowable(width="100%", thickness=1, color=HexColor("#cccccc")))
    elements.append(Spacer(1, 10))

    # Summary body
    for para in summary.split("\n"):
        p = para.strip()
        if p:
            elements.append(Paragraph(p, body_style))
            elements.append(Spacer(1, 4))

    elements.append(Spacer(1, 12))
    elements.append(HRFlowable(width="100%", thickness=1, color=HexColor("#cccccc")))
    elements.append(Paragraph(f"Sources ({len(sources)})", source_title))

    for s in sources[:20]:
        book = s.get("book", "?")
        page = s.get("page", "")
        snippet = s.get("snippet", "")[:200]
        label = f"<b>{book}</b>"
        if page:
            label += f" ~p.{page}"
        elements.append(Paragraph(label + " — " + snippet, source_item))
        elements.append(Spacer(1, 2))

    doc.build(elements)
    with open(path, "rb") as f:
        data = f.read()
    os.unlink(path)
    return data
