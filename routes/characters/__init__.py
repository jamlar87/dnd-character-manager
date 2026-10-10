"""Character management routes — package.

`all.py` assembles the sibling route modules into one composite router and holds
the shared PHB registries; this package re-exports the router plus the helpers
other modules (routes/dm.py) and the tests import by name.
"""
from routes.characters.all import (
    # Router
    router,
    # Shared globals
    # Helpers used by routes/dm.py
    _load_monster_cache,
    _call_ollama,
    _call_ai,
    _extract_json,
    _xp_for_cr,
    _assign_encounter_counts,
    _search_manuals,
    parse_source_filter, source_matches,
    _monster_cr_sort_key,
    # Helper functions used by tests/test_core_functions.py
    parse_class_levels, total_level, primary_class,
    get_caster_type, get_prepared_max,
    get_spells_known_max, get_cantrips_known_max,
    # Internal helpers used elsewhere
)
from routes.characters.sheet import get_spellcasting_mod
from routes.characters.creation import (
    _build_character, random_name, random_equipment,
)
