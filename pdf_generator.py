"""
D&D 5e Printable Character Sheet PDF Generator — v3 (reportlab, pure vector).
Generates a professional, official-looking multi-page character sheet.

Page 1: Main stats (ability scores, skills, combat, features, equipment)
Page 2: Personality, backstory, treasure, allies
Page 3: Spellcasting (for casters only)

Uses reportlab with absolute point coordinates. No raster, no debug garbage.
"""
import json
import os
import sys
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.utils import simpleSplit

# ═══════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════
PAGE_W, PAGE_H = letter  # 612 x 792 points (US Letter)
FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
FONT_OBLIQUE = "Helvetica-Oblique"
GRAY_BG = 0.95  # very light gray for alternate rows

# Margins from top-left (user spec coordinates use top-left origin)
# Reportlab uses bottom-left, so we convert: rl_y = PAGE_H - tl_y
MARGIN = 18  # ~0.25 inch


def tl(pdf_canvas, x, y):
    """Convert top-left coordinates to reportlab bottom-left."""
    return (x, PAGE_H - y)


# ═══════════════════════════════════════════════════════════════
#  SPELL METADATA CACHE
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
    """Convert sqlite3.Row or tuple into structured dict for PDF generation."""
    if hasattr(row, "keys"):
        d = dict(row)
    else:
        # Tuple — need column names from somewhere
        # Try to get columns from the cursor or use a reasonable fallback
        raise TypeError("build_char_data requires sqlite3.Row, not tuple. "
                        "Set row_factory = sqlite3.Row on the connection.")

    # Parse JSON fields
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

    # String defaults
    for f in ["personality", "backstory", "alignment", "subrace", "subclass"]:
        d.setdefault(f, "")

    # Background data contains ideals, bonds, flaws sometimes
    bg_data = d.get("background_data", {}) or {}
    d["ideals"] = d.get("ideals", "") or bg_data.get("ideals", "") or ""
    d["bonds"] = d.get("bonds", "") or bg_data.get("bonds", "") or ""
    d["flaws"] = d.get("flaws", "") or bg_data.get("flaws", "") or ""

    # Derived values
    d["initiative"] = mod_int(d.get("dexterity", 10))
    d["passive_perception"] = d.get("passive_perception", 10) or 10
    d["inspiration"] = d.get("inspiration", 0) or 0
    d["proficiency_bonus"] = d.get("proficiency_bonus", 2) or 2

    # Hit dice
    hd = d.get("hit_dice", "1d8") or "1d8"
    d["hit_dice_type"] = hd
    d["hit_dice_total"] = d.get("level", 1)
    d["hit_dice_used"] = d.get("hit_dice_used", 0) or 0

    # Spell slots
    ss = d.get("spell_slot_data", {}) or {}
    d["spell_slots"] = ss.get("by_level", {})
    d["pact_slots"] = ss.get("pact_slots", {})
    d["spell_slots_used"] = d.get("spell_slots_used", {}) or {}

    # Currency
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

    # Infer spellcasting ability
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

    return d


# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════
def mod_int(score):
    return (score - 10) // 2


def mod_str(score):
    m = mod_int(score)
    return f"+{m}" if m >= 0 else str(m)


def trunc(text, max_len):
    text = str(text) if text else ""
    return text[:max_len]


def _y_tl(pdf, y_tl):
    """Convert top-left y to reportlab y (bottom-left origin)."""
    return PAGE_H - y_tl


def draw_label(c, x, y_tl, w, text, size=5):
    """Draw a section label above a field (y_tl is top-left origin)."""
    y = _y_tl(c, y_tl)
    c.setFont(FONT, size)
    c.drawString(x, y, str(text))


def draw_value_box(c, x, y_tl, w, h, text, size=9, bold=True, align="left", border_color=None):
    """Draw a bordered value box with text. y_tl is top-left origin."""
    y = _y_tl(c, y_tl) - h
    if border_color is None:
        border_color = (0, 0, 0)
    c.setStrokeColor(border_color)
    c.rect(x, y, w, h)
    c.setFillColor((255, 255, 255))
    c.setStrokeColor((0, 0, 0))

    font = FONT_BOLD if bold else FONT
    c.setFont(font, size)
    c.setFillColor((0, 0, 0))

    if align == "center":
        c.drawCentredString(x + w / 2, y + (h - size) / 2 + 2, trunc(str(text), 30))
    elif align == "right":
        c.drawRightString(x + w - 3, y + (h - size) / 2 + 2, trunc(str(text), 30))
    else:
        c.drawString(x + 3, y + (h - size) / 2 + 2, trunc(str(text), 30))


