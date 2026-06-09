"""
D&D 5e Printable Character Sheet PDF Generator.
Uses fpdf2 with inch units to match official D&D sheet layout.
Multi-page: Page 1 (stats), Page 2 (personality/backstory), Page 3 (spellcasting).
"""
import json
from fpdf import FPDF

# ── Constants ──────────────────────────────────────────────────
PAGE_W, PAGE_H = 8.5, 11.0  # US Letter
MARGIN = 0.25  # page margin

# Ability score full names and abbreviations
ABILITY_MAP = [
    ("Strength", "STR"), ("Dexterity", "DEX"), ("Constitution", "CON"),
    ("Intelligence", "INT"), ("Wisdom", "WIS"), ("Charisma", "CHA"),
]

SKILLS_LEFT = [
    ("Acrobatics", "Dex"), ("Animal Handling", "Wis"), ("Arcana", "Int"),
    ("Athletics", "Str"), ("Deception", "Cha"), ("History", "Int"),
    ("Insight", "Wis"), ("Intimidation", "Cha"), ("Investigation", "Int"),
]
SKILLS_RIGHT = [
    ("Medicine", "Wis"), ("Nature", "Int"), ("Perception", "Wis"),
    ("Performance", "Cha"), ("Persuasion", "Cha"), ("Religion", "Int"),
    ("Sleight of Hand", "Dex"), ("Stealth", "Dex"), ("Survival", "Wis"),
]

# ── Helper: modifier string ────────────────────────────────────
def mod_str(score):
    m = (score - 10) // 2
    return f"+{m}" if m >= 0 else str(m)

def mod_int(score):
    return (score - 10) // 2

# ── Helper: truncate text ───────────────────────────────────────
def trunc(text, max_len):
    text = str(text) if text else ""
    return text[:max_len]

# ── Helper: draw checkbox ───────────────────────────────────────
def checkbox(pdf, x, y, size=0.12, checked=False):
    pdf.rect(x, y, size, size)
    if checked:
        pdf.line(x, y, x + size, y + size)
        pdf.line(x + size, y, x, y + size)

