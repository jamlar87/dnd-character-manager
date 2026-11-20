"""PDF rendering for D&D 5e character sheets, spellbooks and summaries."""

from .data import (  # noqa: F401  (re-export)
    build_char_data,
)
from .layout import (  # noqa: F401  (re-export)
    _FONTS_DIR,
    COL1_W,
    COL1_X,
    COL2_W,
    COL2_X,
    COL3_W,
    COL3_X,
    FONT,
    FONT_BOLD,
    GRID_Y,
    MARGIN,
    PAGE_H,
    PAGE_W,
    TITLE_FONT,
    _bubble,
    _checkbox,
    _draw_appendix_page,
    _label,
    _text_box,
    _value,
    trunc,
    yb,
)
from .official import (  # noqa: F401  (re-export)
    _REPO_ROOT,
    _SAVE_ABBR_CHECKBOXES,
    _SAVE_CHECKBOXES,
    _SKILL_CHECKBOXES,
    _SLOT_TOTAL_FIELDS,
    _SPELL_FIELDS_BY_LEVEL,
    _TEMPLATE_PATH,
    _mod_str,
    _resolve_template,
    _skill_field_name,
    fill_official_sheet,
)
from .shared import (  # noqa: F401  (re-export)
    _ITEM_INDEX,
    _SPELL_CACHE,
    _get_item_description,
    _get_spell_cache,
)
from .sheet import (  # noqa: F401  (re-export)
    _draw_col1_stats,
    _draw_col2_combat,
    _draw_col3_personality,
    draw_header,
    draw_page1,
    draw_page2,
    generate_character_sheet,
)
from .spellbook import (  # noqa: F401  (re-export)
    _render_spell_card,
    draw_equipment_appendix,
    draw_page3,
    draw_page4_appendix,
    draw_spell_appendix,
)
from .summary import (  # noqa: F401  (re-export)
    generate_ai_summary_pdf,
)
from .text import (  # noqa: F401  (re-export)
    _build_condensed_features,
    _build_equipment_appendix_text,
    _build_full_feature_text,
    _build_page1_features_text,
    _build_short_features_text,
    _build_spell_appendix_text,
    _build_spell_short_desc,
    _build_summons_appendix_text,
    _extract_mechanical_summary,
    mod_int,
)

__all__ = [
    "COL1_W",
    "COL1_X",
    "COL2_W",
    "COL2_X",
    "COL3_W",
    "COL3_X",
    "FONT",
    "FONT_BOLD",
    "GRID_Y",
    "MARGIN",
    "PAGE_H",
    "PAGE_W",
    "TITLE_FONT",
    "build_char_data",
    "draw_equipment_appendix",
    "draw_header",
    "draw_page1",
    "draw_page2",
    "draw_page3",
    "draw_page4_appendix",
    "draw_spell_appendix",
    "fill_official_sheet",
    "generate_ai_summary_pdf",
    "generate_character_sheet",
    "mod_int",
    "trunc",
    "yb",
]
