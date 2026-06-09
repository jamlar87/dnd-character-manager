"""
D&D 5e Official Character Sheet PDF Generator — v4 (rigid grid, reportlab).
Matches the official D&D 5e character sheet layout with absolute bounding boxes.
No flowable text — every element has a fixed position and size.

Page 1: 3-column grid — abilities, skills, combat, personality blocks, features
Page 2: Narrative layout — appearance, backstory, allies, treasure, features continued
Page 3: Spell matrix — 3 columns by level, prep circles, slots tracking
"""
import json
import os
import sys
import re
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.utils import simpleSplit

# ═══════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════
PAGE_W, PAGE_H = letter  # 612 x 792
FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
FONT_OBL = "Helvetica-Oblique"
MARGIN = 18


def yb(y_tl):
    """Convert top-left y to reportlab bottom-left y."""
    return PAGE_H - y_tl


# ═══════════════════════════════════════════════════════════════
#  SPELL METADATA (kept minimal — only for page 3 slot counts)
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
#  DATA BUILDER
# ═══════════════════════════════════════════════════════════════
def build_char_data(row, db_cursor=None):
    """Convert sqlite3.Row into structured dict for PDF generation."""
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

    # Derived
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

    # Spells from DB
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

    # Condensed feature names for page 1 (bullet points only, not full rule text)
    d["condensed_features"] = _build_condensed_features(d)

    # Full feature text for page 2
    d["full_feature_text"] = _build_full_feature_text(d)

    return d


def mod_int(score):
    return (score - 10) // 2


def mod_str(score):
    m = mod_int(score)
    return f"+{m}" if m >= 0 else str(m)