# ══════════════════════════════════════════════════════════════════
#  CHARACTER DATA BUILDER
# ══════════════════════════════════════════════════════════════════
def build_char_data(char_row, db_cursor=None):
    """Convert a DB character row into a structured dict for PDF generation."""
    cols = [
        "id", "user_id", "name", "race", "subrace", "class_name", "subclass",
        "level", "background", "alignment", "strength", "dexterity", "constitution",
        "intelligence", "wisdom", "charisma", "hp_max", "hp_current", "temp_hp",
        "ac", "speed", "proficiency_bonus", "hit_dice", "hit_dice_used",
        "death_saves_success", "death_saves_fail", "skills", "tool_proficiencies",
        "weapon_proficiencies", "armor_proficiencies", "languages", "features",
        "inventory", "equipped", "notes", "created_at",
    ]
    d = dict(zip(cols, char_row[:len(cols)]))
    # Remaining columns (vary by schema version)
    extra_cols = [
        "personality", "backstory", "feature_data", "attacks_data",
        "spell_slot_data", "passive_perception", "inspiration", "exhaustion",
        "portrait_url", "portrait_prompt", "save_proficiencies",
        "damage_resistances", "damage_immunities", "damage_vulnerabilities",
        "condition_immunities", "ideals", "bonds", "flaws",
        "class_levels", "equipped_items", "spell_ability", "death_saves_enabled",
        "exhaustion_level",
    ]
    for i, col in enumerate(extra_cols):
        idx = len(cols) + i
        if idx < len(char_row):
            d[col] = char_row[idx]
        else:
            d[col] = None

    # Parse JSON fields
    for field in ["skills", "tool_proficiencies", "weapon_proficiencies",
                  "armor_proficiencies", "languages", "features", "inventory",
                  "equipped", "feature_data", "attacks_data", "spell_slot_data",
                  "save_proficiencies", "damage_resistances", "damage_immunities",
                  "damage_vulnerabilities", "condition_immunities", "class_levels",
                  "equipped_items"]:
        val = d.get(field)
        if isinstance(val, str) and val:
            try:
                d[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass

    # Parse personality/bonds/flaws/ideals
    for f in ["personality", "backstory", "ideals", "bonds", "flaws"]:
        val = d.get(f)
        if val is None:
            d[f] = ""

    # Compute derived values
    d["initiative"] = mod_int(d.get("dexterity", 10))
    d["passive_perception"] = d.get("passive_perception", 10) or 10
    d["inspiration"] = d.get("inspiration", 0) or 0

    # Hit dice parsing
    hd = d.get("hit_dice", "1d8") or "1d8"
    d["hit_dice_type"] = hd.replace(str(d["level"]), "").strip()
    d["hit_dice_total"] = d["level"]
    d["hit_dice_used"] = d.get("hit_dice_used", 0) or 0

    # Spell slots
    ss = d.get("spell_slot_data", {}) or {}
    d["spell_slots"] = ss.get("by_level", {})
    d["pact_slots"] = ss.get("pact_slots", {})

    # Fetch spells from DB if cursor provided
    d["spells"] = []
    d["is_caster"] = False
    if db_cursor:
        spells = db_cursor.execute(
            "SELECT spell_name, spell_level, prepared FROM character_spells WHERE character_id=? ORDER BY spell_level, spell_name",
            (d["id"],)
        ).fetchall()
        if spells:
            d["spells"] = spells
            d["is_caster"] = True

    return d


# ══════════════════════════════════════════════════════════════════
#  PAGE 1 — MAIN CHARACTER SHEET
# ══════════════════════════════════════════════════════════════════
def draw_page1(pdf, c):
    """Draw the main character sheet (header, abilities, skills, combat, attacks)."""
    # ── Header ──
    draw_header(pdf, c)

    # ── Left Column: Ability Scores ──
    draw_ability_scores(pdf, c)

    # ── Left Column: Skills ──
    draw_skills(pdf, c)

    # ── Left Column: Passive Perception, Proficiencies ──
    draw_left_bottom(pdf, c)

    # ── Middle Column: Combat Stats, HP, Hit Dice, Death Saves ──
    draw_combat(pdf, c)

    # ── Middle Column: Attacks & Spellcasting ──
    draw_attacks(pdf, c)

    # ── Right Column: Features & Traits ──
    draw_features_traits(pdf, c)

    # ── Right Column: Equipment ──
    draw_equipment(pdf, c)


# ── Header ──────────────────────────────────────────────────────
def draw_header(pdf, c):
    """Top header: character name, race, class, background, player, XP, alignment."""
    y0 = 0.3
    box_h = 0.28
    label_off = 0.12  # label sits above the box

    def labeled_box(x, y, w, label, value, font_size=8):
        pdf.set_font("Helvetica", "", 5)
        pdf.set_xy(x, y - label_off)
        pdf.cell(w, label_off, label, align="L")
        pdf.set_font("Helvetica", "B", font_size)
        pdf.set_xy(x, y)
        pdf.cell(w, box_h, trunc(value, 25), border=1, align="L")

    # Row 1
    labeled_box(0.5, y0, 2.5, "Character Name", c.get("name", ""))
    labeled_box(3.2, y0, 1.8, "Race", c.get("race", ""))
    labeled_box(5.2, y0, 1.8, "Class & Level", f"{c.get('class_name','')} {c.get('level','')}")
    labeled_box(7.2, y0, 1.3, "Background", c.get("background", ""))

    # Row 2
    y2 = y0 + 0.4
    labeled_box(0.5, y2, 2.5, "Player Name", "")
    labeled_box(3.2, y2, 1.8, "Faction", "")
    labeled_box(5.2, y2, 1.8, "Experience Points", "")
    labeled_box(7.2, y2, 1.3, "Alignment", c.get("alignment", ""))

    # Row 3 - Inspiration + Proficiency Bonus
    y3 = y2 + 0.4
    pdf.set_font("Helvetica", "", 5)
    pdf.set_xy(0.5, y3 - label_off)
    pdf.cell(0.8, label_off, "Inspiration", align="L")
    checkbox(pdf, 0.55, y3 + 0.03, 0.14, checked=bool(c.get("inspiration", 0)))

    pdf.set_font("Helvetica", "", 5)
    pdf.set_xy(1.5, y3 - label_off)
    pdf.cell(0.6, label_off, "Prof. Bonus", align="L")
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_xy(1.5, y3)
    pdf.cell(0.5, box_h, str(c.get("proficiency_bonus", 2)), border=1, align="C")


# ── Ability Scores ──────────────────────────────────────────────
def draw_ability_scores(pdf, c):
    """Six ability score blocks in the left column."""
    y_start = 1.5
    block_w = 1.0
    block_h = 1.2
    col2_x = 1.7

    positions = [
        (0.5, y_start), (0.5, y_start + 1.3), (0.5, y_start + 2.6),
        (col2_x, y_start), (col2_x, y_start + 1.3), (col2_x, y_start + 2.6),
    ]

    abilities = ["strength", "dexterity", "constitution",
                 "intelligence", "wisdom", "charisma"]
    abbrs = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]
    save_profs = c.get("save_proficiencies", []) or []

    for i, (x, y) in enumerate(positions):
        abil = abilities[i]
        score = c.get(abil, 10)
        mod = mod_int(score)

        # Box outline
        pdf.set_line_width(0.008)
        pdf.rect(x, y, block_w, block_h)

        # Ability abbreviation
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_xy(x, y + 0.05)
        pdf.cell(block_w, 0.2, abbrs[i], align="C")

        # Score
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_xy(x, y + 0.28)
        pdf.cell(0.6, 0.4, str(score), align="C")

        # Modifier
        pdf.set_font("Helvetica", "B", 8)
        pdf.rect(x + 0.62, y + 0.28, 0.35, 0.4)
        pdf.set_xy(x + 0.62, y + 0.32)
        pdf.cell(0.35, 0.32, f"{mod:+d}", align="C")

        # Save proficiency checkbox
        is_prof = abbrs[i].lower() in [s.lower() for s in save_profs]
        pdf.set_font("Helvetica", "", 4)
        pdf.set_xy(x + 0.05, y + 0.72)
        pdf.cell(0.5, 0.15, "SAVE PROF", align="L")
        checkbox(pdf, x + 0.52, y + 0.72, 0.1, checked=is_prof)

        # Save modifier
        save_mod = mod + (c.get("proficiency_bonus", 2) if is_prof else 0)
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_xy(x + 0.62, y + 0.72)
        pdf.cell(0.35, 0.2, f"{save_mod:+d}", align="C")


