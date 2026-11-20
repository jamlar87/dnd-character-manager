"""DM Tools routes — monsters, NPCs, encounters, campaigns, traps, widgets."""

# Imported here as well as in the sub-modules: these are the package-level names
# ``from routes.dm import ...`` callers relied on before the split, and the
# attributes tests monkeypatch. FastAPI registration lives in the sub-routers below.
import json
import random
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

import core.data as _core_data  # MANUALS_BASE is read live: tests repoint it
from core.config import STATIC
from core.data import (
    ALIGNMENTS,
    CLASSES,
    FLEXIBLE_ASI_RACES,
    ITEM_INDEX,
    MANUAL_TRAPS,
    RACE_NAMES,
    RACES,
    SRD_MAGIC_ITEMS,
    SRD_SPELLS,
    _get_named_item_types,
    _get_source_slug_map,
    _load_manual_json,
    _scan_manual_pdfs,
)
from core.db import _json_list, get_db
from core.features import (
    enrich_features,
    get_caster_type,
    get_racial_trait_effects,
    get_spell_slots,
)
from core.http import _render, static_asset_version
from core.security import _is_admin, _require_owned, _user_where, require_user
from data import SUBCLASS_LEVELS
from routes.characters import (
    _assign_encounter_counts,
    _build_character,
    _call_ai,
    _extract_json,
    _load_monster_cache,
    _monster_cr_sort_key,
    _search_manuals,
    _xp_for_cr,
    parse_source_filter,
    source_matches,
)
from routes.characters.ai_routes import _calculate_ac, _pick_skills
from routes.characters.sheet import (
    allocate_ability_scores,
    calc_hp,
    get_equipment_for_level,
    get_spells_for_level,
)
from services.leveling import PROFICIENCY_BONUS, get_class_features
from services.reference_assets import ensure_source_slug_map_asset
from services.rules import ability_mod
from services.schema import HAS_PORTRAIT_SQL, hydrate_json_columns
from services.sources import clean_source_display
from services.text import alpha_key
from services.validation import clamp_fields, clamp_int
from summon_templates import SUMMON_TEMPLATES

router = APIRouter()

from .api import router as _api_router  # noqa: E402
from .campaigns import router as _campaigns_router  # noqa: E402
from .encounters import router as _encounters_router  # noqa: E402
from .monsters import router as _monsters_router  # noqa: E402
from .npcs import router as _npcs_router  # noqa: E402
from .page import router as _page_router  # noqa: E402
from .traps import router as _traps_router  # noqa: E402
from .widgets import router as _widgets_router  # noqa: E402

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
from ._shared import (  # noqa: F401
    _MANUAL_HP_RE,
    NPC_LIST_COLS,
    _manual_npc_hp,
)
from .api import (  # noqa: F401
    ai_summary_pdf,
    dm_character_hp,
    dm_characters_for_combat,
    dm_search_manuals,
    dm_search_manuals_summarize,
    dm_summon_hp,
    dm_user_characters,
)
from .campaigns import (  # noqa: F401
    _campaign_owned,
    _campaign_roster_ids,
    award_xp,
    campaign_xp,
    dm_campaign_add_character,
    dm_campaign_add_npc,
    dm_campaign_create,
    dm_campaign_delete,
    dm_campaign_remove_character,
    dm_campaign_remove_npc,
    dm_campaign_update,
    dm_campaign_update_npc_notes,
    dm_campaigns_list,
)
from .encounters import (  # noqa: F401
    _HABITAT_CONFLICTS,
    _HABITAT_MARKERS,
    _HABITAT_SUBTYPE,
    _HABITAT_TYPE,
    PARTY_PROFILE_COLS,
    PARTY_PROFILE_COLS_C,
    _build_party_profile,
    _monster_habitat_evidence,
    _monster_habitats,
    dm_ai_build_encounter,
    dm_ai_party_profile,
    dm_encounter_add_creature,
    dm_encounter_add_npc,
    dm_encounter_combat_state,
    dm_encounter_copy,
    dm_encounter_create,
    dm_encounter_delete,
    dm_encounter_detail,
    dm_encounter_remove_npc,
    dm_encounter_roll_initiative,
    dm_encounter_share,
    dm_encounter_update,
    dm_encounter_update_init,
    dm_encounters_list,
)
from .monsters import (  # noqa: F401
    _enrich_monster,
    _enrich_spell,
    dm_monster_detail,
    dm_monster_list,
    dm_monster_search,
    dm_monsters_by_cr,
    dm_spell_detail,
)
from .npcs import (  # noqa: F401
    _apply_portrait_to_built_character,
    _detect_creation_gaps,
    _npc_int,
    _npc_to_character_data,
    dm_ai_build_npc,
    dm_npc_build_from_gaps,
    dm_npc_build_to_character,
    dm_npc_create,
    dm_npc_delete,
    dm_npc_detail,
    dm_npc_portrait_image,
    dm_npc_update,
    dm_npcs_list,
)
from .page import (  # noqa: F401
    _DM_LIBRARY_VERSION,
    _campaign_characters_live,
    _json_for_script,
    _manual_group_payload,
    _manual_npc_payload,
    _monster_card_payload,
    _spell_card_payload,
    _trap_card_payload,
    campaign_detail,
    dm_tools,
    ensure_dm_library_asset,
)
from .traps import (  # noqa: F401
    dm_ai_build_trap,
)
from .widgets import (  # noqa: F401
    WIDGET_MAX,
    WIDGET_TYPES,
    _clean_widgets,
    _save_widgets,
    _widgets_of,
    dm_campaign_widgets,
    dm_encounter_widgets,
    dm_save_campaign_widgets,
    dm_save_encounter_widgets,
)