def draw_checkbox(c, x, y_tl, size=8, checked=False):
    """Draw a checkbox at top-left coordinates."""
    y = _y_tl(c, y_tl) - size
    c.setStrokeColor((0, 0, 0))
    c.rect(x, y, size, size)
    if checked:
        c.line(x + 1, y + 1, x + size - 1, y + size - 1)
        c.line(x + size - 1, y + 1, x + 1, y + size - 1)


def draw_bubble(c, x, y_tl, r=5, filled=False):
    """Draw a circular bubble (death saves) at top-left coordinates."""
    cy = _y_tl(c, y_tl)
    c.setStrokeColor((0, 0, 0))
    c.circle(x, cy, r)
    if filled:
        c.setFillColor((0, 0, 0))
        c.circle(x, cy, r, fill=1)
        c.setFillColor((255, 255, 255))


def draw_wrapped_text(c, x, y_tl, w, h, text, size=6, label_text=None, continuation=False):
    """Draw wrapped text inside a bordered box. Clips to height.
    Returns True if content overflowed (not fully displayed)."""
    y_bottom = _y_tl(c, y_tl) - h

    # Draw border rect
    c.setStrokeColor((0, 0, 0))
    c.rect(x, y_bottom, w, h)

    if not text:
        return False

    if label_text:
        c.setFont(FONT, 4)
        c.setFillColor((0.3, 0.3, 0.3))
        c.drawString(x + 2, _y_tl(c, y_tl) - 8, str(label_text))
        c.setFillColor((0, 0, 0))

    line_height = size + 2
    max_lines = int((h - 6) / line_height)
    if max_lines < 1:
        return False

    # Split text into lines that fit within width
    lines = simpleSplit(str(text), FONT, size, w - 6)
    display_lines = lines[:max_lines]
    overflow = len(lines) > max_lines

    if continuation:
        c.setFont(FONT_OBLIQUE, 5)
        c.setFillColor((0.4, 0.4, 0.4))
        c.drawString(x + 2, _y_tl(c, y_tl) - 10, "(continued)")
        c.setFillColor((0, 0, 0))

    c.setFont(FONT, size)
    c.setFillColor((0, 0, 0))
    for i, line in enumerate(display_lines):
        ly = _y_tl(c, y_tl) - 6 - (i + 1) * line_height
        c.drawString(x + 3, ly, line)

    if overflow:
        c.setFont(FONT_OBLIQUE, 4.5)
        c.setFillColor((0.5, 0.5, 0.5))
        c.drawString(x + 3, y_bottom + 2, "(continued on next page)")
        c.setFillColor((0, 0, 0))

    return overflow


# ═══════════════════════════════════════════════════════════════
#  PAGE 1 — MAIN CHARACTER SHEET
# ═══════════════════════════════════════════════════════════════
def draw_page1(c, d):
    draw_header(c, d)
    draw_ability_scores(c, d)
    draw_skills(c, d)
    draw_left_bottom(c, d)
    draw_combat(c, d)
    draw_attacks(c, d)
    result = draw_features_traits(c, d)
    draw_equipment(c, d)
    draw_currency(c, d)
    return result  # overflow info for page 2