# ── Skills ──────────────────────────────────────────────────────
def draw_skills(pdf, c):
    """18 skills in two columns."""
    y_start = 5.0
    row_h = 0.27
    skills = c.get("skills", []) or []
    prof_bonus = c.get("proficiency_bonus", 2)
    abilities = {
        "strength": c.get("strength", 10), "dexterity": c.get("dexterity", 10),
        "constitution": c.get("constitution", 10), "intelligence": c.get("intelligence", 10),
        "wisdom": c.get("wisdom", 10), "charisma": c.get("charisma", 10),
    }

    def skill_row(x, y, name, ability_abbr, skills_list, prof_bonus):
        ability_key = {"Str": "strength", "Dex": "dexterity", "Con": "constitution",
                       "Int": "intelligence", "Wis": "wisdom", "Cha": "charisma"}[ability_abbr]
        ab_mod = mod_int(abilities[ability_key])
        is_prof = name in skills_list
        skill_mod = ab_mod + (prof_bonus if is_prof else 0)

        checkbox(pdf, x, y + 0.06, 0.1, checked=is_prof)
        pdf.set_font("Helvetica", "", 6)
        pdf.set_xy(x + 0.15, y)
        pdf.cell(1.0, row_h, f"{name} ({ability_abbr})", align="L")
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_xy(x + 1.08, y)
        pdf.cell(0.2, row_h, f"{skill_mod:+d}", align="R")

    # Left column
    for i, (name, abbr) in enumerate(SKILLS_LEFT):
        skill_row(0.5, y_start + i * row_h, name, abbr, skills, prof_bonus)

    # Right column
    for i, (name, abbr) in enumerate(SKILLS_RIGHT):
        skill_row(1.7, y_start + i * row_h, name, abbr, skills, prof_bonus)


