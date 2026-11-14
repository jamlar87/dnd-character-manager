"""DM Tools routes — monsters, NPCs, encounters, campaigns, traps, widgets."""

# Imported here as well as in the sub-modules: these are the package-level names
# ``from routes.dm import ...`` callers relied on before the split, and the
# attributes tests monkeypatch. FastAPI registration lives in the sub-routers below.
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response, RedirectResponse
import json, random, re
from pathlib import Path
from core.config import STATIC
from core.security import require_user, _user_where, _require_owned, _is_admin
from core.http import _render, static_asset_version
from core.db import get_db, _json_list
from routes.characters import _load_monster_cache, _call_ai, _extract_json, _xp_for_cr, _assign_encounter_counts, _search_manuals, _build_character, _monster_cr_sort_key
from routes.characters import parse_source_filter, source_matches
from services.text import alpha_key
from services.reference_assets import ensure_source_slug_map_asset
from core.data import RACES, CLASSES, FLEXIBLE_ASI_RACES, RACE_NAMES
from core.data import _load_manual_json, _get_named_item_types, _get_source_slug_map, _scan_manual_pdfs
import core.data as _core_data  # MANUALS_BASE is read live: tests repoint it
from core.features import enrich_features, get_caster_type, get_spell_slots, get_racial_trait_effects
from core.data import MANUAL_TRAPS
from core.data import SRD_SPELLS
from core.data import SRD_MAGIC_ITEMS, ITEM_INDEX
from data import SUBCLASS_LEVELS
from summon_templates import SUMMON_TEMPLATES
from core.data import ALIGNMENTS
from routes.characters.sheet import allocate_ability_scores, calc_hp, get_equipment_for_level, get_spells_for_level
from routes.characters.ai_routes import _calculate_ac, _pick_skills
from services.leveling import PROFICIENCY_BONUS, get_class_features
from services.rules import ability_mod
from services.schema import HAS_PORTRAIT_SQL, hydrate_json_columns
from services.sources import clean_source_display
from services.validation import clamp_int, clamp_fields

router = APIRouter()

from .page import router as _page_router  # noqa: E402
from .monsters import router as _monsters_router  # noqa: E402
from .npcs import router as _npcs_router  # noqa: E402
from .encounters import router as _encounters_router  # noqa: E402
from .campaigns import router as _campaigns_router  # noqa: E402
from .traps import router as _traps_router  # noqa: E402
from .widgets import router as _widgets_router  # noqa: E402
from .api import router as _api_router  # noqa: E402
router.include_router(_page_router)
router.include_router(_monsters_router)
router.include_router(_npcs_router)
router.include_router(_encounters_router)
router.include_router(_campaigns_router)
router.include_router(_traps_router)
router.include_router(_widgets_router)
router.include_router(_api_router)

# Including the sub-routers cannot reproduce the original registration order on its
# own: several sections interleaved in the old file (the AI builders sat between the
# NPC and encounter routes, campaign widgets before the HP endpoints). Starlette
# matches in registration order, so re-attach the routes in their original order.
_ORIGINAL_ORDER = (
    'dm_tools', 'dm_monster_detail', 'dm_spell_detail', 'dm_monster_list',
    'dm_monster_search', 'dm_monsters_by_cr', 'dm_npc_create', 'dm_npcs_list',
    'dm_npc_detail', 'dm_npc_update', 'dm_npc_delete', 'dm_npc_portrait_image',
    'dm_npc_build_to_character', 'dm_npc_build_from_gaps', 'dm_ai_party_profile',
    'dm_ai_build_encounter', 'dm_ai_build_npc', 'dm_ai_build_trap', 'dm_search_manuals',
    'dm_search_manuals_summarize', 'ai_summary_pdf', 'dm_encounter_create',
    'dm_encounter_copy', 'dm_encounters_list', 'dm_encounter_detail', 'dm_encounter_share',
    'dm_encounter_add_npc', 'dm_encounter_add_creature', 'dm_encounter_remove_npc',
    'dm_encounter_update_init', 'dm_encounter_roll_initiative', 'dm_encounter_combat_state',
    'dm_encounter_update', 'dm_encounter_delete', 'dm_campaign_create', 'dm_campaigns_list',
    'dm_campaign_update', 'dm_campaign_delete', 'dm_campaign_add_character',
    'dm_campaign_remove_character', 'dm_campaign_add_npc', 'dm_campaign_remove_npc',
    'dm_campaign_update_npc_notes', 'dm_user_characters', 'dm_characters_for_combat',
    'campaign_detail', 'dm_encounter_widgets', 'dm_save_encounter_widgets',
    'dm_campaign_widgets', 'dm_save_campaign_widgets', 'dm_character_hp', 'dm_summon_hp',
    'campaign_xp', 'award_xp'
)
_routes_by_name = {r.name: r for r in router.routes}
router.routes[:] = [_routes_by_name[_n] for _n in _ORIGINAL_ORDER]
del _routes_by_name