def draw_header(c, d):
    """Rows at top: name, race, class, background, player, alignment, inspiration, PB."""
    y0 = 36  # 0.5 inch from top
    box_h = 18
    gap = 30

    # Row 1
    r1 = [
        (36, y0, 180, "Character Name", d.get("name", "")),
        (230, y0, 130, "Race", d.get("race", "")),
        (370, y0, 130, "Class & Level", f"{d.get('class_name','')} {d.get('level','')}"),
        (510, y0, 90, "Background", d.get("background", "")),
    ]
    for x, y, w, lbl, val in r1:
        draw_label(c, x, y - 10, w, lbl)
        draw_value_box(c, x, y, w, box_h, val)

    # Row 2
    y2 = y0 + gap
    r2 = [
        (36, y2, 180, "Player Name", ""),
        (230, y2, 130, "Faction", ""),
        (370, y2, 130, "Experience Points", ""),
        (510, y2, 90, "Alignment", d.get("alignment", "")),
    ]
    for x, y, w, lbl, val in r2:
        draw_label(c, x, y - 10, w, lbl)
        draw_value_box(c, x, y, w, box_h, val)

    # Row 3
    y3 = y2 + gap
    draw_label(c, 36, y3 - 10, 54, "Inspiration")
    draw_checkbox(c, 42, y3 + 4, 8, checked=bool(d.get("inspiration", 0)))
    draw_label(c, 108, y3 - 10, 54, "Prof. Bonus")
    draw_value_box(c, 108, y3, 36, box_h, str(d.get("proficiency_bonus", 2)), size=10, align="center")


def draw_ability_scores(c, d):
    """Six ability score blocks: STR/DEX/CON in col A, INT/WIS/CHA in col B."""
    abilities = [
        ("STR", "strength"), ("DEX", "dexterity"), ("CON", "constitution"),
        ("INT", "intelligence"), ("WIS", "wisdom"), ("CHA", "charisma"),
    ]
    save_profs = [s.lower() for s in (d.get("save_proficiencies", []) or [])]
    prof = d.get("proficiency_bonus", 2)
    block_w, block_h = 72, 86
    col_x = [36, 130]
    y_top = 108

    for i, (abbr, key) in enumerate(abilities):
        col = i // 3
        row = i % 3
        x = col_x[col]
        y_tl = y_top + row * (block_h + 8)

        score = d.get(key, 10)
        mod = mod_int(score)
        is_prof = abbr.lower() in save_profs
        save_mod = mod + (prof if is_prof else 0)

        # Border
        y_b = _y_tl(c, y_tl) - block_h
        c.setStrokeColor((0, 0, 0))
        c.rect(x, y_b, block_w, block_h)

        # Abbreviation
        c.setFont(FONT_BOLD, 7)
        c.drawCentredString(x + block_w / 2, _y_tl(c, y_tl + 9), abbr)

        # Score (large)
        c.setFont(FONT_BOLD, 16)
        c.drawString(x + 5, _y_tl(c, y_tl + 42), str(score))

        # Modifier box
        mod_x, mod_w = x + 46, 24
        c.rect(mod_x, _y_tl(c, y_tl + 18) - 28, mod_w, 28)
        c.setFont(FONT_BOLD, 9)
        c.drawCentredString(mod_x + mod_w / 2, _y_tl(c, y_tl + 42), f"{mod:+d}")

        # Save proficiency label + checkbox
        c.setFont(FONT, 4)
        c.drawString(x + 3, _y_tl(c, y_tl + 58), "SAVE PROF")
        draw_checkbox(c, x + 43, y_tl + 48, 8, checked=is_prof)

        # Save modifier
        c.setFont(FONT_BOLD, 7)
        c.drawCentredString(mod_x + mod_w / 2, _y_tl(c, y_tl + 64), f"{save_mod:+d}")


