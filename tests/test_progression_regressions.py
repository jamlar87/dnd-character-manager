"""Character progression and spell-slot regression tests."""

from routes.characters.all import get_spell_slots, get_character_spell_slots


def test_fighter_has_no_spell_slots():
    result = get_spell_slots("Fighter", 5)
    assert result["slots"] == 0
    assert all(value == 0 for value in result["by_level"].values())


def test_wizard_level_one_has_two_first_level_slots():
    result = get_spell_slots("Wizard", 1)
    assert result["by_level"][1] == 2
    assert result["by_level"][2] == 0


def test_warlock_level_one_has_pact_slot():
    result = get_spell_slots("Warlock", 1)
    assert result["slots"] == 1
    assert result["slot_level"] == 1
    assert "Pact Magic" in result["note"]


def test_multiclass_warlock_tracks_pact_slots_separately():
    result = get_character_spell_slots({
        "class_levels": '{"Wizard": 5, "Warlock": 2}',
        "class_name": "Wizard",
        "level": 7,
    })
    assert result["multiclass"] is True
    assert result["pact_slots"]["slots"] == 2
    assert result["pact_slots"]["slot_level"] == 1
    assert result["by_level"][3] >= 2


def test_multiclass_total_level_is_preserved():
    result = get_character_spell_slots({
        "class_levels": '{"Cleric": 3, "Fighter": 2}',
        "class_name": "Cleric",
        "level": 5,
    })
    assert result["total_level"] == 5
    assert result["by_level"][2] >= 2


def test_invalid_level_does_not_create_slots():
    result = get_spell_slots("Wizard", 99)
    assert result["slots"] == 0
    assert result["by_level"] == {}