# ── Left column bottom: Passive Perception, Proficiencies ───────
def draw_left_bottom(pdf, c):
    """Passive Perception + Other Proficiencies & Languages."""
    y = 7.8

    # Passive Perception
    pdf.set_font("Helvetica", "", 5)
    pdf.set_xy(0.5, y)
    pdf.cell(1.2, 0.12, "Passive Perception (Wisdom)", align="L")
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_xy(0.5, y + 0.12)
    pdf.cell(0.6, 0.25, str(c.get("passive_perception", 10)), border=1, align="C")

    # Other Proficiencies & Languages
    y2 = y + 0.5
    pdf.set_font("Helvetica", "", 5)
    pdf.set_xy(0.5, y2)
    pdf.cell(2.0, 0.12, "Other Proficiencies & Languages", align="L")

    prof_text = []
    for cat, items in [
        ("Weapons", c.get("weapon_proficiencies", [])),
        ("Armor", c.get("armor_proficiencies", [])),
        ("Tools", c.get("tool_proficiencies", [])),
        ("Languages", c.get("languages", [])),
    ]:
        if items:
            prof_text.append(f"{cat}: {', '.join(items)}")
    text = "\n".join(prof_text) if prof_text else ""

    pdf.set_font("Helvetica", "", 5.5)
    pdf.set_xy(0.5, y2 + 0.14)
    pdf.multi_cell(2.0, 0.14, text, border=1)


