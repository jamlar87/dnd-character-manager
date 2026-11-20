"""Spellbook pages (spell matrix, spell/equipment appendices)."""

from reportlab.lib.utils import simpleSplit

from .layout import (
    FONT,
    FONT_BOLD,
    MARGIN,
    PAGE_H,
    PAGE_W,
    _bubble,
    _label,
    _value,
    trunc,
    yb,
)
from .text import _build_spell_short_desc


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
