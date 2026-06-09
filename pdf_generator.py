"""
D&D 5e Printable Character Sheet PDF Generator — v2.
Uses fpdf2 with point coordinates (72pt/inch) for precise official-sheet alignment.
Multi-page: Page 1 (stats), Page 2 (personality/backstory), Page 3 (spellcasting).
"""
import json, math
from fpdf import FPDF

# ═══════════════════════════════════════════════════════════════
#  CONSTANTS — all in points (1 inch = 72pt)
# ═══════════════════════════════════════════════════════════════
PT = 1  # we use points as base unit
INCH = 72
PAGE_W = 8.5 * INCH   # 612
PAGE_H = 11 * INCH    # 792

FONT_LABEL = "Helvetica"
FONT_BODY = "Helvetica"
FONT_BOLD = "Helvetica"
FONT_MONO = "Courier"

# ═══════════════════════════════════════════════════════════════
#  SPELL METADATA CACHE
# ═══════════════════════════════════════════════════════════════
_SPELL_CACHE = None

def _get_spell_cache():
    global _SPELL_CACHE
    if _SPELL_CACHE is not None:
        return _SPELL_CACHE
    try:
        import sys
        sys.path.insert(0, '/home/james/dnd-campaign-expert')
        from engine.spells import _load_spell_cache
        raw = _load_spell_cache()
        _SPELL_CACHE = {}
        for s in raw:
            _SPELL_CACHE[s['name'].lower()] = s
    except Exception:
        _SPELL_CACHE = {}
    return _SPELL_CACHE


# ═══════════════════════════════════════════════════════════════
#  DATA BUILDER
# ═══════════════════════════════════════════════════════════════
def build_char_data(row, db_cursor=None):
    """Convert sqlite3.Row into structured dict for PDF generation."""
    d = dict(row)

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

    # Ensure defaults
    for field in json_fields:
        if d.get(field) is None:
            d[field] = {}

    d.setdefault("personality", "")
    d.setdefault("backstory", "")
    d.setdefault("alignment", "")
    d.setdefault("subrace", "")
    d.setdefault("subclass", "")

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

    # Spells
    d["spells"] = []
    d["is_caster"] = False
    if db_cursor:
        spells = db_cursor.execute(
            "SELECT spell_name, spell_level, prepared FROM character_spells WHERE character_id=? ORDER BY spell_level, spell_name",
            (d["id"],)
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
    spell_ab_mod = mod_int(ab_scores.get(sa, 10))
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

def checkbox(pdf, x, y, size=8, checked=False):
    pdf.rect(x, y, size, size)
    if checked:
        pdf.line(x + 1, y + 1, x + size - 1, y + size - 1)
        pdf.line(x + size - 1, y + 1, x + 1, y + size - 1)

def circle_bubble(pdf, x, y, r=4, filled=False):
    pdf.ellipse(x - r, y - r, r * 2, r * 2)
    if filled:
        pdf.set_fill_color(0)
        pdf.ellipse(x - r, y - r, r * 2, r * 2, style="F")
        pdf.set_fill_color(255)

def label(pdf, x, y, w, text, size=5):
    pdf.set_font(FONT_LABEL, "", size)
    pdf.set_xy(x, y)
    pdf.cell(w, 8, text, align="L")

def value_box(pdf, x, y, w, h, text, size=8, bold=True, align="L"):
    pdf.set_font(FONT_BOLD if bold else FONT_BODY, "B" if bold else "", size)
    pdf.set_xy(x, y)
    pdf.cell(w, h, trunc(str(text), 40), border=1, align=align)

def wrapped_text(pdf, x, y, w, h, text, size=6, line_h=None):
    """Draw multi-line text with border, clipping to height. Returns True if overflowed."""
    if not text:
        pdf.rect(x, y, w, h)
        return False
    if line_h is None:
        line_h = size + 1
    pdf.set_font(FONT_BODY, "", size)
    max_lines = int(h / line_h)
    # Write text into a temporary buffer to count lines
    # fpdf2 multi_cell will auto-wrap — we need to manually handle overflow
    pdf.set_xy(x, y)
    # We use a clipping approach: draw a rect, then write text within it
    # fpdf2 doesn't clip text, so we need to truncate manually
    lines = []
    words = text.split()
    current = ""
    test_pdf = FPDF(unit="pt", format="letter")
    test_pdf.set_font(FONT_BODY, "", size)
    for word in words:
        trial = f"{current} {word}".strip()
        if test_pdf.get_string_width(trial) < w - 4:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)

    overflow = len(lines) > max_lines
    display_lines = lines[:max_lines]
    if overflow and max_lines > 1:
        display_lines[-1] = display_lines[-1][:int(w / 4)] + "..."

    pdf.set_font(FONT_BODY, "", size)
    pdf.set_fill_color(255, 255, 255)
    pdf.rect(x, y, w, h, style="DF")
    pdf.set_xy(x + 2, y + 2)
    for i, line in enumerate(display_lines):
        pdf.set_xy(x + 2, y + 2 + i * line_h)
        pdf.cell(w - 4, line_h, line, align="L")

    if overflow and max_lines > 1:
        pdf.set_font(FONT_BODY, "I", 5)
        pdf.set_xy(x + 2, y + h - 8)
        pdf.cell(w - 4, 6, "(continued on next page)", align="R")

    return overflow