def draw_skills(c, d):
    """18 skills in two columns."""
    y0 = 360
    row_h = 18
    skills = d.get("skills", []) or []
    pb = d.get("proficiency_bonus", 2)
    scores = {
        "strength": d.get("strength", 10), "dexterity": d.get("dexterity", 10),
        "constitution": d.get("constitution", 10), "intelligence": d.get("intelligence", 10),
        "wisdom": d.get("wisdom", 10), "charisma": d.get("charisma", 10),
    }
    abbr_map = {"Str": "strength", "Dex": "dexterity", "Con": "constitution",
                 "Int": "intelligence", "Wis": "wisdom", "Cha": "charisma"}

    left = [
        ("Acrobatics", "Dex"), ("Animal Handling", "Wis"), ("Arcana", "Int"),
        ("Athletics", "Str"), ("Deception", "Cha"), ("History", "Int"),
        ("Insight", "Wis"), ("Intimidation", "Cha"), ("Investigation", "Int"),
    ]
    right = [
        ("Medicine", "Wis"), ("Nature", "Int"), ("Perception", "Wis"),
        ("Performance", "Cha"), ("Persuasion", "Cha"), ("Religion", "Int"),
        ("Sleight of Hand", "Dex"), ("Stealth", "Dex"), ("Survival", "Wis"),
    ]

    def draw_skill(x, y_tl, name, abbr):
        ab_mod = mod_int(scores[abbr_map[abbr]])
        is_prof = name in skills
        skill_mod = ab_mod + (pb if is_prof else 0)
        draw_checkbox(c, x, y_tl + 4, 7, checked=is_prof)
        c.setFont(FONT, 6)
        c.drawString(x + 10, _y_tl(c, y_tl + row_h - 4), f"{name} ({abbr})")
        c.setFont(FONT_BOLD, 7)
        c.drawRightString(x + 100, _y_tl(c, y_tl + row_h - 4), f"{skill_mod:+d}")

    for i, (name, abbr) in enumerate(left):
        draw_skill(36, y0 + i * row_h, name, abbr)
    for i, (name, abbr) in enumerate(right):
        draw_skill(130, y0 + i * row_h, name, abbr)


def draw_left_bottom(c, d):
    """Passive Perception + Other Proficiencies & Languages."""
    y = 540

    draw_label(c, 36, y, 100, "Passive Perception")
    draw_value_box(c, 36, y + 8, 44, 18, str(d.get("passive_perception", 10)), size=10, align="center")

    y2 = y + 36
    parts = []
    for label_name, field in [("Weapons", "weapon_proficiencies"), ("Armor", "armor_proficiencies"),
                               ("Tools", "tool_proficiencies"), ("Languages", "languages")]:
        items = d.get(field, []) or []
        if items:
            parts.append(f"{label_name}: {', '.join(items)}")
    text = "\n".join(parts) if parts else ""
    draw_wrapped_text(c, 36, y2 + 8, 160, 70, text, size=5)


def draw_combat(c, d):
    """Middle column: AC, Initiative, Speed, HP, Hit Dice, Death Saves."""
    x = 216
    y = 108

    # AC, Initiative, Speed
    draw_label(c, x + 14, y - 10, 56, "Armor Class")
    draw_value_box(c, x + 14, y, 56, 20, str(d.get("ac", 10)), size=11, align="center")
    draw_label(c, x + 90, y - 10, 48, "Initiative")
    draw_value_box(c, x + 90, y, 48, 20, f"{d.get('initiative', 0):+d}", size=9, align="center")
    draw_label(c, x + 152, y - 10, 36, "Speed")
    draw_value_box(c, x + 152, y, 36, 20, str(d.get("speed", 30)), size=9, align="center")

    # HP
    y_hp = y + 48
    draw_label(c, x + 14, y_hp - 10, 72, "Hit Point Max")
    draw_value_box(c, x + 14, y_hp, 72, 20, str(d.get("hp_max", 10)), size=11, align="center")
    draw_label(c, x + 100, y_hp - 10, 72, "Current HP")
    draw_value_box(c, x + 100, y_hp, 72, 20, str(d.get("hp_current", 10)), size=11, align="center")
    draw_label(c, x + 14, y_hp + 30, 72, "Temporary HP")
    draw_value_box(c, x + 14, y_hp + 40, 72, 18, str(d.get("temp_hp", 0)), size=9, align="center")

    # Hit Dice
    y_hd = y_hp + 90
    draw_label(c, x + 14, y_hd - 10, 108, "Hit Dice")
    hd_type = d.get("hit_dice_type", "1d8")
    total = d.get("hit_dice_total", 1)
    used = d.get("hit_dice_used", 0)
    draw_value_box(c, x + 14, y_hd, 108, 18, f"{total - used} / {total}   ({hd_type})", size=8, align="center")

    # Death Saves
    y_ds = y_hd + 32
    draw_label(c, x + 14, y_ds - 10, 100, "Death Saves")
    c.setFont(FONT, 4.5)
    c.drawString(x + 14, _y_tl(c, y_ds + 8), "Successes")
    succ = d.get("death_saves_success", 0) or 0
    for i in range(3):
        draw_bubble(c, x + 60 + i * 16, y_ds + 8, 5, filled=(i < succ))
    c.setFont(FONT, 4.5)
    c.drawString(x + 14, _y_tl(c, y_ds + 24), "Failures")
    fail = d.get("death_saves_fail", 0) or 0
    for i in range(3):
        draw_bubble(c, x + 60 + i * 16, y_ds + 24, 5, filled=(i < fail))


