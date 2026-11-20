"""Legacy three-column ReportLab character sheet (pages 1-2)."""

import os

from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

from .layout import (
    COL1_W,
    COL1_X,
    COL2_W,
    COL2_X,
    COL3_W,
    COL3_X,
    FONT,
    FONT_BOLD,
    GRID_Y,
    PAGE_H,
    _bubble,
    _checkbox,
    _label,
    _text_box,
    _value,
    trunc,
    yb,
)
from .shared import _get_item_description
from .spellbook import (
    draw_equipment_appendix,
    draw_page3,
    draw_page4_appendix,
    draw_spell_appendix,
)
from .text import mod_int


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
    for j, (h, cw) in enumerate(zip(["Name", "Atk", "Damage/Type"], col_w, strict=False)):
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
        for j, (v, cw) in enumerate(zip(vals, col_w, strict=False)):
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