# ═══════════════════════════════════════════════════════════════
#  PAGE 1 — MAIN CHARACTER SHEET
# ═══════════════════════════════════════════════════════════════
def draw_page1(pdf, c):
    draw_header(pdf, c)
    draw_ability_scores(pdf, c)
    draw_skills(pdf, c)
    draw_left_bottom(pdf, c)
    draw_combat(pdf, c)
    draw_attacks(pdf, c)
    draw_features_traits(pdf, c)
    draw_equipment(pdf, c)
    draw_currency(pdf, c)


def draw_header(pdf, c):
    """Header rows at top of page 1."""
    y0 = 22  # ~0.3"
    box_h = 18
    gap = 30  # ~0.4" between rows

    # Row 1: Character Name, Race, Class & Level, Background
    r1 = [
        (36, y0, 180, "Character Name", c.get("name", "")),
        (230, y0, 130, "Race", c.get("race", "")),
        (370, y0, 130, "Class & Level", f"{c.get('class_name','')} {c.get('level','')}"),
        (510, y0, 90, "Background", c.get("background", "")),
    ]
    for x, y, w, lbl, val in r1:
        label(pdf, x, y - 9, w, lbl)
        value_box(pdf, x, y, w, box_h, val)

    # Row 2: Player Name, Faction, XP, Alignment
    y2 = y0 + gap
    r2 = [
        (36, y2, 180, "Player Name", ""),
        (230, y2, 130, "Faction", ""),
        (370, y2, 130, "Experience Points", ""),
        (510, y2, 90, "Alignment", c.get("alignment", "")),
    ]
    for x, y, w, lbl, val in r2:
        label(pdf, x, y - 9, w, lbl)
        value_box(pdf, x, y, w, box_h, val)

    # Row 3: Inspiration + Proficiency Bonus
    y3 = y2 + gap
    label(pdf, 36, y3 - 9, 54, "Inspiration")
    checkbox(pdf, 40, y3 + 4, 8, checked=bool(c.get("inspiration", 0)))
    label(pdf, 108, y3 - 9, 54, "Prof. Bonus")
    value_box(pdf, 108, y3, 36, box_h, str(c.get("proficiency_bonus", 2)), size=10, align="C")


def draw_ability_scores(pdf, c):
    """Six ability score blocks in two columns, left side."""
    block_w = 72
    block_h = 86
    col_a_x, col_b_x = 36, 130
    y_top = 108  # ~1.5"

    abilities = [
        ("STR", "strength"), ("DEX", "dexterity"), ("CON", "constitution"),
        ("INT", "intelligence"), ("WIS", "wisdom"), ("CHA", "charisma"),
    ]
    save_profs = [s.lower() for s in (c.get("save_proficiencies", []) or [])]
    prof_bonus = c.get("proficiency_bonus", 2)

    for i, (abbr, key) in enumerate(abilities):
        col = i // 3
        row = i % 3
        x = col_a_x if col == 0 else col_b_x
        y = y_top + row * (block_h + 8)

        score = c.get(key, 10)
        mod = mod_int(score)

        pdf.set_line_width(0.5)
        pdf.rect(x, y, block_w, block_h)

        # Ability abbreviation
        pdf.set_font(FONT_BOLD, "B", 7)
        pdf.set_xy(x, y + 3)
        pdf.cell(block_w, 12, abbr, align="C")

        # Score (large)
        pdf.set_font(FONT_BOLD, "B", 16)
        pdf.set_xy(x, y + 18)
        pdf.cell(44, 28, str(score), align="C")

        # Modifier (small box to right of score)
        pdf.set_font(FONT_BOLD, "B", 8)
        pdf.rect(x + 46, y + 18, 24, 28)
        pdf.set_xy(x + 46, y + 22)
        pdf.cell(24, 20, f"{mod:+d}", align="C")

        # Save proficiency
        is_prof = abbr.lower() in save_profs
        pdf.set_font(FONT_LABEL, "", 4)
        pdf.set_xy(x + 3, y + 50)
        pdf.cell(38, 8, "SAVE PROF", align="L")
        checkbox(pdf, x + 42, y + 48, 8, checked=is_prof)

        # Save modifier (total)
        save_mod = mod + (prof_bonus if is_prof else 0)
        pdf.set_font(FONT_BOLD, "B", 7)
        pdf.set_xy(x + 46, y + 50)
        pdf.cell(24, 14, f"{save_mod:+d}", align="C")


