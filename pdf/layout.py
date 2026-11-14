"""Page geometry, fonts and low-level drawing primitives."""

import os

from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Register Cinzel Bold for D&D title font
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FONTS_DIR = _REPO_ROOT  # fonts/ lives at the repo root, not inside pdf/
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
MARGIN = 18


def yb(y_tl):
    """Convert top-left y to reportlab bottom-left y."""
    return PAGE_H - y_tl


# ═══════════════════════════════════════════════════════════════
#  SPELL CACHE (lightweight — only used for slot lookups)

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

COL1_X, COL1_W = 45, 140
COL2_X, COL2_W = 245, 180
COL3_X, COL3_W = 445, 150
GRID_Y = 165

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
