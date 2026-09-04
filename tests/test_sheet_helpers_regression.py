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

    def test_runtime_limited_use_keys_resolve_via_clean_strip(self):
        """data_loader injects many more LIMITED_USE keys (race/subclass/NPC
        traits). Every one must resolve to a FEATURE_ACTION_TYPES entry —
        either exact, or after stripping the trailing parenthetical suffix
        (the render lookup does the same _clean_key strip)."""
        import re
        from services.data_loader import load_manual_data
        load_manual_data()
        from data import LIMITED_USE, FEATURE_ACTION_TYPES
        _clean = lambda k: re.sub(r'\s*\([^)]*\)\s*$', '', k).strip()
        missing = sorted(
            k for k in LIMITED_USE
            if k not in FEATURE_ACTION_TYPES and _clean(k) not in FEATURE_ACTION_TYPES
        )
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


class TestRiderCalloutCards:
    """Registry-driven always-on rider cards (the Sneak Attack family) render
    for characters that have the feature and never for those without."""

    def test_registry_keys_are_lowercase_base_names(self):
        from data import RIDER_CARDS
        for key, entry in RIDER_CARDS.items():
            assert key == key.strip().lower(), key
            assert entry.get("name") and entry.get("body"), key
            # placeholder tokens used must be resolvable ones
            import re as _re
            toks = set(_re.findall(r"\{(\w+)\}", entry.get("body", "") + entry.get("tag", "")))
            known = {"init", "ds_dice", "bc_tag", "pb_die", "dice"}
            assert toks <= known, (key, toks)

    def test_builder_emits_card_only_for_features_present(self):
        from routes.characters.sheet import _build_rider_cards
        fd = [
            {"name": "Divine Fury", "level": "L3", "base_name": "Divine Fury"},
            {"name": "Rage", "level": "L1", "base_name": "Rage"},
        ]
        char = {"wisdom": 14}
        cards = _build_rider_cards(fd, char, {"Barbarian": 14})
        names = [c["name"] for c in cards]
        assert "Divine Fury" in names
        assert "Rage" not in names            # not in registry
        assert "Brutal Critical" not in names  # not in feature_data

    def test_builder_level_scaling_tokens(self):
        from routes.characters.sheet import _build_rider_cards
        fd = [
            {"name": "Divine Strike", "level": "L8", "base_name": "Divine Strike"},
            {"name": "Brutal Critical", "level": "L9", "base_name": "Brutal Critical"},
        ]
        char = {}
        cards = _build_rider_cards(fd, char, {"Cleric": 8, "Barbarian": 13})
        by = {c["name"]: c for c in cards}
        assert "1d8" in by["Divine Strike"]["tag"]     # cleric 8 -> 1d8
        assert "2 dice" in by["Brutal Critical"]["tag"]  # barb 13 -> 2 dice
        assert "{" not in by["Divine Strike"]["body"]
        cards14 = _build_rider_cards(fd, char, {"Cleric": 14})
        assert "2d8" in cards14[0]["tag"]