# Re-exports: the pre-split module had every helper at `routes.dm.<name>`.
from .page import (  # noqa: F401
    _json_for_script, _campaign_characters_live, dm_tools, _DM_LIBRARY_VERSION,
    _monster_card_payload, ensure_dm_library_asset, _manual_npc_payload, _trap_card_payload,
    _manual_group_payload, _spell_card_payload, campaign_detail,
)
from .monsters import (  # noqa: F401
    dm_monster_detail, dm_spell_detail, _enrich_spell, _enrich_monster, dm_monster_list,
    dm_monster_search, dm_monsters_by_cr,
)
from .npcs import (  # noqa: F401
    _npc_int, dm_npc_create, dm_npcs_list, dm_npc_detail, dm_npc_update, dm_npc_delete,
    dm_npc_portrait_image, _detect_creation_gaps, _npc_to_character_data,
    _apply_portrait_to_built_character, dm_npc_build_to_character, dm_npc_build_from_gaps,
    dm_ai_build_npc,
)
from .encounters import (  # noqa: F401
    PARTY_PROFILE_COLS, PARTY_PROFILE_COLS_C, _build_party_profile, dm_ai_party_profile,
    _HABITAT_TYPE, _HABITAT_SUBTYPE, _HABITAT_MARKERS, _HABITAT_CONFLICTS,
    _monster_habitat_evidence, _monster_habitats, dm_ai_build_encounter, dm_encounter_create,
    dm_encounter_copy, dm_encounters_list, dm_encounter_detail, dm_encounter_share,
    dm_encounter_add_npc, dm_encounter_add_creature, dm_encounter_remove_npc,
    dm_encounter_update_init, dm_encounter_roll_initiative, dm_encounter_combat_state,
    dm_encounter_update, dm_encounter_delete,
)
from .campaigns import (  # noqa: F401
    dm_campaign_create, dm_campaigns_list, dm_campaign_update, dm_campaign_delete,
    dm_campaign_add_character, dm_campaign_remove_character, dm_campaign_add_npc,
    dm_campaign_remove_npc, dm_campaign_update_npc_notes, _campaign_owned, _campaign_roster_ids,
    campaign_xp, award_xp,
)
from .traps import (  # noqa: F401
    dm_ai_build_trap,
)
from .widgets import (  # noqa: F401
    WIDGET_TYPES, WIDGET_MAX, _clean_widgets, _widgets_of, _save_widgets, dm_encounter_widgets,
    dm_save_encounter_widgets, dm_campaign_widgets, dm_save_campaign_widgets,
)
from .api import (  # noqa: F401
    dm_search_manuals, dm_search_manuals_summarize, ai_summary_pdf, dm_user_characters,
    dm_characters_for_combat, dm_character_hp, dm_summon_hp,
)
from ._shared import (  # noqa: F401
    NPC_LIST_COLS, _MANUAL_HP_RE, _manual_npc_hp,
)