def draw_attacks(c, d):
    """Middle column: Attacks & Spellcasting table."""
    x = 216
    y_tl = 418
    col_w = [80, 36, 56]
    headers = ["Name", "Atk Bonus", "Damage/Type"]

    draw_label(c, x + 14, y_tl - 10, 172, "Attacks & Spellcasting")

    # Header row
    for i, (h, w) in enumerate(zip(headers, col_w)):
        cx = x + 14 + sum(col_w[:i])
        ry = _y_tl(c, y_tl) - 12
        c.setFont(FONT_BOLD, 5)
        c.rect(cx, ry, w, 12)
        c.drawString(cx + 2, ry + 3, h)

    # Data rows (6 rows)
    attacks = d.get("attacks_data", []) or []
    row_h = 17
    for ri in range(6):
        ry_tl = y_tl + 12 + ri * row_h
        atk = attacks[ri] if ri < len(attacks) else {}
        vals = [
            atk.get("name", ""),
            f"{atk.get('attack_bonus', 0):+d}" if atk.get("attack_bonus") is not None else "",
            atk.get("damage", ""),
        ]
        for j, (v, w) in enumerate(zip(vals, col_w)):
            cx = x + 14 + sum(col_w[:j])
            ry = _y_tl(c, ry_tl) - row_h
            c.setFont(FONT, 6)
            c.rect(cx, ry, w, row_h)
            c.drawString(cx + 2, ry + 4, trunc(str(v), 18))

    # Ammunition / notes field
    yn = y_tl + 12 + 6 * row_h + 4
    draw_label(c, x + 14, yn, 172, "Ammunition / Special Properties")
    yn_b = _y_tl(c, yn + 8) - 50
    c.rect(x + 14, yn_b, 172, 50)


def draw_features_traits(c, d):
    """Right column: Features & Traits with text wrapping."""
    x = 420
    y_tl = 108
    w = 168
    h = 400

    lines = []
    features = d.get("features", []) or []
    for f in features:
        if isinstance(f, dict):
            lines.append(f"{f.get('level', '')}: {f.get('name', '')}")
        elif isinstance(f, str) and f.strip():
            lines.append(f)

    feature_data = d.get("feature_data", []) or []
    for fd in feature_data:
        name = fd.get("name", "")
        desc = fd.get("description", "")
        if name:
            lines.append(name)
            if desc:
                lines.append(f"  {desc}")

    text = "\n".join(lines)
    overflow = draw_wrapped_text(c, x + 2, y_tl, w, h, text, size=5.5)

    if overflow:
        # Build remaining text for page 2
        remaining_text = ""
        c.setFont(FONT, 5.5)
        all_lines = simpleSplit(text, FONT, 5.5, w - 6)
        max_lines = int((h - 6) / 7.5)
        if len(all_lines) > max_lines:
            remaining_text = "\n".join(all_lines[max_lines:])
    else:
        remaining_text = ""

    draw_label(c, x + 2, y_tl - 10, w, "Features & Traits")
    return {"features_overflow": remaining_text}


def draw_equipment(c, d):
    """Right column: Equipment list."""
    x = 420
    y_tl = 520
    w = 168

    items = []
    inventory = d.get("inventory", []) or []
    equipped = d.get("equipped", []) or []
    attuned = d.get("attuned_items", []) or []

    for item in attuned:
        if isinstance(item, dict):
            items.append(f"[A] {item.get('name', '')} x{item.get('qty', 1)}")
    for item in equipped:
        if isinstance(item, dict):
            items.append(f"[E] {item.get('name', '')}")
    for item in inventory:
        if isinstance(item, dict):
            items.append(f"{item.get('name', '')} x{item.get('qty', 1)}")

    text = "\n".join(items)
    draw_wrapped_text(c, x + 2, y_tl + 2, w, 180, text, size=5.5)
    draw_label(c, x + 2, y_tl - 10, w, "Equipment")


