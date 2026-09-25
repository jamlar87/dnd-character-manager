"""Constructs must not be described as living creatures.

The bestiary wording says "Full body, single creature", which is why the battering ram came out
fleshy and the clockwork entries came out organic — the prompt asserted the thing was alive. These
assertions pin both directions: constructs get the built-from-materials wording and never the
creature one, and ordinary monsters keep the creature wording.

The false-positive cases matter as much as the positive ones: a "Battering Shield" is an ordinary
shield whose subtitle can mention siege equipment, and giving it a siege engine's description is
the same class of mistake as the ram looking alive.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from services.ref_portraits import construct_cue, prompt_for  # noqa: E402


@pytest.mark.parametrize("name,subtitle", [
    ("Clockwork Soldier", "Construct · CR 1"),
    ("Battering Ram", "Construct · CR 5"),
    ("Bronze Golem", "Construct · CR 11"),
    ("Animated Armor", "Construct · CR 1"),
    ("Airship", "Vehicle"),
    ("Stone Golem", ""),
])
def test_constructs_are_detected(name, subtitle):
    assert construct_cue(name, subtitle), f"{name} should be treated as a construct"


@pytest.mark.parametrize("name,subtitle", [
    ("Goblin", "Humanoid · CR 1/4"),
    ("Adult Red Dragon", "Dragon · CR 17"),
    ("Battering Shield", "Siege equipment"),
    ("Dire Wolf", "Beast · CR 1"),
])
def test_living_things_are_not(name, subtitle):
    assert construct_cue(name, subtitle) is None, f"{name} is not a construct"


def test_construct_prompt_never_claims_a_creature():
    p = prompt_for("creature", "Battering Ram", "Construct · CR 5", "")
    assert "single creature" not in p, "the construct prompt must not call it a creature"
    # The cue is kept short now: SDXL truncates at 77 tokens, and a wordy cue pushed the subject off
    # the end of the prompt (see test_prompt_token_budget.py). "a built machine" carries the same
    # meaning as "built, not born" in half the tokens.
    for word in ("siege engine", "built machine"):
        assert word in p


def test_clockwork_prompt_is_mechanical():
    p = prompt_for("creature", "Clockwork Soldier", "Construct · CR 1", "")
    for word in ("clockwork", "gears", "no flesh"):
        assert word in p


def test_vehicle_prompt_has_no_occupants():
    p = prompt_for("item", "Airship", "Vehicle", "")
    for word in ("no people", "no crew"):
        assert word in p


def test_ordinary_creature_keeps_the_bestiary_wording():
    p = prompt_for("creature", "Goblin", "Humanoid · CR 1/4", "")
    assert "single creature" in p
    assert "no flesh" not in p


def test_subtitle_alone_only_matches_the_plain_type_word():
    """A named family in the subtitle must not trigger that family's description."""
    assert construct_cue("Bookkeeper", "Construct · CR 5")
    assert construct_cue("Battering Shield", "Siege equipment") is None
