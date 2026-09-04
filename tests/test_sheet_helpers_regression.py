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


class TestFeatureVariantCollapse:
    """Leveling stores suffixed variants of the same feature (e.g. "Brutal
    Critical (1 die)" @L9 then "(2 dice)" @L13). The sheet must keep only the
    highest-level variant and expose a stable base_name so template dice
    badges (which match base names like 'Brutal Critical') render."""

    def test_brutal_critical_variants_collapse_to_highest(self):
        from routes.characters.sheet import collapse_variant_features
        fd = [
            {"name": "Brutal Critical (1 die)", "level": "L9"},
            {"name": "Brutal Critical (2 dice)", "level": "L13"},
        ]
        out = collapse_variant_features(fd)
        assert len(out) == 1
        assert out[0]["name"] == "Brutal Critical (2 dice)"
        assert out[0]["base_name"] == "Brutal Critical"

    def test_repeatable_features_are_not_collapsed(self):
        from routes.characters.sheet import collapse_variant_features
        # Expertise and ASIs legitimately repeat without suffix variants
        fd = [
            {"name": "Expertise", "level": "L1"},
            {"name": "Expertise", "level": "L6"},
            {"name": "Ability Score Improvement", "level": "L4"},
            {"name": "Ability Score Improvement", "level": "L8"},
        ]
        out = collapse_variant_features(fd)
        assert len(out) == 4
        assert out[0]["base_name"] == "Expertise"

    def test_plain_plus_variant_entry_is_untouched(self):
        from routes.characters.sheet import collapse_variant_features
        # A plain base entry alongside a variant means distinct features
        fd = [
            {"name": "Extra Attack", "level": "L5"},
            {"name": "Extra Attack (3)", "level": "L11"},
        ]
        out = collapse_variant_features(fd)
        assert len(out) == 2


class TestFeatureActionTypeCoverage:
    """Every static LIMITED_USE feature (data.py) must have a
    FEATURE_ACTION_TYPES entry so its tracked button shows the correct type
    badge (Action/Bonus Action/Reaction/Special) plus a tooltip — features
    without one default to a generic "Action" badge with no desc. (Runtime
    trait injections from data_loader for racial/NPC/homebrew features are out
    of scope; they fall back to the generic default like any unmatched key.)"""

    @staticmethod
    def _parse_source_literal(name: str) -> set[str]:
        import re
        src = open("data.py", encoding="utf-8").read()
        block = re.search(rf"{name} = \{{(.*?)\n\}}", src, re.S)
        assert block, f"{name} block not found in data.py"
        return set(re.findall(r'^\s*"([^"]+)":', block.group(1), re.M))

    def test_all_limited_use_keys_have_action_types(self):
        lu = self._parse_source_literal("LIMITED_USE")
        fat = self._parse_source_literal("FEATURE_ACTION_TYPES")
        missing = sorted(lu - fat)
        assert missing == []

    def test_spot_check_action_types(self):
        from data import FEATURE_ACTION_TYPES
        expected = {
            "bend luck": "Reaction",       # reaction + 2 sorcery points
            "arcane recovery": "Special",  # during a short rest, no action
            "ki": "Special",               # resource pool, not an action
            "divine sense": "Action",      # use an action to detect
            "dragon wings": "Bonus Action",
            "misty escape": "Reaction",
        }
        for key, want in expected.items():
            assert FEATURE_ACTION_TYPES[key][0] == want, key
