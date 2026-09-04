"""Regression tests for character-sheet helpers (item effects, natural attacks,
bonus-action detection)."""

import pytest
from routes.characters.sheet import (
    _grants_bonus_action, compute_item_effects,
)
from services.combat import (
    _build_racial_traits, _build_natural_weapon_attacks,
    _build_character_attacks, _build_inventory_attacks,
)


class TestGrantsBonusAction:
    """Only descriptions that actually grant a bonus action should flag."""

    def test_true_grant_phrase(self):
        assert _grants_bonus_action(
            "As a bonus action, you can command the other end to move."
        ) is True

    def test_using_bonus_action(self):
        assert _grants_bonus_action(
            "You can use a bonus action to activate the wand."
        ) is True

    def test_false_mention_loading_firearm(self):
        # Musket Loading text merely mentions the term — must NOT flag
        assert _grants_bonus_action(
            "Loading: you can fire only one piece of ammunition per action, "
            "bonus action, or reaction, regardless of your number of attacks."
        ) is False

    def test_empty(self):
        assert _grants_bonus_action("") is False
        assert _grants_bonus_action(None) is False


class TestComputeItemEffectsDarkvision:
    def test_goggles_of_night(self):
        result = compute_item_effects(["Goggles of Night"], [])
        assert result["darkvision"] == 60

    def test_no_darkvision_default(self):
        result = compute_item_effects(["Ring of Protection"], [])
        assert result["darkvision"] == 0


class TestLaterLevelActionTyping:
    """High-level Rogue features need correct action types so they render as
    tracked Special/limited-use buttons instead of passive chips (Death Strike
    handled as a template callout card gated on Rogue level >= 17)."""

    def test_stroke_of_luck_special_short_rest(self):
        from services.leveling import enrich_features
        mods = {k: 0 for k in ("strength", "dexterity", "constitution",
                                "intelligence", "wisdom", "charisma")}
        res = enrich_features(["L20: Stroke of Luck"], class_name="Rogue", level=20,
                              mods=mods, class_levels={"Rogue": 20})
        assert len(res) == 1
        assert res[0]["action_type"] == "Special"
        assert res[0]["uses_max"] == 1
        assert res[0]["uses"] == 1
        assert res[0]["recharge"] == "short"