def draw_skills(pdf, c):
    """18 skills in two columns, left side."""
    y0 = 360  # ~5.0"
    row_h = 18
    skills = c.get("skills", []) or []
    pb = c.get("proficiency_bonus", 2)
    scores = {
        "strength": c.get("strength", 10), "dexterity": c.get("dexterity", 10),
        "constitution": c.get("constitution", 10), "intelligence": c.get("intelligence", 10),
        "wisdom": c.get("wisdom", 10), "charisma": c.get("charisma", 10),
    }
    abbr_map = {"Str": "strength", "Dex": "dexterity", "Con": "constitution",
                 "Int": "intelligence", "Wis": "wisdom", "Cha": "charisma"}

    skills_left = [
        ("Acrobatics", "Dex"), ("Animal Handling", "Wis"), ("Arcana", "Int"),
        ("Athletics", "Str"), ("Deception", "Cha"), ("History", "Int"),
        ("Insight", "Wis"), ("Intimidation", "Cha"), ("Investigation", "Int"),
    ]
    skills_right = [
        ("Medicine", "Wis"), ("Nature", "Int"), ("Perception", "Wis"),
        ("Performance", "Cha"), ("Persuasion", "Cha"), ("Religion", "Int"),
        ("Sleight of Hand", "Dex"), ("Stealth", "Dex"), ("Survival", "Wis"),
    ]

    def draw_skill(x, y, name, abbr):
        ability_key = abbr_map[abbr]
        ab_mod = mod_int(scores[ability_key])
        is_prof = name in skills
        skill_mod = ab_mod + (pb if is_prof else 0)
        checkbox(pdf, x, y + 4, 7, checked=is_prof)
        pdf.set_font(FONT_BODY, "", 6)
        pdf.set_xy(x + 10, y)
        pdf.cell(76, row_h, f"{name} ({abbr})", align="L")
        pdf.set_font(FONT_BOLD, "B", 7)
        pdf.set_xy(x + 86, y)
        pdf.cell(14, row_h, f"{skill_mod:+d}", align="R")

    for i, (name, abbr) in enumerate(skills_left):
        draw_skill(36, y0 + i * row_h, name, abbr)
    for i, (name, abbr) in enumerate(skills_right):
        draw_skill(130, y0 + i * row_h, name, abbr)


def draw_left_bottom(pdf, c):
    """Passive Perception + Other Proficiencies & Languages."""
    y = 540  # ~7.5"

    # Passive Perception
    label(pdf, 36, y, 90, "Passive Perception (Wisdom)")
    value_box(pdf, 36, y + 8, 44, 18, str(c.get("passive_perception", 10)), size=10, align="C")

    # Other Proficiencies & Languages
    y2 = y + 36
    label(pdf, 36, y2, 160, "Other Proficiencies & Languages")
    parts = []
    for label_name, field in [("Weapons", "weapon_proficiencies"), ("Armor", "armor_proficiencies"),
                               ("Tools", "tool_proficiencies"), ("Languages", "languages")]:
        items = c.get(field, []) or []
        if items:
            parts.append(f"{label_name}: {', '.join(items)}")
    text = "\n".join(parts) if parts else ""
    wrapped_text(pdf, 36, y2 + 8, 160, 54, text, size=5, line_h=10)


