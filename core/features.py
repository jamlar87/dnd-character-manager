"""Rule helpers that combine data tables with the levelling service.

Thin re-exports today; the point is that routes import D&D rules from a module
that does not drag in the FastAPI app.
"""
from __future__ import annotations

from services.combat import (
    get_racial_trait_effects, _build_racial_traits, _subrace_traits,
    _build_inventory_attacks, _build_character_attacks,
    _build_charged_item_attacks, get_character_armor_profs, _resolve_armor_item,
    check_armor_proficiency_from_set, _parse_enhancement,
)
from services.items import (
    _resolve_item_key, _build_item_description, _build_item_type,
    _resolve_source, _extract_srd_dice, _item_rarity_for_level,
)
from services.leveling import enrich_features, get_caster_type, get_spell_slots
