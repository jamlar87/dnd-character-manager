"""Character management routes — package.

`all.py` assembles the sibling route modules into one composite router and holds
the shared PHB registries; this package re-exports the router plus the helpers
other modules (routes/dm.py) and the tests import by name.
"""
from routes.characters.all import (
    _assign_encounter_counts,
    _call_ai,
    _call_ollama,
    _extract_json,
    # Shared globals
    # Helpers used by routes/dm.py
    _load_monster_cache,
    _monster_cr_sort_key,
    _search_manuals,
    _xp_for_cr,
    get_cantrips_known_max,
    # Internal helpers used elsewhere
    get_caster_type,
    get_prepared_max,
    get_spells_known_max,
    # Helper functions used by tests/test_core_functions.py
    parse_class_levels,
    parse_source_filter,
    primary_class,
    # Router
    router,
    source_matches,
    total_level,
)
from routes.characters.creation import (
    _build_character,
    random_equipment,
    random_name,
)
from routes.characters.sheet import get_spellcasting_mod
