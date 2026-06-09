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
def build_char_data(row, db_cursor=None):
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
            d[field] = {} if field in ("spell_slots_used", "spell_slot_data", "background_data") else []

    for f in ["personality", "backstory", "alignment", "subrace", "subclass"]:
        d.setdefault(f, "")

    bg_data = d.get("background_data", {}) or {}
    d["ideals"] = d.get("ideals", "") or bg_data.get("ideals", "") or ""
    d["bonds"] = d.get("bonds", "") or bg_data.get("bonds", "") or ""
    d["flaws"] = d.get("flaws", "") or bg_data.get("flaws", "") or ""

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

    d["condensed_features"] = _build_condensed_features(d)
    d["full_feature_text"] = _build_full_feature_text(d)
    d["page1_features"] = _build_page1_features_text(d)
    d["has_long_features"] = len(d.get("full_feature_text", "")) > 140

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
    return "\n".join(lines[:30])


def _build_full_feature_text(d):
    lines = []
    feature_data = d.get("feature_data", []) or []
    for fd in feature_data:
        name = fd.get("name", "")
        desc = fd.get("description", "")
        if name:
            lines.append(name.upper())
            if desc:
                lines.append(desc)
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
            # Take first ~250 chars; break at sentence boundary
            short = desc[:250]
            last_period = max(short.rfind("."), short.rfind("…"))
            if last_period > 100:
                short = short[:last_period + 1]
            lines.append(short)
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
    c.setFont(FONT_BOLD, size)
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
        c.circle(x, cy, r, fill=1)
        c.setFillColor((255, 255, 255))


def _text_box(c, x, y_tl, w, h, text, size=6, label_text=None):
    """Fixed bounding box. Text clips — never overflows."""
    y_bottom = yb(y_tl) - h
    c.setStrokeColor((0, 0, 0))
    c.rect(x, y_bottom, w, h)
    if label_text:
        c.setFont(FONT_BOLD, 5)  # was 4, +25%
        c.setFillColor((0.2, 0.2, 0.2))
        c.drawString(x + 2, yb(y_tl) - 7, str(label_text).upper())
        c.setFillColor((0, 0, 0))
    if not text:
        return
    line_h = size + 2
    offset_top = 10 if label_text else 4
    max_lines = int((h - offset_top) / line_h)
    if max_lines < 1:
        return
    lines = simpleSplit(str(text), FONT, size, w - 6)
    display = lines[:max_lines]
    if len(lines) > max_lines and max_lines > 1:
        display[-1] = display[-1][:int(w / 8)] + "… (cont.)"
    c.setFont(FONT, size)
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
    _label(c, COL2_X, y_atk - 9, "Attacks & Spellcasting")
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
    # Currency — separate section above Equipment
    y_cur = y_atk + 12 + 5 * 15 + 22  # increased from +14
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
    # Equipment — extended to column bottom with full descriptions
    y_eq = y_cur + 40
    _label(c, COL2_X, y_eq - 9, "Equipment")
    # Fill remaining column space to bottom margin (40pt from page bottom)
    eq_box_h = int(yb(y_eq) - yb(PAGE_H - 40))
    items = []
    for item in (d.get("equipped", []) or []):
        if isinstance(item, dict):
            name = item.get("name", "")
            desc = _get_item_description(name)
            qty = item.get("qty", 1)
            if qty > 1:
                name = f"{name} x{qty}"
            if desc:
                items.append(f"[E] {name}: {desc}")
            else:
                items.append(f"[E] {name}")
    for item in (d.get("inventory", []) or []):
        if isinstance(item, dict):
            name = item.get("name", "")
            desc = _get_item_description(name)
            qty = item.get("qty", 1)
            if qty > 1:
                name = f"{name} x{qty}"
            if desc:
                items.append(f"{name}: {desc}")
            else:
                items.append(f"{name}")
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
        ("Personality Traits", d.get("personality", "")),
        ("Ideals", d.get("ideals", "")),
        ("Bonds", d.get("bonds", "")),
        ("Flaws", d.get("flaws", "")),
    ]):
        sy = y + i * (block_h + gap)
        _text_box(c, COL3_X, sy, w, block_h, txt, size=5, label_text=lbl)
    y_feat = y + 4 * (block_h + gap) + 14
    feat_h = PAGE_H - 36 - y_feat
    _text_box(c, COL3_X, y_feat, w, feat_h, d.get("page1_features", ""), size=4.5,
              label_text="Features & Traits")