def draw_combat(pdf, c):
    """Middle column: AC, Initiative, Speed, HP, Hit Dice, Death Saves."""
    x = 216  # ~3.0"
    y = 108  # 1.5"

    def mid_box(mx, my, mw, mlbl, mval, msize=9):
        label(pdf, mx, my - 9, mw, mlbl)
        value_box(pdf, mx, my, mw, 20, str(mval), size=msize, align="C")

    # AC + Initiative + Speed
    mid_box(x + 14, y, 56, "Armor Class", c.get("ac", 10), 11)
    mid_box(x + 90, y, 48, "Initiative", f"{c.get('initiative', 0):+d}", 9)
    mid_box(x + 152, y, 36, "Speed", c.get("speed", 30), 9)

    # Hit Points
    y_hp = y + 48
    mid_box(x + 14, y_hp, 72, "Hit Point Max", c.get("hp_max", 10), 11)
    mid_box(x + 100, y_hp, 72, "Current HP", c.get("hp_current", 10), 11)
    mid_box(x + 14, y_hp + 40, 72, "Temporary HP", c.get("temp_hp", 0), 9)

    # Hit Dice
    y_hd = y_hp + 90
    label(pdf, x + 14, y_hd - 9, 108, "Hit Dice")
    hd_type = c.get("hit_dice_type", "1d8")
    total = c.get("hit_dice_total", 1)
    used = c.get("hit_dice_used", 0)
    value_box(pdf, x + 14, y_hd, 108, 18, f"{total - used} / {total}   ({hd_type})", size=8, align="C")

    # Death Saves
    y_ds = y_hd + 32
    label(pdf, x + 14, y_ds - 9, 100, "Death Saves")
    pdf.set_font(FONT_LABEL, "", 4.5)
    pdf.set_xy(x + 14, y_ds)
    pdf.cell(50, 8, "Successes", align="L")
    succ = c.get("death_saves_success", 0) or 0
    for i in range(3):
        circle_bubble(pdf, x + 60 + i * 16, y_ds + 8, 5, filled=(i < succ))
    pdf.set_font(FONT_LABEL, "", 4.5)
    pdf.set_xy(x + 14, y_ds + 16)
    pdf.cell(50, 8, "Failures", align="L")
    fail = c.get("death_saves_fail", 0) or 0
    for i in range(3):
        circle_bubble(pdf, x + 60 + i * 16, y_ds + 24, 5, filled=(i < fail))


def draw_attacks(pdf, c):
    """Middle column: Attacks & Spellcasting table."""
    x = 216
    y = 418  # ~5.8"
    col_w = [80, 36, 56]
    headers = ["Name", "Atk Bonus", "Damage/Type"]

    label(pdf, x + 14, y - 9, 172, "Attacks & Spellcasting")

    # Header row
    for i, (h, w) in enumerate(zip(headers, col_w)):
        cx = x + 14 + sum(col_w[:i])
        pdf.set_font(FONT_BOLD, "B", 5)
        pdf.set_xy(cx, y)
        pdf.cell(w, 12, h, border=1, align="C")

    # Data rows
    attacks = c.get("attacks_data", []) or []
    row_h = 17
    for ri in range(6):
        ry = y + 12 + ri * row_h
        atk = attacks[ri] if ri < len(attacks) else {}
        vals = [
            atk.get("name", ""),
            f"{atk.get('attack_bonus', 0):+d}" if atk.get("attack_bonus") is not None else "",
            atk.get("damage", ""),
        ]
        for j, (v, w) in enumerate(zip(vals, col_w)):
            cx = x + 14 + sum(col_w[:j])
            pdf.set_font(FONT_BODY, "", 6)
            pdf.set_xy(cx, ry)
            pdf.cell(w, row_h, trunc(v, 18), border=1, align="L" if j == 0 else "C")

    # Notes
    yn = y + 12 + 6 * row_h + 4
    label(pdf, x + 14, yn, 172, "Ammunition / Special Properties")
    pdf.rect(x + 14, yn + 8, 172, 50)


def draw_features_traits(pdf, c):
    """Right column: Features & Traits with text wrapping."""
    x = 420  # ~5.83"
    y = 108
    w = 168  # ~2.33"
    h = 400  # ~5.5"

    label(pdf, x + 2, y - 9, w, "Features & Traits")

    # Build text from features and feature_data
    lines = []
    features = c.get("features", []) or []
    for f in features:
        if isinstance(f, dict):
            lines.append(f"{f.get('level', '')}: {f.get('name', '')}")
        elif isinstance(f, str) and f.strip():
            lines.append(f)

    feature_data = c.get("feature_data", []) or []
    for fd in feature_data:
        name = fd.get("name", "")
        desc = fd.get("description", "")
        if name:
            lines.append(f"\n{name}")
            if desc:
                lines.append(f"  {desc}")

    text = "\n".join(lines) if lines else ""
    wrapped_text(pdf, x + 2, y, w, h, text, size=5.5, line_h=8)