def draw_currency(c, d):
    """Currency fields at bottom right."""
    x = 420
    y_tl = 710
    coins = [("CP", d.get("cp", 0)), ("SP", 0), ("EP", 0), ("GP", d.get("gp", 0)), ("PP", 0)]
    for i, (cn, cv) in enumerate(coins):
        cx = x + 2 + i * 34
        ry_label = _y_tl(c, y_tl) - 14
        ry_val = _y_tl(c, y_tl + 14) - 14
        c.setFont(FONT_BOLD, 5)
        c.rect(cx, ry_label, 32, 14)
        c.drawCentredString(cx + 16, ry_label + 4, cn)
        c.setFont(FONT, 6)
        c.rect(cx, ry_val, 32, 14)
        c.drawCentredString(cx + 16, ry_val + 4, str(cv))


# ═══════════════════════════════════════════════════════════════
#  PAGE 2 — PERSONALITY, BACKSTORY, TREASURE, ALLIES
# ═══════════════════════════════════════════════════════════════
def draw_page2(c, d, features_continued=""):
    """Two-column layout for personality, backstory, etc."""

    def section(x, y_tl, w, h, label_text, text, size=6):
        draw_label(c, x, y_tl - 10, w, label_text)
        return draw_wrapped_text(c, x, y_tl + 2, w, h - 2, text, size=size)

    # Left column
    section(18, 36, 288, 100, "Personality Traits", d.get("personality", ""))
    section(18, 148, 288, 72, "Ideals", d.get("ideals", ""))
    section(18, 232, 288, 72, "Bonds", d.get("bonds", ""))
    section(18, 316, 288, 72, "Flaws", d.get("flaws", ""))

    # Features & Traits continued
    if features_continued:
        section(18, 400, 288, 300, "Features & Traits (continued)", features_continued, size=5)

    # Right column
    inventory = d.get("inventory", []) or []
    items_lines = [f"{item.get('name', '')} x{item.get('qty', 1)}"
                   for item in inventory[:20] if isinstance(item, dict)]
    items_text = "\n".join(items_lines)
    section(324, 36, 270, 200, "Equipment (continued)", items_text, size=5.5)
    section(324, 248, 270, 150, "Treasure", "")
    section(324, 410, 270, 150, "Backstory", d.get("backstory", ""))
    section(324, 572, 270, 130, "Allies & Organizations", "")