# ── Combat Stats ────────────────────────────────────────────────
def draw_combat(pdf, c):
    """Middle column: AC, Initiative, Speed, HP, Hit Dice, Death Saves."""
    x = 3.0
    y = 1.5

    def labeled_box(x, y, w, label, value, font_size=9):
        pdf.set_font("Helvetica", "", 5)
        pdf.set_xy(x, y - 0.1)
        pdf.cell(w, 0.1, label, align="L")
        pdf.set_font("Helvetica", "B", font_size)
        pdf.set_xy(x, y)
        pdf.cell(w, 0.3, str(value), border=1, align="C")

    # AC
    labeled_box(3.2, y, 0.7, "Armor Class", c.get("ac", 10), 11)
    # Initiative
    init = c.get("initiative", 0)
    pdf.set_font("Helvetica", "", 5)
    pdf.set_xy(4.3, y - 0.1)
    pdf.cell(0.7, 0.1, "Initiative", align="L")
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(4.3, y)
    pdf.cell(0.5, 0.3, f"{init:+d}", border=1, align="C")
    # Speed
    pdf.set_font("Helvetica", "", 5)
    pdf.set_xy(5.1, y - 0.1)
    pdf.cell(0.5, 0.1, "Speed", align="L")
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(5.1, y)
    pdf.cell(0.5, 0.3, str(c.get("speed", 30)), border=1, align="C")

    # ── Hit Points ──
    y_hp = y + 0.6
    labeled_box(3.2, y_hp, 1.0, "Hit Point Maximum", c.get("hp_max", 10), 11)
    labeled_box(4.4, y_hp, 1.0, "Current HP", c.get("hp_current", 10), 11)
    pdf.set_font("Helvetica", "", 5)
    pdf.set_xy(3.2, y_hp + 0.4)
    pdf.cell(1.0, 0.1, "Temporary HP", align="L")
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(3.2, y_hp + 0.5)
    pdf.cell(1.0, 0.25, str(c.get("temp_hp", 0)), border=1, align="C")

    # ── Hit Dice ──
    y_hd = y_hp + 0.9
    pdf.set_font("Helvetica", "", 5)
    pdf.set_xy(3.2, y_hd - 0.1)
    pdf.cell(1.5, 0.1, "Hit Dice", align="L")
    hd_type = c.get("hit_dice_type", "d8")
    hd_total = c.get("hit_dice_total", 1)
    hd_used = c.get("hit_dice_used", 0)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_xy(3.2, y_hd)
    pdf.cell(1.5, 0.25, f"{hd_total - hd_used} / {hd_total}   ({hd_type})", border=1, align="C")

    # ── Death Saves ──
    y_ds = y_hd + 0.45
    pdf.set_font("Helvetica", "", 5)
    pdf.set_xy(3.2, y_ds - 0.1)
    pdf.cell(1.0, 0.1, "Death Saves", align="L")

    pdf.set_font("Helvetica", "", 4.5)
    pdf.set_xy(3.2, y_ds)
    pdf.cell(0.8, 0.12, "Successes", align="L")
    for i in range(3):
        checkbox(pdf, 3.9 + i * 0.18, y_ds, 0.12, checked=(i < c.get("death_saves_success", 0)))

    pdf.set_font("Helvetica", "", 4.5)
    pdf.set_xy(3.2, y_ds + 0.18)
    pdf.cell(0.8, 0.12, "Failures", align="L")
    for i in range(3):
        checkbox(pdf, 3.9 + i * 0.18, y_ds + 0.18, 0.12, checked=(i < c.get("death_saves_fail", 0)))


# ── Attacks & Spellcasting ──────────────────────────────────────
def draw_attacks(pdf, c):
    """Middle column: attacks table."""
    x = 3.0
    y = 5.8
    col_w = [1.0, 0.5, 0.7]
    headers = ["Name", "Atk Bonus", "Damage/Type"]

    pdf.set_font("Helvetica", "B", 5)
    pdf.set_xy(x + 0.2, y - 0.12)
    pdf.cell(2.2, 0.12, "Attacks & Spellcasting", align="L")

    # Table header
    for i, (h, w) in enumerate(zip(headers, col_w)):
        pdf.set_font("Helvetica", "B", 5)
        px = x + 0.2 + sum(col_w[:i])
        pdf.set_xy(px, y)
        pdf.cell(w, 0.2, h, border=1, align="C")

    # Table rows
    attacks = c.get("attacks_data", []) or []
    row_h = 0.25
    for row_idx in range(6):
        ry = y + 0.2 + row_idx * row_h
        atk = attacks[row_idx] if row_idx < len(attacks) else {}
        vals = [
            atk.get("name", ""),
            f"{atk.get('attack_bonus', 0):+d}" if atk.get("attack_bonus") is not None else "",
            atk.get("damage", ""),
        ]
        for i, (v, w) in enumerate(zip(vals, col_w)):
            pdf.set_font("Helvetica", "", 6)
            px = x + 0.2 + sum(col_w[:i])
            pdf.set_xy(px, ry)
            pdf.cell(w, row_h, trunc(v, 18), border=1, align="L" if i == 0 else "C")

    # Notes area below attacks
    pdf.set_font("Helvetica", "", 5)
    pdf.set_xy(x + 0.2, y + 2.0)
    pdf.cell(2.2, 0.1, "Ammunition / Special Properties", align="L")
    pdf.set_font("Helvetica", "", 5.5)
    pdf.set_xy(x + 0.2, y + 2.1)
    pdf.multi_cell(2.2, 0.14, "", border=1)