def _build_condensed_features(d):
    """Build short bullet-point feature names for page 1 Features & Traits box."""
    lines = []
    feature_data = d.get("feature_data", []) or []

    # Usage hints from descriptions
    usage_hints = {
        "divine sense": "1+CHA/day",
        "lay on hands": f"{d.get('level',1)*5} HP pool",
        "channel divinity": "1/rest",
        "second wind": "1d10+Lvl",
        "action surge": "1/rest",
        "rage": f"{2 + (d.get('level',1)-1)//4}/day",
        "bardic inspiration": f"{d.get('cha_mod',0)}/day",
        "wild shape": "2/rest",
        "ki": f"{d.get('level',1)} pts",
        "sneak attack": f"{(d.get('level',1)+1)//2}d6",
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

    # Also include simple feature strings (L1: Divine Sense format)
    features = d.get("features", []) or []
    seen = {fd.get("name", "").lower() for fd in feature_data}
    for f in features:
        if isinstance(f, str) and ":" in f:
            name_part = f.split(":", 1)[1].strip().split("|")[0].strip()
            if name_part.lower() not in seen:
                lines.append(f"• {name_part}")
                seen.add(name_part.lower())

    return "\n".join(lines[:30])  # max 30 items


def _build_full_feature_text(d):
    """Build full feature descriptions for page 2."""
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


def trunc(text, max_len):
    text = str(text) if text else ""
    return text[:max_len]


def _draw_label(c, x, y_tl, text, size=5):
    """Small label above a field."""
    c.setFont(FONT, size)
    c.drawString(x, yb(y_tl), str(text))


def _draw_value(c, x, y_tl, w, h, text, size=9, bold=True, center=False):
    """Bordered value box."""
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


def _draw_checkbox(c, x, y_tl, sz=8, checked=False):
    y = yb(y_tl) - sz
    c.setStrokeColor((0, 0, 0))
    c.rect(x, y, sz, sz)
    if checked:
        c.line(x + 1, y + 1, x + sz - 1, y + sz - 1)
        c.line(x + sz - 1, y + 1, x + 1, y + sz - 1)


def _draw_bubble(c, x, y_tl, r=5, filled=False):
    cy = yb(y_tl)
    c.setStrokeColor((0, 0, 0))
    c.circle(x, cy, r)
    if filled:
        c.setFillColor((0, 0, 0))
        c.circle(x, cy, r, fill=1)
        c.setFillColor((255, 255, 255))


def _draw_text_box(c, x, y_tl, w, h, text, size=6, label_text=None):
    """Fixed bounding box with wrapped text. Clips — never overflows."""
    y_bottom = yb(y_tl) - h
    c.setStrokeColor((0, 0, 0))
    c.rect(x, y_bottom, w, h)

    if label_text:
        c.setFont(FONT, 4)
        c.setFillColor((0.4, 0.4, 0.4))
        c.drawString(x + 2, yb(y_tl) - 7, str(label_text))
        c.setFillColor((0, 0, 0))

    if not text:
        return

    line_h = size + 2
    max_lines = int((h - (10 if label_text else 4)) / line_h)
    if max_lines < 1:
        return

    lines = simpleSplit(str(text), FONT, size, w - 6)
    display = lines[:max_lines]

    # Overflow indicator
    if len(lines) > max_lines and max_lines > 1:
        display[-1] = display[-1][:int(w / 8)] + "… (cont.)"

    c.setFont(FONT, size)
    c.setFillColor((0, 0, 0))
    for i, line in enumerate(display):
        ly = yb(y_tl) - (8 if label_text else 4) - (i + 1) * line_h
        c.drawString(x + 3, ly, line)


# ═══════════════════════════════════════════════════════════════
#  PAGE 1 — 3-COLUMN GRID
# ═══════════════════════════════════════════════════════════════
# Column positions:
COL1_X, COL1_W = 18, 140
COL2_X, COL2_W = 170, 230
COL3_X, COL3_W = 410, 184
GRID_Y = 165  # top of 3-column area


def draw_page1(c, d):
    draw_header(c, d)
    draw_col1(c, d)
    draw_col2(c, d)
    draw_col3(c, d)


# ── HEADER ────────────────────────────────────────────────────
def draw_header(c, d):
    y0, box_h, gap = 30, 16, 28

    # Row 1
    for x, w, lbl, val in [
        (COL1_X, COL1_W, "Character Name", d.get("name", "")),
        (COL2_X, 120, "Race", d.get("race", "")),
        (COL3_X - 80, 120, "Class & Level", f"{d.get('class_name','')} {d.get('level','')}"),
        (COL3_X + 44, 140, "Background", d.get("background", "")),
    ]:
        _draw_label(c, x, y0 - 9, lbl)
        _draw_value(c, x, y0, w, box_h, val, size=8)

    # Row 2
    y2 = y0 + gap
    for x, w, lbl, val in [
        (COL1_X, 76, "Player Name", ""),
        (COL1_X + 82, 54, "Faction", ""),
        (COL2_X, 90, "Experience Points", ""),
        (COL2_X + 96, 110, "Alignment", d.get("alignment", "")),
    ]:
        _draw_label(c, x, y2 - 9, lbl)
        _draw_value(c, x, y2, w, box_h, val, size=8)

    # Row 3 — Inspiration + Proficiency Bonus
    y3 = y2 + gap
    _draw_label(c, COL1_X, y3 - 9, "Inspiration")
    _draw_checkbox(c, COL1_X + 4, y3 + 4, 8, checked=bool(d.get("inspiration", 0)))
    _draw_label(c, COL1_X + 44, y3 - 9, "Proficiency Bonus")
    _draw_value(c, COL1_X + 44, y3, 30, box_h, str(d.get("proficiency_bonus", 2)), size=10, center=True)

    # Row 4 — Passive Perception
    y4 = y3 + gap
    _draw_label(c, COL2_X, y4 - 9, "Passive Perception")
    _draw_value(c, COL2_X, y4, 40, box_h, str(d.get("passive_perception", 10)), size=9, center=True)


# ── COLUMN 1: Abilities, Saving Throws, Skills, Proficiencies ─
def draw_col1(c, d):
    y = GRID_Y

    # --- ABILITY SCORES ---
    abilities = [
        ("STR", "strength", d.get("str_mod", 0)),
        ("DEX", "dexterity", d.get("dex_mod", 0)),
        ("CON", "constitution", d.get("con_mod", 0)),
        ("INT", "intelligence", d.get("int_mod", 0)),
        ("WIS", "wisdom", d.get("wis_mod", 0)),
        ("CHA", "charisma", d.get("cha_mod", 0)),
    ]
    save_profs = [s.lower() for s in (d.get("save_proficiencies", []) or [])]
    prof = d.get("proficiency_bonus", 2)
    block_w, block_h = 56, 70
    sub_cols = [COL1_X, COL1_X + 62]
    gap_y = 6

    for i, (abbr, key, mod) in enumerate(abilities):
        col = i % 2
        row_idx = i // 2
        x = sub_cols[col]
        y_tl = y + row_idx * (block_h + gap_y)

        score = d.get(key, 10)
        is_prof = abbr.lower() in save_profs
        save_mod = mod + (prof if is_prof else 0)

        # Block border
        y_b = yb(y_tl) - block_h
        c.setStrokeColor((0, 0, 0))
        c.rect(x, y_b, block_w, block_h)

        # Abbreviation
        c.setFont(FONT_BOLD, 6)
        c.drawCentredString(x + block_w / 2, yb(y_tl + 8), abbr)

        # Score
        c.setFont(FONT_BOLD, 15)
        c.drawCentredString(x + 20, yb(y_tl + 36), str(score))

        # Modifier box
        mx, mw = x + 34, 20
        c.rect(mx, yb(y_tl + 14) - 24, mw, 24)
        c.setFont(FONT_BOLD, 8)
        c.drawCentredString(mx + mw / 2, yb(y_tl + 36), f"{mod:+d}")

        # Save proficiency
        c.setFont(FONT, 3.5)
        c.drawString(x + 2, yb(y_tl + 54), "SAVE")
        _draw_checkbox(c, x + 29, y_tl + 48, 7, checked=is_prof)
        c.setFont(FONT_BOLD, 6)
        c.drawCentredString(mx + mw / 2, yb(y_tl + 58), f"{save_mod:+d}")

    # --- SKILLS ---
    y_skills = y + 3 * (block_h + gap_y) + 4
    skills = d.get("skills", []) or []
    scores = {
        "strength": d.get("strength", 10), "dexterity": d.get("dexterity", 10),
        "constitution": d.get("constitution", 10), "intelligence": d.get("intelligence", 10),
        "wisdom": d.get("wisdom", 10), "charisma": d.get("charisma", 10),
    }
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

    # Skills title
    _draw_label(c, COL1_X, y_skills - 9, "Skills")

    for i, (name, abbr) in enumerate(all_skills):
        col = i // 9
        row_idx = i % 9
        sx = COL1_X + col * 72
        sy = y_skills + row_idx * row_h

        is_prof = name in skills
        ab_mod = mod_int(scores[abbr_map[abbr]])
        skill_mod = ab_mod + (prof if is_prof else 0)

        _draw_checkbox(c, sx, sy + 3, 6, checked=is_prof)
        c.setFont(FONT, 5)
        c.drawString(sx + 8, yb(sy + row_h - 3), f"{name[:12]} ({abbr})")
        c.setFont(FONT_BOLD, 5.5)
        c.drawRightString(sx + 66, yb(sy + row_h - 3), f"{skill_mod:+d}")

    # --- OTHER PROFICIENCIES ---
    y_prof = y_skills + 9 * row_h + 4
    parts = []
    for lbl, field in [("Weapons", "weapon_proficiencies"), ("Armor", "armor_proficiencies"),
                        ("Tools", "tool_proficiencies"), ("Languages", "languages")]:
        items = d.get(field, []) or []
        if items:
            parts.append(f"{lbl}: {', '.join(items[:6])}")
    _draw_text_box(c, COL1_X, y_prof, COL1_W, 60, "\n".join(parts), size=4.5, label_text="Other Proficiencies")


# ── COLUMN 2: Combat, HP, HD, Death Saves, Attacks, Equipment ─
def draw_col2(c, d):
    y = GRID_Y

    # AC + Initiative + Speed
    _draw_label(c, COL2_X, y - 9, "Armor Class")
    _draw_value(c, COL2_X, y, 50, 20, str(d.get("ac", 10)), size=11, center=True)
    _draw_label(c, COL2_X + 58, y - 9, "Initiative")
    _draw_value(c, COL2_X + 58, y, 44, 20, f"{d.get('initiative', 0):+d}", size=9, center=True)
    _draw_label(c, COL2_X + 110, y - 9, "Speed")
    _draw_value(c, COL2_X + 110, y, 44, 20, str(d.get("speed", 30)), size=9, center=True)

    # HP
    y_hp = y + 36
    _draw_label(c, COL2_X, y_hp - 9, "Hit Point Maximum")
    _draw_value(c, COL2_X, y_hp, 62, 20, str(d.get("hp_max", 10)), size=11, center=True)
    _draw_label(c, COL2_X + 70, y_hp - 9, "Current HP")
    _draw_value(c, COL2_X + 70, y_hp, 62, 20, str(d.get("hp_current", 10)), size=11, center=True)
    _draw_label(c, COL2_X + 140, y_hp - 9, "Temp HP")
    _draw_value(c, COL2_X + 140, y_hp, 44, 20, str(d.get("temp_hp", 0)), size=9, center=True)

    # Hit Dice + Death Saves
    y_hd = y_hp + 32
    _draw_label(c, COL2_X, y_hd - 9, "Hit Dice")
    hd_type = d.get("hit_dice_type", "1d8")
    total = d.get("hit_dice_total", 1)
    used = d.get("hit_dice_used", 0)
    _draw_value(c, COL2_X, y_hd, 64, 16, f"{total-used}/{total} {hd_type}", size=7, center=True)

    _draw_label(c, COL2_X + 72, y_hd - 9, "Death Saves")
    c.setFont(FONT, 4)
    c.drawString(COL2_X + 72, yb(y_hd + 6), "Success")
    succ = d.get("death_saves_success", 0) or 0
    for i in range(3):
        _draw_bubble(c, COL2_X + 110 + i * 16, y_hd + 6, 5, filled=(i < succ))
    c.drawString(COL2_X + 72, yb(y_hd + 20), "Fail")
    fail = d.get("death_saves_fail", 0) or 0
    for i in range(3):
        _draw_bubble(c, COL2_X + 110 + i * 16, y_hd + 20, 5, filled=(i < fail))

    # Attacks & Spellcasting
    y_atk = y_hd + 44
    _draw_label(c, COL2_X, y_atk - 9, "Attacks & Spellcasting")
    col_w = [80, 34, 54]
    headers = ["Name", "Atk", "Damage/Type"]
    row_h = 15

    for j, (h, cw) in enumerate(zip(headers, col_w)):
        cx = COL2_X + sum(col_w[:j])
        ry = yb(y_atk) - 12
        c.setFont(FONT_BOLD, 4.5)
        c.rect(cx, ry, cw, 12)
        c.drawString(cx + 2, ry + 3, h)

    attacks = d.get("attacks_data", []) or []
    for ri in range(5):
        ry_tl = y_atk + 12 + ri * row_h
        atk = attacks[ri] if ri < len(attacks) else {}
        vals = [
            atk.get("name", ""),
            f"{atk.get('attack_bonus', 0):+d}" if atk.get("attack_bonus") is not None else "",
            atk.get("damage", ""),
        ]
        for j, (v, cw) in enumerate(zip(vals, col_w)):
            cx = COL2_X + sum(col_w[:j])
            ry = yb(ry_tl) - row_h
            c.setFont(FONT, 5.5)
            c.rect(cx, ry, cw, row_h)
            c.drawString(cx + 2, ry + 3, trunc(str(v), 20))

    # Equipment
    y_eq = y_atk + 12 + 5 * row_h + 6
    _draw_label(c, COL2_X, y_eq - 9, "Equipment")

    # Currency pills on left edge of equipment box
    eq_box_x, eq_box_w, eq_box_h = COL2_X, COL2_W - 4, 65
    coins = [("CP", d.get("cp", 0)), ("SP", 0), ("EP", 0), ("GP", d.get("gp", 0)), ("PP", 0)]
    coin_w = 26
    for i, (cn, cv) in enumerate(coins):
        cx = eq_box_x + 2
        cy = yb(y_eq) - i * 13 - 13
        c.setFont(FONT_BOLD, 4.5)
        c.rect(cx, cy, coin_w, 12)
        c.drawCentredString(cx + coin_w / 2, cy + 3, cn)
        c.setFont(FONT, 5.5)
        c.rect(cx, cy - 14, coin_w, 14)
        c.drawCentredString(cx + coin_w / 2, cy - 11, str(cv))

    # Equipment text
    items = []
    inventory = d.get("inventory", []) or []
    equipped = d.get("equipped", []) or []
    for item in equipped:
        if isinstance(item, dict):
            items.append(f"[E] {item.get('name', '')}")
    for item in inventory:
        if isinstance(item, dict):
            items.append(f"{item.get('name', '')} x{item.get('qty', 1)}")
    eq_text = "\n".join(items[:15])

    c.setStrokeColor((0, 0, 0))
    c.rect(eq_box_x, yb(y_eq) - eq_box_h, eq_box_w, eq_box_h)
    if eq_text:
        eq_lines = simpleSplit(eq_text, FONT, 5, eq_box_w - coin_w - 8)
        max_eq = int(eq_box_h / 9)
        c.setFont(FONT, 5)
        c.setFillColor((0, 0, 0))
        for i, line in enumerate(eq_lines[:max_eq]):
            c.drawString(eq_box_x + coin_w + 6, yb(y_eq) - 10 - i * 9, line)


# ── COLUMN 3: Personality, Ideals, Bonds, Flaws, Features ─────
def draw_col3(c, d):
    y = GRID_Y
    block_h = 50
    gap = 4
    w = COL3_W

    sections = [
        ("Personality Traits", d.get("personality", "")),
        ("Ideals", d.get("ideals", "")),
        ("Bonds", d.get("bonds", "")),
        ("Flaws", d.get("flaws", "")),
    ]

    for i, (lbl, txt) in enumerate(sections):
        sy = y + i * (block_h + gap)
        _draw_text_box(c, COL3_X, sy, w, block_h, txt, size=5, label_text=lbl)

    # Features & Traits
    y_feat = y + 4 * (block_h + gap) + 6
    feat_h = PAGE_H - 36 - y_feat  # fill remaining page
    _draw_text_box(c, COL3_X, y_feat, w, feat_h, d.get("condensed_features", ""), size=5, label_text="Features & Traits")


# ═══════════════════════════════════════════════════════════════
#  PAGE 2 — NARRATIVE LAYOUT
# ═══════════════════════════════════════════════════════════════
def draw_page2(c, d):
    y = 30

    # --- Top row: physical properties ---
    phys_fields = [
        ("Age", ""), ("Height", ""), ("Weight", ""),
        ("Eyes", ""), ("Skin", ""), ("Hair", ""),
    ]
    phys_w = 92
    for i, (lbl, val) in enumerate(phys_fields):
        px = COL1_X + i * (phys_w + 4)
        _draw_label(c, px, y - 9, lbl)
        _draw_value(c, px, y, phys_w, 16, val, size=8)

    # --- Left side (50%): Appearance + Backstory ---
    left_x = COL1_X
    left_w = 280
    y2 = y + 32

    _draw_text_box(c, left_x, y2, left_w, 280, "",
                   size=5, label_text="Character Appearance")

    _draw_text_box(c, left_x, y2 + 290, left_w, 400, d.get("backstory", ""),
                   size=5, label_text="Character Backstory")

    # --- Right side (50%): Allies, Features continued, Treasure ---
    right_x = COL1_X + left_w + 16
    right_w = 260

    # Allies & Organizations (with faction symbol square)
    _draw_text_box(c, right_x, y2, right_w, 180, "",
                   size=5, label_text="Allies & Organizations")
    # Faction symbol square
    sym_x, sym_y, sym_sz = right_x + right_w - 48, y2 + 8, 44
    c.setStrokeColor((0, 0, 0))
    c.rect(sym_x, yb(sym_y) - sym_sz, sym_sz, sym_sz)

    # Additional Features & Traits
    _draw_text_box(c, right_x, y2 + 190, right_w, 310, d.get("full_feature_text", ""),
                   size=4.5, label_text="Additional Features & Traits")

    # Treasure
    _draw_text_box(c, right_x, y2 + 510, right_w, 150, "",
                   size=5, label_text="Treasure")


# ═══════════════════════════════════════════════════════════════
#  PAGE 3 — SPELL MATRIX (3 columns by level, prep circles)
# ═══════════════════════════════════════════════════════════════
def draw_page3(c, d):
    x0 = MARGIN
    y = 22

    # Header
    sa = d.get("spell_ability", "")
    dc = d.get("spell_save_dc", 10)
    atk = d.get("spell_attack_bonus", 0)
    focus = "holy symbol" if d.get("class_name") in ("Cleric", "Paladin") else "arcane focus"
    c.setFont(FONT_BOLD, 9)
    c.drawString(x0, yb(y + 14), f"Spellcasting Ability: {sa}")
    c.drawString(x0 + 160, yb(y + 14), f"Spell Save DC: {dc}")
    c.drawString(x0 + 300, yb(y + 14), f"Spell Attack Bonus: {atk:+d}")

    y += 28

    # Get spells grouped by level
    spells = d.get("spells", [])
    by_level = {}
    for sp_name, sp_level, prepared in spells:
        by_level.setdefault(sp_level, []).append((sp_name, prepared))

    spell_slots = d.get("spell_slots", {})
    slot_used = d.get("spell_slots_used", {})

    # Cantrips section at top
    cantrips = by_level.get(0, [])
    if cantrips:
        _draw_label(c, x0, y, "Cantrips (0-level)")
        for i, (name, _) in enumerate(cantrips[:8]):
            c.setFont(FONT, 5.5)
            c.drawString(x0 + 8, yb(y + 10 + i * 12), name)
        y += 10 + len(cantrips[:8]) * 12 + 6

    # 3-column spell level blocks
    col_w = 188
    col_gap = 6
    block_h = 190
    block_gap = 6

    # Map levels to columns: 1,4,7 | 2,5,8 | 3,6,9
    level_col_map = {1: 0, 2: 1, 3: 2, 4: 0, 5: 1, 6: 2, 7: 0, 8: 1, 9: 2}
    row_per_col = {0: 0, 1: 0, 2: 0}

    y_start = y + 6

    for lvl in range(1, 10):
        col = level_col_map[lvl]
        row = row_per_col[col]
        row_per_col[col] += 1

        cx = x0 + col * (col_w + col_gap)
        cy_tl = y_start + row * (block_h + block_gap)

        total_slots = int(spell_slots.get(str(lvl), 0))
        used_slots = int(slot_used.get(str(lvl), 0))

        # Block border
        c.setStrokeColor((0, 0, 0))
        c.rect(cx, yb(cy_tl) - block_h, col_w, block_h)

        # Level header
        c.setFont(FONT_BOLD, 6)
        c.drawString(cx + 4, yb(cy_tl + 8), f"{lvl}{'st' if lvl==1 else 'nd' if lvl==2 else 'rd' if lvl==3 else 'th'} Level")

        # Slots Total + Expended bar
        c.setFont(FONT, 5)
        c.drawString(cx + 4, yb(cy_tl + 20), f"Slots Total: {total_slots}")
        c.drawString(cx + 80, yb(cy_tl + 20), f"Slots Expended: {used_slots}")

        # Prep circles for used slots
        for b in range(min(total_slots, 12)):
            bx = cx + 4 + (b % 6) * 12
            by = cy_tl + 28 + (b // 6) * 12
            _draw_bubble(c, bx + 5, by + 4, 4, filled=(b < used_slots))

        # Spell lines with prep circles
        level_spells = by_level.get(lvl, [])
        line_y_start = cy_tl + 52
        if total_slots > 6:
            line_y_start += 12  # second row of bubbles

        max_lines = int((block_h - (line_y_start - cy_tl)) / 13)
        for i, (sp_name, prepared) in enumerate(level_spells[:max_lines]):
            ly = line_y_start + i * 13
            # Prep circle
            _draw_bubble(c, cx + 8, ly + 2, 3.5, filled=prepared)
            # Horizontal line for spell name
            ln_y = yb(ly + 2)
            c.line(cx + 16, ln_y, cx + col_w - 4, ln_y)
            # Spell name
            c.setFont(FONT_BOLD if prepared else FONT, 5)
            c.drawString(cx + 18, ln_y + 1, trunc(sp_name, 28))


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

    c.save()

    if use_temp:
        with open(path, "rb") as f:
            data = f.read()
        os.unlink(path)
        return data

    return output_path