# ═══════════════════════════════════════════════════════════════
#  PAGE 3 — SPELLCASTING
# ═══════════════════════════════════════════════════════════════
def draw_page3(c, d):
    """Spellcasting sheet with slots grid and prepared/known spells table."""
    x0 = 18
    y = 30

    # Header: Spellcasting Ability, DC, Attack Bonus
    sa = d.get("spell_ability", "")
    dc = d.get("spell_save_dc", 10)
    atk = d.get("spell_attack_bonus", 0)
    focus = "holy symbol" if d.get("class_name") in ("Cleric", "Paladin") else "arcane focus"
    c.setFont(FONT_BOLD, 9)
    c.drawString(x0, _y_tl(c, y + 14), f"Spellcasting Ability: {sa}")
    c.drawString(x0 + 160, _y_tl(c, y + 14), f"Spell Save DC: {dc}")
    c.drawString(x0 + 300, _y_tl(c, y + 14), f"Spell Attack Bonus: {atk:+d}")

    # Cantrips
    y += 28
    spells = d.get("spells", [])
    cantrips = [s for s in spells if s[1] == 0]
    if cantrips:
        draw_label(c, x0, y, 500, "Cantrips (0-level spells)")
        for i, sp in enumerate(cantrips[:10]):
            c.setFont(FONT, 6)
            c.drawString(x0 + 8, _y_tl(c, y + 12 + i * 14), sp[0])
        y += 12 + len(cantrips[:10]) * 14 + 8
    else:
        draw_label(c, x0, y, 500, "Cantrips: None")
        y += 16

    # Spell Slots Grid
    y_slots = y + 8
    draw_label(c, x0, y_slots, 500, "Spell Slots")
    spell_slots = d.get("spell_slots", {})
    slot_used = d.get("spell_slots_used", {})

    # Draw slots grid header
    c.setFont(FONT_BOLD, 5)
    grid_y = _y_tl(c, y_slots + 10) - 12
    for lvl in range(1, 10):
        lx = x0 + (lvl - 1) * 58
        c.rect(lx, grid_y, 54, 12)
        c.drawCentredString(lx + 27, grid_y + 3, f"Level {lvl}")

    # Slot totals
    total_y = grid_y - 16
    c.setFont(FONT, 7)
    for lvl in range(1, 10):
        lx = x0 + (lvl - 1) * 58
        total = int(spell_slots.get(str(lvl), 0))
        c.rect(lx, total_y, 54, 16)
        c.drawCentredString(lx + 27, total_y + 5, f"Total: {total}")

    # Used bubbles
    bubble_y = total_y - 6
    for lvl in range(1, 10):
        lx = x0 + (lvl - 1) * 58
        total = int(spell_slots.get(str(lvl), 0))
        used = int(slot_used.get(str(lvl), 0))
        if total > 0:
            for b in range(min(total, 10)):
                bx = lx + 8 + (b % 5) * 10
                by = bubble_y - (b // 5) * 14
                c.setStrokeColor((0, 0, 0))
                c.circle(bx, by, 4)
                if b < used:
                    c.setFillColor((0, 0, 0))
                    c.circle(bx, by, 4, fill=1)
                    c.setFillColor((255, 255, 255))
            bubble_y -= 2 + min((total - 1) // 5 + 1, 2) * 14

    # Spells Table
    y_spells = y_slots + 110
    draw_label(c, x0, y_spells, 500, "Known / Prepared Spells")

    # Fetch spell metadata
    spell_cache = _get_spell_cache()

    col_ws = [24, 140, 48, 56, 60, 52, 40]
    hdrs = ["Lvl", "Spell Name", "Range", "Casting Time", "Duration", "Components", "Conc."]

    # Table header
    header_y = _y_tl(c, y_spells + 10) - 10
    c.setFont(FONT_BOLD, 4.5)
    for j, (h, cw) in enumerate(zip(hdrs, col_ws)):
        cx = x0 + sum(col_ws[:j])
        c.rect(cx, header_y, cw, 10)
        c.drawString(cx + 2, header_y + 2, h)

    # Draw rows (up to 30)
    leveled_spells = [s for s in spells if s[1] > 0]
    row_h = 14
    rows_per_page = 30

    for i, sp in enumerate(leveled_spells[:rows_per_page]):
        sp_name, sp_level, prepared = sp[0], sp[1], sp[2]
        ry = header_y - (i + 1) * row_h

        # Look up metadata
        meta = spell_cache.get(sp_name.lower(), {})
        range_val = meta.get("range", "")
        casting = meta.get("casting_time", "")
        duration = meta.get("duration", "")
        comps = ", ".join(meta.get("components", [])) if meta.get("components") else ""
        conc = "C" if meta.get("concentration") else ""

        vals = [str(sp_level), sp_name, range_val, casting, duration, comps, conc]

        for j, (v, cw) in enumerate(zip(vals, col_ws)):
            cx = x0 + sum(col_ws[:j])
            c.rect(cx, ry, cw, row_h)
            font_name = FONT_BOLD if (prepared and j == 1) else FONT
            c.setFont(font_name, 4.5)
            c.drawString(cx + 2, ry + 3, trunc(str(v), 30 if j == 1 else 15))


# ═══════════════════════════════════════════════════════════════
#  MAIN GENERATOR
# ═══════════════════════════════════════════════════════════════
def generate_character_sheet(char_data, output_path=None):
    """Generate a multi-page character sheet PDF. Returns bytes or saves to output_path."""
    import tempfile

    use_temp = output_path is None
    path = output_path or tempfile.mktemp(suffix=".pdf")

    c = canvas.Canvas(path, pagesize=letter)
    c.setTitle(f"D&D 5e Character Sheet — {char_data.get('name', 'Character')}")
    c.setAuthor("Character Manager")

    # Page 1 — Main sheet
    features_result = draw_page1(c, char_data)
    c.showPage()

    # Page 2 — Personality & backstory
    features_continued = features_result.get("features_overflow", "") if features_result else ""
    draw_page2(c, char_data, features_continued)
    c.showPage()

    # Page 3 — Spellcasting (only for casters)
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
