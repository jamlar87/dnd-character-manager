"""Character management routes — package.

The original monolithic file lives at routes/characters/all.py for reference.
All functions are re-exported here for backward-compatible imports.
"""
from routes.characters.all import (
    # Router
    router,
    # Shared globals
    MANUAL_MONSTERS, MANUAL_TRAPS,
    # Helpers used by routes/dm.py
    _load_monster_cache,
    _call_ollama,
    _extract_json,
    _xp_for_cr,
    _assign_encounter_counts,
    _search_manuals,
    _build_character,
    # Helper functions used by tests/test_core_functions.py
    parse_class_levels, total_level, primary_class,
    get_spellcasting_mod, get_caster_type, get_prepared_max,
    get_spells_known_max, get_cantrips_known_max,
    random_name, random_equipment,
    # Internal helpers used elsewhere
    _normalize_recharge,
)
