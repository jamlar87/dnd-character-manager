"""Character sheet: the page, its save path, equipment/attunement and the
sheet-side JSON API.

One 2.6k-line module, split along its own section seams. Every name the rest of
the app imported from `routes.characters.sheet` is re-exported below, so
`from routes.characters.sheet import router` (routes/characters/all.py) and
`from routes.characters.sheet import allocate_ability_scores, calc_hp,
get_equipment_for_level, get_spells_for_level` (routes/dm.py) are unchanged.

Sub-modules:
    _shared      constants and pure helpers, no routes
    view         GET the sheet page + the sheet-ref.js payload
    update       the save path (undo recording) and the ASI/expertise edits
    api          live-session JSON endpoints
    inventory    equipment, attunement and item effects
    feats        feat tables and feature enrichment
    progression  level-scaling helpers (calc_hp, allocate_ability_scores, ...)
"""

import sys
import types

from fastapi import APIRouter

from . import _shared, api, feats, inventory, progression, update, view
from ._shared import (  # noqa: F401
    _BA_GRANT_RE,
    _INVOCATION_NAMES,
    _grants_bonus_action,
    _merged_races_for_popup,
    compute_advantage_map,
)
from .api import (  # noqa: F401
    _portrait_thumbnail,
    _record_conditions,
    _sync_combat_hp_impl,
    add_condition,
    character_portrait_image,
    character_toggle_share,
    create_summon,
    delete_character,
    get_attacks,
    get_conditions,
    get_summons,
    reload_charge,
    remove_condition,
    set_concentration,
    spend_charge,
    sync_combat_hp,
    update_summon_hp,
)
from .feats import (  # noqa: F401
    FEAT_BY_NAME,
    FEAT_DETAILS,
    FEATURE_DEFENSES,
    KNOWN_FEATS,
    _add_cd_sub_options,
    _add_dice_to_features,
    _add_invocation_levels,
    _add_reaction_type_to_features,
    _add_source_to_features,
    _build_known_feats,
    _build_rider_cards,
    _recalc_limited_uses,
    collapse_variant_features,
)
from .inventory import (  # noqa: F401
    ITEM_ATTUNEMENT,
    ITEM_PROPERTIES,
    _build_item_properties,
    _equipment_added,
    _manual_equipment,
    compute_item_effects,
)
from .progression import (  # noqa: F401
    STANDARD_ARRAY,
    allocate_ability_scores,
    calc_hp,
    get_equipment_for_level,
    get_spellcasting_mod,
    get_spells_for_level,
    pick_magic_items,
)
from .update import (  # noqa: F401
    _publish_change,
    edit_asi_choice,
    edit_expertise_choice,
    update_character,
)

# Imported by the route modules, re-exported because callers and tests reach
# through this package for them (tests patch `sheet.require_user`).
from .view import (  # noqa: F401
    _SHEET_REF_VERSION,
    _sheet_reference_payload,
    character_sheet,
    ensure_sheet_reference_asset,
    require_user,
    sheet_asset_version,
)

router = APIRouter()
router.include_router(view.router)
router.include_router(update.router)
router.include_router(api.router)

_SUBMODULES = (_shared, feats, inventory, progression, view, update, api)


class _SheetNamespace(types.ModuleType):
    """Keep `routes.characters.sheet.<name> = value` meaningful after the split.

    This used to be one module, so tests patched names straight onto it
    (`monkeypatch.setattr(sheet_routes, "require_user", ...)`): the route
    functions read them as module globals. Those names now live in sub-modules,
    so every write is forwarded to each sub-module that holds the name, which
    preserves the old patching contract.
    """

    def __setattr__(self, name, value):
        for mod in _SUBMODULES:
            if name in vars(mod):
                setattr(mod, name, value)
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _SheetNamespace
