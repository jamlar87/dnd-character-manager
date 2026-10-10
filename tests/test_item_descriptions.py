"""Weapon descriptions must state a ranged weapon's range.

`_build_item_description` derived a "Range X/Y ft" string and then dropped it, so
a longbow described itself as "Martial ranged weapon 1d8 piercing ammunition ..."
with no range anywhere. The fix only adds the segment for Ranged weapons: a melee
weapon's `range` is always 5 ft (noise), and a thrown melee weapon already prints
its range through the Thrown property (so adding it again would duplicate it).
"""

import pytest

from services.items import _build_item_description


def _weapon(**over):
    item = {"equipment_category": {"name": "Weapon"}, "weapon_category": "Martial",
            "weapon_range": "Ranged", "range": {"normal": 150, "long": 600},
            "damage": {"damage_dice": "1d8", "damage_type": {"name": "Piercing"}},
            "properties": [{"name": "Ammunition"}, {"name": "Heavy"}],
            "weight": 2, "cost": {"quantity": 50, "unit": "gp"}}
    item.update(over)
    return item


def test_ranged_weapon_description_carries_its_range():
    text = _build_item_description(_weapon())
    assert "range 150/600 ft" in text, text


def test_ranged_weapon_without_a_long_range_shows_the_single_value():
    text = _build_item_description(_weapon(range={"normal": 80}))
    assert "range 80 ft" in text, text


def test_melee_weapon_gains_no_range_segment():
    """A melee weapon's range is always 5 ft — printing it is noise."""
    text = _build_item_description(_weapon(weapon_range="Melee", range={"normal": 5},
                                           properties=[{"name": "Versatile"}]))
    assert "range" not in text, text


def test_thrown_melee_weapon_does_not_print_its_range_twice():
    """The Thrown property already shows the range; the segment must not repeat it."""
    text = _build_item_description(_weapon(weapon_category="Simple", weapon_range="Melee",
                                           range={"normal": 30, "long": 120},
                                           throw_range={"normal": 30, "long": 120},
                                           damage={"damage_dice": "1d6",
                                                   "damage_type": {"name": "Piercing"}},
                                           properties=[{"name": "Thrown"}]))
    assert "thrown (range 30/120)" in text, text
    assert text.count("30/120") == 1, text


def test_real_longbow_in_the_item_index_keeps_its_range():
    """End-to-end: ITEM_INDEX precomputes each description with this builder."""
    from main import ITEM_INDEX
    entry = next((v for k, v in ITEM_INDEX.items()
                  if isinstance(v, dict) and v.get("name") == "Longbow"), None)
    if not entry:
        pytest.skip("Longbow not in ITEM_INDEX")
    text = entry.get("description") or _build_item_description(entry)
    assert "range 150/600 ft" in text, text


def test_real_melee_weapon_in_the_item_index_has_no_range_segment():
    from main import ITEM_INDEX
    entry = next((v for k, v in ITEM_INDEX.items()
                  if isinstance(v, dict) and v.get("name") == "Longsword"), None)
    if not entry:
        pytest.skip("Longsword not in ITEM_INDEX")
    text = entry.get("description") or _build_item_description(entry)
    assert "range 5 ft" not in text, text