# ═══════════════════════════════════════════════════════════════
#  PAGE 2 — NARRATIVE LAYOUT (5 locked blocks, NO feature text)
# ═══════════════════════════════════════════════════════════════
def draw_page2(c, d):
    y0 = 30
    # Physical properties row
    phys_w = 92
    for i, (lbl, val) in enumerate([
        ("Age", ""), ("Height", ""), ("Weight", ""),
        ("Eyes", ""), ("Skin", ""), ("Hair", ""),
    ]):
        px = COL1_X + i * (phys_w + 4)
        _label(c, px, y0 - 9, lbl)
        _value(c, px, y0, phys_w, 16, val, size=8)
    y2 = y0 + 32
    left_x, left_w = COL1_X, 280
    right_x, right_w = left_x + left_w + 16, 260

    # Left: Character Appearance, Character Backstory
    _text_box(c, left_x, y2, left_w, 280, "", size=5, label_text="Character Appearance")
    _text_box(c, left_x, y2 + 290, left_w, 400, d.get("backstory", ""), size=5,
              label_text="Character Backstory")

    # Right: Allies & Organizations, Additional Features & Traits, Treasure
    _text_box(c, right_x, y2, right_w, 180, "", size=5, label_text="Allies & Organizations")
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
        for i, (name, _) in enumerate(cantrips[:8]):
            c.setFont(FONT, 5.5)
            c.drawString(x0 + 8, yb(y + 10 + i * 12), name)
        y += 10 + len(cantrips[:8]) * 12 + 6

    # Column layout: Col1=1st,2nd | Col2=3rd,4th,5th | Col3=6th,7th,8th,9th
    # Exact X positions per spec: Col1=45, Col2=245, Col3=445 (200pt gaps)
    col_layout = [
        (45, [1, 2]),          # Col 1: 1st, 2nd
        (245, [3, 4, 5]),      # Col 2: 3rd, 4th, 5th
        (445, [6, 7, 8, 9]),   # Col 3: 6th, 7th, 8th, 9th
    ]
    col_w = 160
    y_start = y + 6

    for cx, levels in col_layout:
        # Light tinted background for this column to visually separate
        c.setFillColor((0.97, 0.97, 0.97) if cx > 45 else (1, 1, 1))
        c.setStrokeColor((0, 0, 0))
        c.rect(cx, yb(PAGE_H - MARGIN), col_w, (PAGE_H - MARGIN) - yb(y_start), fill=1, stroke=0)
        c.setFillColor((0, 0, 0))

        # Draw vertical column divider
        if cx > 45:
            c.setStrokeColor((0.5, 0.5, 0.5))
            c.setDash(2, 4)
            c.line(cx - 4, yb(y_start), cx - 4, yb(PAGE_H - MARGIN))
            c.setDash()
            c.setStrokeColor((0, 0, 0))

        # Calculate card heights so all levels in this column fit
        cards_in_col = len(levels)
        available_h = PAGE_H - y_start - MARGIN
        card_h = (available_h - (cards_in_col - 1) * 4) / cards_in_col

        for i, lvl in enumerate(levels):
            cy_tl = y_start + i * (card_h + 4)
            _render_spell_card(c, cx, cy_tl, col_w, card_h, lvl,
                               by_level.get(lvl, []), spell_slots, slot_used)


def _render_spell_card(c, x, y_tl, w, h, level, spells_list, spell_slots, slot_used):
    """Render one self-contained spell level card."""
    # Card border
    c.setStrokeColor((0, 0, 0))
    c.rect(x, yb(y_tl) - h, w, h)

    # Level header
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(level, "th")
    c.setFont(FONT_BOLD, 6)
    c.drawString(x + 4, yb(y_tl + 8), f"{level}{suffix} LEVEL")

    total_slots = int(spell_slots.get(str(level), 0))
    used_slots = int(slot_used.get(str(level), 0))

    # Slots bar
    c.setFont(FONT_BOLD, 5)
    c.drawString(x + 4, yb(y_tl + 20), "SLOTS TOTAL:")
    _value(c, x + 60, y_tl + 12, 24, 12, str(total_slots), size=7, center=True)
    c.setFont(FONT_BOLD, 5)
    c.drawString(x + 92, yb(y_tl + 20), "SLOTS EXPENDED:")
    _value(c, x + 160, y_tl + 12, 24, 12, str(used_slots), size=7, center=True)

    # Slot bubbles
    for b in range(min(total_slots, 12)):
        bx = x + 6 + (b % 6) * 12
        by = y_tl + 30 + (b // 6) * 12
        _bubble(c, bx + 4, by + 4, 3.5, filled=(b < used_slots))

    # Spell lines
    line_y = y_tl + 54
    if total_slots > 6:
        line_y += 12
    max_lines = int((h - (line_y - y_tl) - 4) / 12)
    for i, (sp_name, prepared) in enumerate(spells_list[:max_lines]):
        ly = line_y + i * 12
        _bubble(c, x + 8, ly + 2, 3, filled=prepared)
        ln_y = yb(ly + 2)
        c.setStrokeColor((0.7, 0.7, 0.7))
        c.line(x + 14, ln_y, x + w - 4, ln_y)
        c.setStrokeColor((0, 0, 0))
        c.setFont(FONT_BOLD if prepared else FONT, 5)
        c.drawString(x + 16, ln_y + 1, trunc(sp_name, 32))

    # Fill remaining lines with empty prep circles + ruled lines
    for i in range(len(spells_list), max_lines):
        ly = line_y + i * 12
        _bubble(c, x + 8, ly + 2, 3, filled=False)
        ln_y = yb(ly + 2)
        c.setStrokeColor((0.7, 0.7, 0.7))
        c.line(x + 14, ln_y, x + w - 4, ln_y)
        c.setStrokeColor((0, 0, 0))


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
    if char_data.get("has_long_features"):
        draw_page4_appendix(c, char_data)
        c.showPage()
    c.save()
    if use_temp:
        with open(path, "rb") as f:
            data = f.read()
        os.unlink(path)
        return data
    return output_path
