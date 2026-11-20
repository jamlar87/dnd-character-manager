"""Rule helpers that combine data tables with the levelling service.

Thin re-exports today; the point is that routes import D&D rules from a module
that does not drag in the FastAPI app.
"""
from __future__ import annotations

from services.combat import (
    _build_character_attacks,
    _build_charged_item_attacks,
    _build_inventory_attacks,
    _build_racial_traits,
    _parse_enhancement,
    _resolve_armor_item,
    _subrace_traits,
    check_armor_proficiency_from_set,
    get_character_armor_profs,
    get_racial_trait_effects,
)
from services.items import (
    _build_item_description,
    _build_item_type,
    _extract_srd_dice,
    _item_rarity_for_level,
    _resolve_item_key,
    _resolve_source,
)
from services.leveling import enrich_features, get_caster_type, get_spell_slots
