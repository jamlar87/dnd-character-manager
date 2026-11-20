"""Backward-compatible facade over the :mod:`pdf` package.

The implementation used to live in this single module; it is now split by
concern under ``pdf/``.  Every name that was importable from here still is,
so call sites need no changes.
"""
import json  # noqa: F401  (was importable as pdf_generator.json)
import os  # noqa: F401
import sys  # noqa: F401

from reportlab.lib.pagesizes import letter  # noqa: F401
from reportlab.lib.utils import simpleSplit  # noqa: F401
from reportlab.pdfbase import pdfmetrics  # noqa: F401
from reportlab.pdfbase.ttfonts import TTFont  # noqa: F401
from reportlab.pdfgen import canvas  # noqa: F401

from pdf import *  # noqa: F401,F403
from pdf import (  # private helpers, kept for tests and internal callers
    _FONTS_DIR,
    _ITEM_INDEX,
    _SAVE_ABBR_CHECKBOXES,
    _SAVE_CHECKBOXES,
    _SKILL_CHECKBOXES,
    _SLOT_TOTAL_FIELDS,
    _SPELL_CACHE,
    _SPELL_FIELDS_BY_LEVEL,
    _TEMPLATE_PATH,
    _bubble,
    _build_condensed_features,
    _build_equipment_appendix_text,
    _build_full_feature_text,
    _build_page1_features_text,
    _build_short_features_text,
    _build_spell_appendix_text,
    _build_spell_short_desc,
    _build_summons_appendix_text,
    _checkbox,
    _draw_appendix_page,
    _draw_col1_stats,
    _draw_col2_combat,
    _draw_col3_personality,
    _extract_mechanical_summary,
    _get_item_description,
    _get_spell_cache,
    _label,
    _mod_str,
    _render_spell_card,
    _resolve_template,
    _skill_field_name,
    _text_box,
    _value,
)  # noqa: F401
from services.rules import ability_mod  # noqa: F401