def draw_equipment(pdf, c):
    """Right column: Equipment list."""
    x = 420
    y_eq = 520
    w = 168

    label(pdf, x + 2, y_eq - 9, w, "Equipment")

    items = []
    inventory = c.get("inventory", []) or []
    equipped = c.get("equipped", []) or []
    equipped_items = c.get("attuned_items", []) or []

    for item in equipped_items:
        if isinstance(item, dict):
            items.append(f"- [E] {item.get('name', '')} x{item.get('qty', 1)}")
    for item in equipped:
        if isinstance(item, dict):
            items.append(f"- [E] {item.get('name', '')}")
    for item in inventory:
        if isinstance(item, dict):
            items.append(f"- {item.get('name', '')} x{item.get('qty', 1)}")

    text = "\n".join(items) if items else ""
    wrapped_text(pdf, x + 2, y_eq + 2, w, 180, text, size=5.5, line_h=10)


def draw_currency(pdf, c):
    """Currency fields at bottom right."""
    x = 420
    y = 710
    coins = [("CP", c.get("cp", 0)), ("SP", 0), ("EP", 0), ("GP", c.get("gp", 0)), ("PP", 0)]
    for i, (cn, cv) in enumerate(coins):
        cx = x + 2 + i * 34
        pdf.set_font(FONT_BOLD, "B", 5)
        pdf.set_xy(cx, y)
        pdf.cell(32, 12, cn, border=1, align="C")
        pdf.set_font(FONT_BODY, "", 6)
        pdf.set_xy(cx, y + 12)
        pdf.cell(32, 12, str(cv), border=1, align="C")


# ═══════════════════════════════════════════════════════════════
#  PAGE 2 — PERSONALITY, TRAITS, BACKSTORY
# ═══════════════════════════════════════════════════════════════
def draw_page2(pdf, c):
    """Two-column layout for personality, backstory, treasure, allies."""
    def section(x, y, w, h, section_label, text, size=6, line_h=10):
        label(pdf, x, y - 9, w, section_label)
        return wrapped_text(pdf, x, y + 2, w, h - 2, str(text) if text else "", size=size, line_h=line_h)

    # Left column (x=18 to 306, w=288)
    section(18, 36, 288, 100, "Personality Traits", c.get("personality", ""))
    section(18, 148, 288, 72, "Ideals", c.get("ideals", ""))
    section(18, 232, 288, 72, "Bonds", c.get("bonds", ""))
    section(18, 316, 288, 72, "Flaws", c.get("flaws", ""))

    # Features & Traits continued
    feature_data = c.get("feature_data", []) or []
    remaining = []
    for fd in feature_data:
        name = fd.get("name", "")
        desc = fd.get("description", "")
        if name:
            remaining.append(f"{name}: {desc}" if desc else name)
    text = "\n".join(remaining[:40]) if remaining else ""
    section(18, 400, 288, 300, "Features & Traits (continued)", text, size=5, line_h=8)

    # Right column (x=324 to 594, w=270)
    # Equipment continued
    inventory = c.get("inventory", []) or []
    items_lines = [f"- {item.get('name', '')} x{item.get('qty', 1)}"
                   for item in inventory[:20] if isinstance(item, dict)]
    items_text = "\n".join(items_lines) if items_lines else ""
    section(324, 36, 270, 200, "Equipment (continued)", items_text, size=5.5, line_h=10)

    section(324, 248, 270, 150, "Treasure", "")
    section(324, 410, 270, 150, "Backstory", c.get("backstory", ""))
    section(324, 572, 270, 130, "Allies & Organizations", "")


