"""Cantrip parity: the creator picker, the class tables, and the PHB must agree.

Ground truth (Player's Handbook):
  * "At 1st level, you know N cantrips of your choice ..." — Bard 2 (p.52),
    Cleric 3 (p.57), Druid 2 (p.65), Sorcerer 4 (p.100), Warlock 2 (p.105),
    Wizard 3 (p.113).
  * Growth: Bard 3@4, 4@10 · Cleric 4@4, 5@10 · Druid 3@4, 4@10 ·
    Sorcerer 5@4, 6@10 · Warlock 3@4, 4@10 · Wizard 4@4, 5@10.
  * "A cantrip is a spell that can be cast at will, without using a spell slot
    and without being prepared in advance." — PHB p.201.

The old creator hardcoded {Bard:2, Sorcerer:4, Warlock:2} in create-spells.js
and only opened for "known" casters, so Cleric/Druid/Wizard silently got zero
cantrips. These tests pin the data source and the picker wiring.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app
from core.data import SPELL_DICE, SRD_SPELLS
from services.leveling import get_cantrips_known_max, get_spells_known_max

STATIC = Path(__file__).parent.parent / "static"

PHB_CANTRIPS_L1 = {"Bard": 2, "Cleric": 3, "Druid": 2, "Sorcerer": 4, "Warlock": 2, "Wizard": 3}
PHB_CANTRIPS_L20 = {"Bard": 4, "Cleric": 5, "Druid": 4, "Sorcerer": 6, "Warlock": 4, "Wizard": 5}

@pytest.fixture(scope="module")
def client():
    # Routers are registered in the lifespan hook, so the context manager matters.
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("cls,count", sorted(PHB_CANTRIPS_L1.items()))
def test_cantrips_known_level1_matches_phb(cls, count):
    assert get_cantrips_known_max(cls, 1) == count


@pytest.mark.parametrize("cls,count", sorted(PHB_CANTRIPS_L20.items()))
def test_cantrips_known_level20_matches_phb(cls, count):
    assert get_cantrips_known_max(cls, 20) == count


@pytest.mark.parametrize("cls", ["Paladin", "Ranger", "Fighter", "Barbarian", "Monk", "Rogue"])
def test_non_cantrip_classes_have_zero(cls):
    assert get_cantrips_known_max(cls, 1) == 0


def test_known_casters_spell_counts_match_phb():
    # PHB Spells Known at 1st level: Bard 4, Sorcerer 2, Warlock 2, Ranger 0 (starts at 2).
    assert get_spells_known_max("Bard", 1) == 4
    assert get_spells_known_max("Sorcerer", 1) == 2
    assert get_spells_known_max("Warlock", 1) == 2
    assert get_spells_known_max("Ranger", 1) == 0


@pytest.mark.parametrize("cls,count", sorted(PHB_CANTRIPS_L1.items()))
def test_quota_endpoint_reports_cantrips(client, cls, count):
    r = client.get("/api/spells/quota", params={"class_name": cls, "level": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["caster"] is True
    assert body["cantrips"] == count
    # Prepared casters get their levelled list auto-loaded, so they owe no picks.
    if cls in ("Cleric", "Druid", "Wizard"):
        assert body["prepared"] is True
        assert body["spells"] == 0
    else:
        assert body["prepared"] is False
        assert body["spells"] == get_spells_known_max(cls, 1)
    assert body["total"] == body["cantrips"] + body["spells"]


@pytest.mark.parametrize("cls", ["Fighter", "Barbarian", "Rogue", "Monk"])
def test_quota_endpoint_zero_for_mundane_classes(client, cls):
    body = client.get("/api/spells/quota", params={"class_name": cls, "level": 1}).json()
    assert body["cantrips"] == 0 and body["spells"] == 0 and body["caster"] is False


def test_quota_endpoint_clamps_level(client):
    body = client.get("/api/spells/quota", params={"class_name": "Wizard", "level": 99}).json()
    assert body["level"] == 20
    assert body["cantrips"] == 5


def test_creator_picker_is_not_hardcoded():
    """The picker must ask the server; a JS copy of the class table drifts."""
    src = (STATIC / "create-spells.js").read_text()
    assert "L1_CANTRIPS" not in src
    assert "L1_SPELLS_KNOWN" not in src
    assert "/api/spells/quota" in src
    # Prepared casters must still be offered their cantrips.
    assert "prepared" in src


def test_creator_core_uses_quota_gate():
    src = (STATIC / "create-core.js").read_text()
    assert "isKnownCaster()" not in src
    assert "_spellQuotaCached()" in src

def test_creator_persists_cantrips_for_prepared_caster(client, auth_headers, seeded_db):
    """A Cleric created with three cantrips must store three level-0 rows.

    The creator used to skip the cantrip step entirely for prepared casters, so
    the character landed with zero cantrips and no way to cast them.
    """
    import sqlite3

    picks = ["Sacred Flame", "Guidance", "Thaumaturgy"]
    r = client.post(
        "/api/character/create",
        headers=auth_headers,
        json={
            "name": "Cantrip Probe",
            "race": "Human",
            "class_name": "Cleric",
            "level": 1,
            "spells": [{"name": n, "level": 0} for n in picks],
        },
    )
    assert r.status_code == 200, r.text
    cid = r.json()["id"]

    con = sqlite3.connect(str(seeded_db["db_path"]))
    rows = con.execute(
        "SELECT spell_name, spell_level FROM character_spells WHERE character_id=? ORDER BY spell_name",
        (cid,),
    ).fetchall()
    con.close()
    cantrips = sorted(n for n, lvl in rows if lvl == 0)
    assert cantrips == sorted(picks)
    # Levelled spells are auto-loaded for prepared casters, so nothing at level 0
    # should have leaked into that path.
    assert all(lvl == 0 for n, lvl in rows if n in picks)


class TestCantripDamageScaling:
    """PHB p.201: cantrip damage grows with character level, never with a slot.

    Eldritch Blast (p.215) is the exception in shape: its dice stay 1d10 and the
    caster fires another beam at 5th/11th/17th, each rolling its own attack.
    """

    @pytest.mark.parametrize("level,expected", [
        (1, "1d10"), (4, "1d10"), (5, "1d10 ×2 beams"),
        (10, "1d10 ×2 beams"), (11, "1d10 ×3 beams"),
        (16, "1d10 ×3 beams"), (17, "1d10 ×4 beams"),
    ])
    def test_eldritch_blast_beam_scaling(self, level, expected):
        from services.leveling import _scaled_dice_display

        assert _scaled_dice_display(SPELL_DICE["eldritch blast"], level) == expected

    @pytest.mark.parametrize("level,expected", [
        (1, "1d10"), (5, "2d10"), (11, "3d10"), (17, "4d10"),
    ])
    def test_fire_bolt_dice_scaling(self, level, expected):
        from services.leveling import _scaled_dice_display

        assert _scaled_dice_display(SPELL_DICE["fire bolt"], level) == expected

    @pytest.mark.parametrize("name", [
        "Acid Splash", "Chill Touch", "Fire Bolt", "Poison Spray", "Produce Flame",
        "Ray of Frost", "Sacred Flame", "Shocking Grasp", "Vicious Mockery",
    ])
    def test_sheet_dice_match_srd_progression_table(self, name):
        """The sheet must agree with the SRD table, not with prose or guesswork."""
        from services.leveling import _scaled_dice_display

        srd = next(s for s in SRD_SPELLS if s.get("name", "").lower() == name.lower())
        table = srd["damage"]["damage_at_character_level"]
        entry = SPELL_DICE[name.lower()]
        for level in (1, 5, 11, 17):
            assert _scaled_dice_display(entry, level) == table[str(level)]

    def test_every_damage_cantrip_has_a_dice_entry(self):
        """A damage cantrip with no manifest row shows no dice on the sheet."""
        missing = [
            s["name"] for s in SRD_SPELLS
            if s.get("level") == 0
            and (s.get("damage") or {}).get("damage_at_character_level")
            and not SPELL_DICE.get(s["name"].lower())
        ]
        assert missing == []