# ── Features & Traits ───────────────────────────────────────────
def draw_features_traits(pdf, c):
    """Right column: Features & Traits text area."""
    x = 5.8
    y = 1.5
    pdf.set_font("Helvetica", "", 5)
    pdf.set_xy(x + 0.2, y - 0.12)
    pdf.cell(2.0, 0.12, "Features & Traits", align="L")

    # Collect features
    features = c.get("features", []) or []
    feature_data = c.get("feature_data", []) or []
    lines = []
    if features:
        for f in features:
            if isinstance(f, dict):
                lines.append(f"{f.get('level', '')}: {f.get('name', '')}")
            else:
                lines.append(str(f))
    if feature_data:
        for fd in feature_data:
            name = fd.get("name", "")
            desc = fd.get("description", "")
            if name and desc:
                lines.append(f"{name}: {desc}")

    text = "\n".join(lines) if lines else ""
    pdf.set_font("Helvetica", "", 5)
    pdf.set_xy(x + 0.2, y)
    pdf.multi_cell(2.0, 0.12, text, border=1)


# ── Equipment ───────────────────────────────────────────────────
def draw_equipment(pdf, c):
    """Right column: Equipment + currency."""
    x = 5.8
    y = 6.4

    pdf.set_font("Helvetica", "", 5)
    pdf.set_xy(x + 0.2, y - 0.12)
    pdf.cell(2.0, 0.12, "Equipment", align="L")

    # Inventory items
    inventory = c.get("inventory", []) or []
    equipped = c.get("equipped", []) or []
    equipped_items = c.get("equipped_items", []) or []

    lines = []
    # Equipped first
    for item in equipped_items:
        if isinstance(item, dict):
            lines.append(f"[E] {item.get('name', '')} x{item.get('qty', 1)}")
    for item in equipped:
        if isinstance(item, dict):
            lines.append(f"[E] {item.get('name', '')}")
    for item in inventory:
        if isinstance(item, dict):
            lines.append(f"{item.get('name', '')} x{item.get('qty', 1)}")

    text = "\n".join(lines) if lines else ""
    pdf.set_font("Helvetica", "", 5)
    pdf.set_xy(x + 0.2, y)
    pdf.multi_cell(2.0, 0.12, text, border=1)

    # Currency
    y_coin = y + 3.2
    pdf.set_font("Helvetica", "", 4.5)
    coins = [("CP", 0), ("SP", 0), ("EP", 0), ("GP", 0), ("PP", 0)]
    for i, (label, val) in enumerate(coins):
        cx = x + 0.2 + i * 0.42
        pdf.set_xy(cx, y_coin)
        pdf.cell(0.4, 0.15, label, border=1, align="C")
        pdf.set_xy(cx, y_coin + 0.15)
        pdf.cell(0.4, 0.15, str(val), border=1, align="C")


