"""Subclass table guards.

Two defects this file pins down, both of which reached the wizard:

  * One subclass, two spellings. `data/exports/classes_export.json` listed
    "Circle of Stars" while `subclasses.json` carried "Circle of the Stars", and the
    Warlock patron appeared as both "Genie" (listed) and "The Genie" (extracted). The
    merge only skipped a LITERAL repeat, so the wizard offered both — and the features
    hung off the spelling nobody could pick, leaving the other option empty.

  * A class feature listed as a subclass: AiME's "Horns Wildly Blowing" is the Rider's
    14th-level feature, not an archetype.
"""
import re

from data import CLASSES, SUBCLASS_FEATURES, FEATURE_ACTION_TYPES, LIMITED_USE
from services.data_loader import (
    _existing_subclass_name,
    _subclass_name_key,
    load_manual_data,
)

# Every subclass offered by the wizard must carry features — an option with none is a
# broken promise (the player picks it and the sheet stays empty). "Way of Tranquility"
# (Monk) was the one offender: not in subclasses.json, the phrase in no manual cache,
# its "PHB p.80" source wrong. James approved removing it, so this set is empty and any
# new featureless option fails.
KNOWN_FEATURELESS_OPTIONS = set()


def test_subclass_name_key_ignores_articles_and_punctuation():
    assert _subclass_name_key("Circle of Stars") == _subclass_name_key("Circle of the Stars")
    assert _subclass_name_key("Genie") == _subclass_name_key("The Genie")
    assert _subclass_name_key("The Great Old One") == _subclass_name_key("Great Old One")
    # Substring neighbours are NOT the same subclass — a "one name contains the
    # other" rule would wrongly merge these, which is why the key only drops articles.
    assert _subclass_name_key("Light Domain") != _subclass_name_key("Twilight Domain")
    assert _subclass_name_key("Thief") != _subclass_name_key("Master Thief")


def test_existing_subclass_name_finds_the_twin_and_leaves_neighbours_alone():
    listed = ["Circle of Stars", "Light Domain", "Thief"]
    assert _existing_subclass_name(listed, "Circle of the Stars") == "Circle of Stars"
    assert _existing_subclass_name(listed, "Twilight Domain") is None
    assert _existing_subclass_name(listed, "Master Thief") is None
    # An exact repeat is not a "twin" — the merge already handles it.
    assert _existing_subclass_name(listed, "Circle of Stars") is None


def test_one_spelling_per_subclass_across_every_class():
    load_manual_data()
    seen = {}
    for cls, data in CLASSES.items():
        for name in data.get("subclasses", []):
            seen.setdefault((cls, _subclass_name_key(name)), []).append(name)
    duplicates = {k: v for k, v in seen.items() if len(v) > 1}
    assert duplicates == {}, f"a class lists one subclass twice: {duplicates}"


def test_the_two_reported_twins_are_gone_and_the_kept_option_has_features():
    load_manual_data()
    druid = CLASSES["Druid"]["subclasses"]
    warlock = CLASSES["Warlock"]["subclasses"]
    assert "Circle of Stars" in druid
    assert "Circle of the Stars" not in druid
    assert "Genie" in warlock
    assert "The Genie" not in warlock
    # The whole point of folding: the option a user can actually pick gets the features.
    assert SUBCLASS_FEATURES.get("Circle of Stars"), "Druid stars subclass lost its features"
    assert SUBCLASS_FEATURES.get("Genie"), "the Genie patron was left with no features"


def test_light_weaver_merged_into_the_listed_name():
    load_manual_data()
    sorcerer = CLASSES["Sorcerer"]["subclasses"]
    assert "Light Weaver" in sorcerer
    assert "Light Weaver Sorcerous Origin" not in sorcerer
    assert SUBCLASS_FEATURES.get("Light Weaver"), "Light Weaver lost its features"


def test_a_class_feature_is_not_listed_as_a_subclass():
    load_manual_data()
    # "Horns Wildly Blowing" is the Rider's 14th-level feature (AiME p.74/75).
    assert CLASSES["Slayer"]["subclasses"] == ["The Rider", "Foe-Hammer"]
    rider = SUBCLASS_FEATURES.get("The Rider", {})
    assert "Horns Wildly Blowing" in [n for names in rider.values() for n in names]


def test_no_subclass_option_is_featureless_beyond_the_known_gap():
    load_manual_data()
    featureless = {
        (cls, name)
        for cls, data in CLASSES.items()
        for name in data.get("subclasses", [])
        if name not in SUBCLASS_FEATURES
    }
    assert featureless == KNOWN_FEATURELESS_OPTIONS, (
        "a subclass option has no features — either wire its data up or record it here: "
        f"{sorted(featureless - KNOWN_FEATURELESS_OPTIONS)}"
    )


def test_every_runtime_limited_use_key_still_resolves_to_a_badge():
    """The nine features added alongside these fixes (NPC/race/subclass abilities,
    plus the Quickened/Subtle metamagic) must keep their sheet badge."""
    load_manual_data()
    clean = lambda k: re.sub(r"\s*\([^)]*\)\s*$", "", k).strip()  # noqa: E731
    missing = sorted(
        k for k in LIMITED_USE
        if k not in FEATURE_ACTION_TYPES and clean(k) not in FEATURE_ACTION_TYPES
    )
    assert missing == []
