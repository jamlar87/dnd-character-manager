"""Feature / appendix text builders (plain strings, no drawing)."""

import os
import sys

from services.rules import ability_mod

from .shared import _get_item_description, _get_spell_cache

def mod_int(score):
    return ability_mod(score)


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