# ══════════════════════════════════════════════════════════════════
#  PAGE 2 — PERSONALITY, TRAITS, BACKSTORY
# ══════════════════════════════════════════════════════════════════
def draw_page2(pdf, c):
    """Draw personality traits, ideals, bonds, flaws, backstory, treasure, allies."""

    def text_section(x, y, w, h, label, value):
        pdf.set_font("Helvetica", "", 5)
        pdf.set_xy(x, y - 0.12)
        pdf.cell(w, 0.12, label, align="L")
        pdf.set_font("Helvetica", "", 5.5)
        pdf.set_xy(x, y)
        pdf.multi_cell(w, h, str(value) if value else "", border=1)

    # ── Left Column ──
    text_section(0.5, 0.5, 3.5, 0.14, "Personality Traits", c.get("personality", ""))
    text_section(0.5, 2.1, 3.5, 0.14, "Ideals", c.get("ideals", ""))
    text_section(0.5, 3.3, 3.5, 0.14, "Bonds", c.get("bonds", ""))
    text_section(0.5, 4.5, 3.5, 0.14, "Flaws", c.get("flaws", ""))

    # Features & Traits continued
    feature_data = c.get("feature_data", []) or []
    remaining = []
    for fd in feature_data:
        name = fd.get("name", "")
        desc = fd.get("description", "")
        if name:
            remaining.append(f"{name}: {desc}" if desc else name)
    text = "\n".join(remaining[:30]) if remaining else ""
    text_section(0.5, 5.8, 3.5, 0.11, "Features & Traits (continued)", text)

    # ── Right Column ──
    # Equipment continued
    inventory = c.get("inventory", []) or []
    items_text = "\n".join(
        f"{item.get('name', '')} x{item.get('qty', 1)}"
        for item in inventory[:20] if isinstance(item, dict)
    )
    text_section(4.5, 0.5, 3.5, 0.12, "Equipment (continued)", items_text)
    text_section(4.5, 3.2, 3.5, 0.12, "Treasure", "")
    text_section(4.5, 5.0, 3.5, 0.12, "Backstory", c.get("backstory", ""))
    text_section(4.5, 8.0, 3.5, 0.14, "Allies & Organizations", "")