# ═══════════════════════════════════════════════════════════════
#  PAGE 3 — SPELLCASTING
# ═══════════════════════════════════════════════════════════════
def draw_page3(pdf, c):
    """Spellcasting sheet with slots grid and prepared/known spells table."""
    x0 = 18
    y = 22

    sa = c.get("spell_ability", "CHA")
    dc = c.get("spell_save_dc", 10)
    atk = c.get("spell_attack_bonus", 0)
    pdf.set_font(FONT_BOLD, "B", 9)
    pdf.set_xy(x0, y)
    pdf.cell(400, 16, f"Spellcasting Ability: {sa}     Spell Save DC: {dc}     Spell Attack Bonus: {atk:+d}", align="L")

    # Cantrips
    y += 24
    spells = c.get("spells", [])
    cantrips = [s for s in spells if s[1] == 0]
    if cantrips:
        label(pdf, x0, y, 500, "Cantrips (0-level spells)")
        for i, sp in enumerate(cantrips[:10]):
            pdf.set_font(FONT_BODY, "", 6)
            pdf.set_xy(x0 + 8, y + 10 + i * 14)
            pdf.cell(300, 12, sp[0], align="L")
        y += 12 + len(cantrips[:10]) * 14 + 8
    else:
        label(pdf, x0, y, 500, "Cantrips: None")
        y += 16

    # Spell Slots Grid
    y_slots = y + 8
    label(pdf, x0, y_slots, 500, "Spell Slots")
    spell_slots = c.get("spell_slots", {})
    slot_used = c.get("spell_slots_used", {})

    for lvl in range(1, 10):
        lx = x0 + (lvl - 1) * 58
        total = int(spell_slots.get(str(lvl), 0))
        used = int(slot_used.get(str(lvl), 0))

        pdf.set_font(FONT_BOLD, "B", 5)
        pdf.set_xy(lx, y_slots + 10)
        pdf.cell(54, 10, f"Level {lvl}", border=1, align="C")
        pdf.set_font(FONT_BODY, "", 7)
        pdf.set_xy(lx, y_slots + 22)
        pdf.cell(54, 14, f"Total: {total}", border=1, align="C")

        # Bubbles for used slots
        for b in range(total):
            bx = lx + 5 + (b % 5) * 10
            by = y_slots + 40 + (b // 5) * 14
            circle_bubble(pdf, bx, by, 4, filled=(b < used))

    y_spells = y_slots + 90

    # Known/Prepared Spells Table
    label(pdf, x0, y_spells, 500, "Known / Prepared Spells")

    # Fetch spell metadata
    spell_cache = _get_spell_cache()

    col_ws = [24, 140, 48, 56, 60, 52, 40]  # Lvl, Name, Range, Casting, Duration, Comp, Conc
    hdrs = ["Lvl", "Spell Name", "Range", "Casting Time", "Duration", "Components", "Conc."]

    # Header
    for j, (h, cw) in enumerate(zip(hdrs, col_ws)):
        cx = x0 + sum(col_ws[:j])
        pdf.set_font(FONT_BOLD, "B", 4.5)
        pdf.set_xy(cx, y_spells + 10)
        pdf.cell(cw, 10, h, border=1, align="C")

    # Rows
    leveled_spells = [s for s in spells if s[1] > 0]
    row_h = 14
    rows_per_page = 30

    for i, sp in enumerate(leveled_spells[:rows_per_page]):
        sp_name, sp_level, prepared = sp[0], sp[1], sp[2]
        ry = y_spells + 22 + i * row_h

        # Look up spell metadata
        meta = spell_cache.get(sp_name.lower(), {})
        range_val = meta.get("range", "")
        casting = meta.get("casting_time", "")
        duration = meta.get("duration", "")
        comps = ", ".join(meta.get("components", []))
        conc = "C" if meta.get("concentration") else ""

        vals = [str(sp_level), sp_name, range_val, casting, duration, comps, conc]

        for j, (v, cw) in enumerate(zip(vals, col_ws)):
            cx = x0 + sum(col_ws[:j])
            is_bold = prepared and j == 1
            pdf.set_font(FONT_BOLD if is_bold else FONT_BODY, "B" if is_bold else "", 4.5)
            pdf.set_xy(cx, ry)
            pdf.cell(cw, row_h, trunc(v, 30 if j == 1 else 15), border=1, align="L" if j == 1 else "C")


# ═══════════════════════════════════════════════════════════════
#  MAIN GENERATOR
# ═══════════════════════════════════════════════════════════════
def generate_character_sheet(char_data, output_path=None):
    """Generate a multi-page character sheet PDF."""
    pdf = FPDF(unit="pt", format="letter")
    pdf.set_auto_page_break(auto=False)
    pdf.set_margin(18)

    # Page 1
    pdf.add_page()
    pdf.set_fill_color(255, 255, 255)
    draw_page1(pdf, char_data)

    # Page 2
    pdf.add_page()
    draw_page2(pdf, char_data)

    # Page 3 — only for casters
    if char_data.get("is_caster"):
        pdf.add_page()
        draw_page3(pdf, char_data)

    if output_path:
        pdf.output(output_path)
        return output_path
    return pdf.output()
