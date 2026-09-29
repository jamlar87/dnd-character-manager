"""The choice-system level tables have exactly ONE definition, and it lives in data.py.

`services/leveling.py` used to redefine six of them *below* its own import of the same
names — a rebind, not a syntax error — and the copies disagreed about SHAPE: data.py kept
plain lists (`METAMAGIC_LEVELS = [3, 10, 17]`, `PACT_BOON_LEVELS = [3]`) while the local
copies were class-keyed (`{"Sorcerer": [3, 10, 17]}`). So the same constant read as a list
or a dict depending on which module you imported it from, and the wizard's generated consts
shipped a mixture of the two. That is the EXPERTISE_LEVELS bug (SKILL.md Pitfall 18) with a
different constant: pyflakes reports shadowed names as nothing at all, and every consumer
that used the wrong shape would have raised or silently seen "no levels".

Nothing was visibly broken only because the two copies happened to agree in value, which is
exactly why this needs a test rather than a fix-and-forget: importing from data, main or
services.leveling must hand back the SAME object.
"""

from __future__ import annotations

import data
import main
import pytest
from services import leveling as leveling_service

TABLES = (
    "METAMAGIC_LEVELS",
    "METAMAGIC_PICKS",
    "INVOCATION_LEVELS",
    "INVOCATION_PICKS",
    "PACT_BOON_LEVELS",
    "MANEUVER_LEVELS",
)


@pytest.mark.parametrize("name", TABLES)
def test_the_table_has_one_definition_across_modules(name):
    from_data = getattr(data, name)
    assert getattr(main, name) is from_data, f"main re-exports a different {name}"
    assert getattr(leveling_service, name) is from_data, (
        f"services.leveling shadows {name} with its own copy — delete the local literal "
        f"instead of syncing it (SKILL.md Pitfall 18)")


@pytest.mark.parametrize("name", TABLES)
def test_the_table_is_class_keyed(name):
    """Every consumer reads these with .get(class_name) / .get(subclass)."""
    table = getattr(data, name)
    assert isinstance(table, dict), f"{name} must be a mapping, got {type(table).__name__}"
    assert hasattr(table, "get")


def test_the_phb_levels_are_intact():
    """Values per PHB: Sorcerer p.101, Warlock p.107, Fighter (Battle Master) p.73."""
    assert data.METAMAGIC_LEVELS == {"Sorcerer": [3, 10, 17]}
    assert data.METAMAGIC_PICKS == {3: 2, 10: 1, 17: 1}
    assert data.INVOCATION_LEVELS == {"Warlock": [2, 5, 7, 9, 12, 15, 18]}
    assert data.INVOCATION_PICKS == {2: 2, 5: 1, 7: 1, 9: 1, 12: 1, 15: 1, 18: 1}
    assert data.PACT_BOON_LEVELS == {"Warlock": 3}
    assert data.MANEUVER_LEVELS == {"Battle Master": [3, 7, 10, 15]}


def test_every_key_names_a_real_class_or_subclass():
    """Rot guard: a renamed class would leave an entry the app can never match."""
    for name in ("METAMAGIC_LEVELS", "INVOCATION_LEVELS", "PACT_BOON_LEVELS"):
        for class_name in getattr(data, name):
            assert class_name in data.CLASSES, f"{name} names unknown class {class_name!r}"
    for subclass in data.MANEUVER_LEVELS:
        assert subclass in main.SUBCLASS_FEATURES, (
            f"MANEUVER_LEVELS names unknown subclass {subclass!r}")


def test_the_pick_tables_cover_their_level_tables():
    """A level that grants a choice but no pick count silently grants nothing."""
    for levels_table, picks_table in (
        ("METAMAGIC_LEVELS", "METAMAGIC_PICKS"),
        ("INVOCATION_LEVELS", "INVOCATION_PICKS"),
    ):
        for levels in getattr(data, levels_table).values():
            picks = getattr(data, picks_table)
            assert all(lvl in picks for lvl in levels), (
                f"{levels_table} lists {levels} but {picks_table} has no pick count for "
                f"every level")


def test_the_create_wizard_ships_the_same_objects():
    """The wizard's consts are generated from these imports — same shape, not a copy."""
    from routes.characters import creation

    assert creation.METAMAGIC_LEVELS is data.METAMAGIC_LEVELS
    assert creation.PACT_BOON_LEVELS is data.PACT_BOON_LEVELS
    assert creation.MANEUVER_LEVELS is data.MANEUVER_LEVELS