# ══════════════════════════════════════════════════════════════════
#  PAGE 3 — SPELLCASTING (for spellcasters)
# ══════════════════════════════════════════════════════════════════
def draw_page3(pdf, c):
    """Draw spellcasting sheet: spell slots, cantrips, prepared/known spells."""
    x0 = 0.5
    y = 0.3

    # ── Header: Spellcasting Ability ──
    spell_ability = c.get("spell_ability", "") or ""
    if not spell_ability:
        # Infer from class
        class_to_ability = {
            "Bard": "CHA", "Cleric": "WIS", "Druid": "WIS",
            "Paladin": "CHA", "Ranger": "WIS", "Sorcerer": "CHA",
            "Warlock": "CHA", "Wizard": "INT",
        }
        spell_ability = class_to_ability.get(c.get("class_name", ""), "")

    # Compute spell DC and attack bonus
    ability_scores = {
        "STR": c.get("strength", 10), "DEX": c.get("dexterity", 10),
        "CON": c.get("constitution", 10), "INT": c.get("intelligence", 10),
        "WIS": c.get("wisdom", 10), "CHA": c.get("charisma", 10),
    }
    spell_ab_mod = mod_int(ability_scores.get(spell_ability, 10))
    prof = c.get("proficiency_bonus", 2)
    spell_dc = 8 + prof + spell_ab_mod
    spell_atk = prof + spell_ab_mod

    pdf.set_font("Helvetica", "B", 8)
    pdf.set_xy(x0, y)
    pdf.cell(3.0, 0.25, f"Spellcasting Ability: {spell_ability}   DC: {spell_dc}   Atk Bonus: {spell_atk:+d}", align="L")

    # ── Cantrips ──
    y += 0.4
    pdf.set_font("Helvetica", "", 5)
    pdf.set_xy(x0, y)
    pdf.cell(7.5, 0.12, "Cantrips (0-level spells)", align="L")

    spells = c.get("spells", [])
    cantrips = [s for s in spells if s[1] == 0]
    for i, sp in enumerate(cantrips[:10]):
        sp_name, sp_level, prepared = sp[0], sp[1], sp[2]
        ry = y + 0.14 + i * 0.18
        pdf.set_font("Helvetica", "", 5.5)
        pdf.set_xy(x0, ry)
        pdf.cell(3.5, 0.16, f"  {sp_name}", align="L")

    # ── Spell Slots Grid ──
    y_slots = y + 2.2
    pdf.set_font("Helvetica", "", 5)
    pdf.set_xy(x0, y_slots)
    pdf.cell(7.5, 0.12, "Spell Slots", align="L")

    spell_slots = c.get("spell_slots", {})
    pact_slots = c.get("pact_slots", {})

    slot_levels = list(range(1, 10))
    col_w = 0.8
    for i, lvl in enumerate(slot_levels):
        cx = x0 + i * col_w
        sl_total = int(spell_slots.get(str(lvl), 0))
        # Pact slots shown separately
        pact = pact_slots.get(str(lvl), {})

        pdf.set_font("Helvetica", "", 4.5)
        pdf.set_xy(cx, y_slots + 0.14)
        pdf.cell(col_w - 0.05, 0.12, f"Lvl {lvl}", border=1, align="C")
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_xy(cx, y_slots + 0.28)
        pdf.cell(col_w - 0.05, 0.2, str(sl_total), border=1, align="C")
        # Bubbles for slots
        for b in range(sl_total):
            bubble_x = cx + 0.08 + (b % 5) * 0.13
            bubble_y = y_slots + 0.52 + (b // 5) * 0.15
            pdf.circle(bubble_x, bubble_y, 0.04)

    # Pact Magic note
    if pact_slots:
        pdf.set_font("Helvetica", "", 5)
        pact_info = pact_slots.get("slots", pact_slots.get("level", ""))
        pdf.set_xy(x0, y_slots + 1.8)
        pdf.cell(7.5, 0.12, f"Pact Magic: {pact_info}", align="L")

    # ── Known/Prepared Spells Table ──
    y_spells = y_slots + 2.2
    pdf.set_font("Helvetica", "", 5)
    pdf.set_xy(x0, y_spells)
    pdf.cell(7.5, 0.12, "Known / Prepared Spells", align="L")

    # Table header
    col_w_spells = [0.4, 2.5, 0.8, 1.0, 1.0, 0.7, 0.8]
    hdrs = ["Lvl", "Spell Name", "Range", "Casting", "Duration", "Comp.", "Conc."]
    for i, (h, w) in enumerate(zip(hdrs, col_w_spells)):
        cx = x0 + sum(col_w_spells[:i])
        pdf.set_font("Helvetica", "B", 4.5)
        pdf.set_xy(cx, y_spells + 0.14)
        pdf.cell(w, 0.14, h, border=1, align="C")

    # Body
    leveled_spells = [s for s in spells if s[1] > 0]
    for i, sp in enumerate(leveled_spells[:30]):
        sp_name, sp_level, prepared = sp[0], sp[1], sp[2]
        ry = y_spells + 0.30 + i * 0.16
        vals = [str(sp_level), sp_name, "", "", "", "", ""]

        # Highlight prepared spells
        is_prepared = prepared if prepared else False
        for j, (v, w) in enumerate(zip(vals, col_w_spells)):
            cx = x0 + sum(col_w_spells[:j])
            pdf.set_font("Helvetica", "B" if is_prepared and j == 1 else "", 5)
            pdf.set_xy(cx, ry)
            pdf.cell(w, 0.14, trunc(v, 20), border=1, align="L" if j == 1 else "C")


# ══════════════════════════════════════════════════════════════════
#  MAIN GENERATOR
# ══════════════════════════════════════════════════════════════════
def generate_character_sheet(char_data, output_path=None):
    """Generate a multi-page character sheet PDF. Returns bytes if no output_path."""
    pdf = FPDF(unit="in", format="letter")
    pdf.set_auto_page_break(auto=False)
    pdf.set_margin(MARGIN)

    # Page 1 — Main character sheet
    pdf.add_page()
    draw_page1(pdf, char_data)

    # Page 2 — Personality & Backstory
    pdf.add_page()
    draw_page2(pdf, char_data)

    # Page 3 — Spellcasting (only if caster)
    if char_data.get("is_caster"):
        pdf.add_page()
        draw_page3(pdf, char_data)

    if output_path:
        pdf.output(output_path)
        return output_path
    else:
        return pdf.output()
