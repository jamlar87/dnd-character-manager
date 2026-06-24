"""Core function tests.

Tests the utility/business-logic functions that don't need a running server.
"""

import pytest
from main import get_racial_trait_effects
from routes.characters import (
    parse_class_levels, total_level, primary_class,
    get_spellcasting_mod, get_caster_type, get_prepared_max,
    get_spells_known_max, get_cantrips_known_max,
    random_name, random_equipment,
)


class TestParseClassLevels:
    """parse_class_levels handles various DB formats."""

    def test_single_class_dict(self):
        """Modern format: class_levels is a JSON dict string."""
        char = {"class_levels": '{"Fighter": 5}', "class_name": "Fighter", "level": 5}
        result = parse_class_levels(char)
        assert result == {"Fighter": 5}

    def test_multiclass_dict(self):
        char = {"class_levels": '{"Paladin": 6, "Bard": 2}'}
        result = parse_class_levels(char)
        assert result == {"Paladin": 6, "Bard": 2}
        assert total_level(result) == 8

    def test_empty_class_levels_fallback(self):
        """Empty/blank class_levels falls back to class_name + level."""
        char = {"class_levels": "", "class_name": "Wizard", "level": 3}
        result = parse_class_levels(char)
        assert result == {"Wizard": 3}

    def test_none_class_levels_fallback(self):
        char = {"class_levels": None, "class_name": "Cleric", "level": 1}
        result = parse_class_levels(char)
        assert result == {"Cleric": 1}

    def test_missing_class_name_defaults(self):
        char = {"class_levels": "{}", "class_name": "", "level": 0}
        result = parse_class_levels(char)
        assert result == {"Fighter": 1}

    def test_invalid_json_fallback(self):
        char = {"class_levels": "not valid json", "class_name": "Rogue", "level": 7}
        result = parse_class_levels(char)
        assert result == {"Rogue": 7}

    def test_empty_object_class_levels_fallback(self):
        char = {"class_levels": "{}", "class_name": "Barbarian", "level": 9}
        result = parse_class_levels(char)
        assert result == {"Barbarian": 9}


class TestTotalLevel:
    def test_single(self):
        assert total_level({"Fighter": 3}) == 3

    def test_multiclass(self):
        assert total_level({"Fighter": 3, "Rogue": 2}) == 5

    def test_empty(self):
        assert total_level({}) == 0


class TestPrimaryClass:
    def test_first_class_wins(self):
        """primary_class returns first key (insertion order = first class taken)."""
        cls, lvl = primary_class({"Bard": 2, "Paladin": 6})
        assert cls == "Bard"  # First key, not highest level
        assert lvl == 2

    def test_tie_goes_to_first(self):
        cls, lvl = primary_class({"Fighter": 3, "Rogue": 3})
        assert cls == "Fighter"
        assert lvl == 3


class TestSpellcastingMod:
    def test_bard_charisma(self):
        assert get_spellcasting_mod("Bard", {"charisma": 4}) == 4

    def test_paladin_charisma(self):
        assert get_spellcasting_mod("Paladin", {"charisma": 3, "wisdom": 2}) == 3

    def test_wizard_intelligence(self):
        assert get_spellcasting_mod("Wizard", {"intelligence": 5}) == 5

    def test_cleric_wisdom(self):
        assert get_spellcasting_mod("Cleric", {"wisdom": 3}) == 3

    def test_unknown_class_zero(self):
        assert get_spellcasting_mod("Homebrew", {"charisma": 4}) == 0


class TestCasterType:
    def test_bard_full(self):
        assert get_caster_type("Bard") == "full"

    def test_paladin_half(self):
        assert get_caster_type("Paladin") == "half"

    def test_warlock_pact(self):
        assert get_caster_type("Warlock") == "pact"

    def test_fighter_none(self):
        assert get_caster_type("Fighter") == "none"

    def test_unknown_none(self):
        assert get_caster_type("Homebrew") == "none"


class TestPreparedMax:
    def test_prepared_casters(self):
        """Prepared casters max = max(1, level + mod)."""
        from main import PREPARED_CASTERS
        for cls in ("Cleric", "Druid", "Paladin", "Wizard"):
            result = get_prepared_max(cls, 5, 3)
            assert result == 8, f"{cls}: expected 8, got {result}"

    def test_non_prepared_zero(self):
        result = get_prepared_max("Bard", 5, 3)
        assert result == 0


class TestSpellsKnownMax:
    def test_bard_level_8(self):
        result = get_spells_known_max("Bard", 8)
        assert result > 0

    def test_noncaster_zero(self):
        result = get_spells_known_max("Fighter", 5)
        assert result == 0


class TestRacialTraitEffects:
    def test_human_standard(self):
        result = get_racial_trait_effects("Human")
        assert isinstance(result, dict)

    def test_dwarf_weapon_profs(self):
        result = get_racial_trait_effects("Dwarf")
        assert "Battleaxe" in result.get("weapon_profs", [])

    def test_dark_elf_subrace_weapons(self):
        result = get_racial_trait_effects("Elf", "Dark Elf (Drow)")
        assert "Rapier" in result.get("weapon_profs", [])


class TestRandomName:
    def test_returns_dict(self):
        result = random_name("Human")
        assert isinstance(result, dict)
        assert "name" in result
        assert result["name"]

    def test_known_race(self):
        result = random_name("Elf")
        assert isinstance(result["name"], str)
        assert len(result["name"]) > 0
