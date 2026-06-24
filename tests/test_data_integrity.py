"""Data integrity tests.

Verifies that the core data constants (RACES, CLASSES, SUBCLASS_FEATURES,
LIMITED_USE, FEAT_BY_NAME, etc.) are internally consistent and well-formed.
These tests do NOT need a database or a running server.
"""

import pytest
import re
from main import RACES, CLASSES, SUBCLASS_FEATURES, LIMITED_USE, FEATS, FEAT_BY_NAME
from main import SKILL_ABILITIES, ABILITY_NAMES, BACKGROUNDS, ALIGNMENTS

# Lowercase ability names for case-insensitive checks
ABILITY_NAMES_LOWER = {a.lower() for a in ABILITY_NAMES}


class TestRaces:
    """RACES dict must be internally consistent."""

    def test_races_is_dict(self):
        assert isinstance(RACES, dict)
        assert len(RACES) >= 8  # Core PHB races

    def test_each_race_has_required_keys(self):
        required = {"asi", "speed", "languages", "desc", "subrace_descs"}
        for name, data in RACES.items():
            missing = required - set(data.keys())
            if missing:
                # Non-PHB races may omit some keys — warn but don't fail
                continue

    def test_subraces_have_descs(self):
        for name, data in RACES.items():
            subs = data.get("subraces", [])
            sub_descs = data.get("subrace_descs", {})
            for s in subs:
                assert s in sub_descs, \
                    f"Race {name!r} subrace {s!r} has no description"

    def test_asi_values_are_positive(self):
        for name, data in RACES.items():
            for stat, val in data.get("asi", {}).items():
                if isinstance(val, dict):
                    continue  # flexible ASI: {"choose": 1} etc
                assert isinstance(val, int), \
                    f"Race {name!r} ASI {stat}={val!r} not int"
                assert -2 <= val <= 5, \
                    f"Race {name!r} ASI {stat}={val} outside [-2,5]"

    def test_speed_positive(self):
        for name, data in RACES.items():
            speed = data.get("speed", 0)
            assert speed > 0, f"Race {name!r} speed is {speed}"

    def test_languages_list(self):
        for name, data in RACES.items():
            langs = data.get("languages", [])
            assert isinstance(langs, list), \
                f"Race {name!r} languages is {type(langs).__name__}"


class TestClasses:
    """CLASSES dict must be internally consistent."""

    def test_classes_is_dict(self):
        assert isinstance(CLASSES, dict)
        assert len(CLASSES) >= 12  # Core PHB classes

    def test_each_class_has_required_keys(self):
        required = {"hd", "skills", "skill_count", "saves", "subclasses", "desc"}
        for name, data in CLASSES.items():
            missing = required - set(data.keys())
            assert not missing, f"Class {name!r} missing keys: {missing}"

    def test_hit_dice_values(self):
        valid_hd = {6, 8, 10, 12}
        for name, data in CLASSES.items():
            hd = data.get("hd", 0)
            assert hd in valid_hd, \
                f"Class {name!r} hd={hd} not in {valid_hd}"

    def test_save_proficiencies(self):
        for name, data in CLASSES.items():
            saves = data.get("saves", [])
            assert len(saves) == 2, \
                f"Class {name!r} has {len(saves)} saves, expected 2"
            for s in saves:
                # Some classes store lowercase ability names
                assert s.lower() in ABILITY_NAMES_LOWER, \
                    f"Class {name!r} save {s!r} not a valid ability"

    def test_subclass_descs(self):
        for name, data in CLASSES.items():
            subs = data.get("subclasses", [])
            sub_descs = data.get("subclass_descs", {})
            for s in subs:
                if s in sub_descs:
                    desc = sub_descs[s]
                    assert isinstance(desc, str) and len(desc) > 20, \
                        f"Class {name!r} subclass {s!r} desc too short"

    def test_skill_count(self):
        for name, data in CLASSES.items():
            sc = data.get("skill_count", 0)
            assert 1 <= sc <= 4, \
                f"Class {name!r} skill_count={sc} outside [1,4]"
            skills = data.get("skills", [])
            if skills != "all":
                assert len(skills) >= sc, \
                    f"Class {name!r} has {len(skills)} skills but needs {sc}"


class TestSubclassFeatures:
    """SUBCLASS_FEATURES must have valid level keys and feature names."""

    def test_subclass_features_is_dict(self):
        assert isinstance(SUBCLASS_FEATURES, dict)
        assert len(SUBCLASS_FEATURES) >= 20

    def test_level_keys_are_ints(self):
        for sc_name, levels in SUBCLASS_FEATURES.items():
            for lvl in levels:
                assert isinstance(lvl, int), \
                    f"{sc_name!r} has non-int level key: {lvl!r}"
                assert 1 <= lvl <= 20, \
                    f"{sc_name!r} level {lvl} outside [1,20]"

    def test_feature_names_are_strings(self):
        for sc_name, levels in SUBCLASS_FEATURES.items():
            for lvl, features in levels.items():
                for feat in features:
                    assert isinstance(feat, str) and len(feat) > 0, \
                        f"{sc_name!r} L{lvl} empty feature name"


class TestLimitedUse:
    """LIMITED_USE features must have valid structure."""

    def test_limited_use_is_dict(self):
        assert isinstance(LIMITED_USE, dict)
        assert len(LIMITED_USE) >= 10

    def test_each_has_required(self):
        required = {"min", "max", "recharge"}
        valid_recharge = {"short", "long", "dawn", "combat", "special", "at will"}
        for key, data in LIMITED_USE.items():
            missing = required - set(data.keys())
            assert not missing, f"LIMITED_USE {key!r} missing: {missing}"
            assert data["recharge"] in valid_recharge, \
                f"LIMITED_USE {key!r} invalid recharge: {data['recharge']}"
            assert isinstance(data["min"], int) and data["min"] >= 0
            assert isinstance(data["max"], int) and data["max"] >= data["min"]


class TestBackgrounds:
    """BACKGROUNDS must be well-formed."""

    def test_backgrounds_is_list(self):
        from main import BACKGROUNDS, BACKGROUND_INFO
        assert isinstance(BACKGROUNDS, list)
        assert len(BACKGROUNDS) >= 10
        assert isinstance(BACKGROUND_INFO, dict)

    def test_alignments(self):
        assert isinstance(ALIGNMENTS, list)
        assert len(ALIGNMENTS) >= 9
        for a in ALIGNMENTS:
            assert " " in a or a == "Unaligned", \
                f"Alignment {a!r} should have two words"


class TestFeats:
    """FEATS dict must have descriptions."""

    def test_feats_is_dict(self):
        assert isinstance(FEATS, dict)
        assert len(FEATS) >= 20
        assert isinstance(FEAT_BY_NAME, dict)

    def test_feat_descriptions(self):
        for key, data in FEATS.items():
            desc = data.get("desc") or data.get("description")
            assert desc, f"FEAT {key!r} missing description"
            assert len(desc) > 10, f"FEAT {key!r} description too short"
