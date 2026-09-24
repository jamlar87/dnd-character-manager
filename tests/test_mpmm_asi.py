"""Monsters of the Multiverse flexible ASI (Tortle).

MPMM lets the player choose "+2 to one ability score and +1 to a different one, or +1 to three
different ones" (MPMM p.6). The record in RACES already carries a default +2/+1 spread, so a
player's pick must REPLACE that spread — adding would silently grant four points. These tests
pin the server half of that contract, and guard the client half (create.html) so the picker
cannot drift back into "add" behaviour or stop being wired up at all.
"""
import re
from pathlib import Path

import pytest

import main
from routes.characters.creation import _race_asi

REPO = Path(__file__).resolve().parent.parent
CREATE_HTML = (REPO / "templates" / "create.html").read_text()


# ── the flag itself ──────────────────────────────────────────────────────────

def test_tortle_is_the_mpmm_race():
    assert main.MPMM_ASI_RACES == {"Tortle"}


def test_tortle_record_keeps_the_default_spread():
    """The replacement branch assumes the record HAS a spread to replace."""
    assert main.RACES["Tortle"]["asi"] == {"strength": 2, "wisdom": 1}


# ── server: the chosen spread replaces, never adds ───────────────────────────

def test_no_pick_keeps_the_default_spread():
    assert _race_asi("Tortle", "", [], "") == {"strength": 2, "wisdom": 1}


@pytest.mark.parametrize("picks", [[], ["dexterity"], [""]])
def test_incomplete_pick_keeps_the_default(picks):
    assert _race_asi("Tortle", "", picks, "two") == {"strength": 2, "wisdom": 1}


def test_plus2_plus1_replaces_the_default():
    got = _race_asi("Tortle", "", ["dexterity", "constitution"], "two")
    assert got == {"dexterity": 2, "constitution": 1}
    assert "strength" not in got and "wisdom" not in got, "pick must REPLACE, not add"


def test_three_plus1_replaces_the_default():
    got = _race_asi("Tortle", "", ["dexterity", "constitution", "wisdom"], "three")
    assert got == {"dexterity": 1, "constitution": 1, "wisdom": 1}


def test_same_ability_twice_is_not_a_legal_plus2_plus1():
    """+2 CON / +1 CON is not a spread MPMM allows — fall back to the default."""
    assert _race_asi("Tortle", "", ["constitution", "constitution"], "two") == \
        {"strength": 2, "wisdom": 1}


def test_repeated_ability_is_not_a_legal_three_plus1():
    assert _race_asi("Tortle", "", ["dexterity", "dexterity", "constitution"], "three") == \
        {"strength": 2, "wisdom": 1}


def test_unknown_mode_is_ignored():
    assert _race_asi("Tortle", "", ["dexterity", "constitution"], "banana") == \
        {"strength": 2, "wisdom": 1}


def test_points_granted_stay_at_three():
    """Whatever the shape, an MPMM race grants exactly three points."""
    for picks, mode in ((["dexterity", "constitution"], "two"),
                        (["dexterity", "constitution", "wisdom"], "three"),
                        ([], "")):
        assert sum(_race_asi("Tortle", "", picks, mode).values()) == 3


# ── other races keep their own rules ─────────────────────────────────────────

def test_half_elf_picks_still_add_to_the_record():
    got = _race_asi("Half-Elf", "", ["dexterity", "wisdom"], "")
    assert got["charisma"] == 2
    assert got["dexterity"] == 1 and got["wisdom"] == 1


def test_half_elf_pick_is_not_swallowed_when_the_record_omits_the_ability():
    """Regression: the pick used to apply only if the ability already existed in the record,
    so choosing DEX/WIS for a half-elf granted nothing at all."""
    for race_name in ("Half-Elf",):
        got = _race_asi(race_name, "", ["dexterity", "intelligence"], "")
        assert got["dexterity"] == 1, f"{race_name}: +1 DEX pick was swallowed"
        assert got["intelligence"] == 1, f"{race_name}: +1 INT pick was swallowed"


@pytest.mark.parametrize("race_name", sorted(main.FLEXIBLE_ASI_RACES))
def test_flexible_pick_grants_its_two_points(race_name):
    """Regression: same swallow — these races have NO record ASI, so the membership test
    rejected every pick and the player got nothing."""
    got = _race_asi(race_name, "", ["dexterity"], "")
    assert got["dexterity"] == 2, f"{race_name}: +2 DEX pick was swallowed"


def test_flexible_asi_race_still_adds():
    flexible = next(iter(main.FLEXIBLE_ASI_RACES))
    base = dict((main.RACES.get(flexible, {}) or {}).get("asi", {}))
    got = _race_asi(flexible, "", ["dexterity"], "")
    assert got.get("dexterity", 0) == base.get("dexterity", 0) + 2


def test_a_plain_race_ignores_picks():
    got = _race_asi("Dwarf", "", ["dexterity", "charisma"], "two")
    assert got == dict(main.RACES["Dwarf"]["asi"])


# ── client half: the picker must exist, be wired, and replace ────────────────

def test_template_receives_the_mpmm_race_list():
    """Without this context var the picker block never renders (the bug class that killed
    the flexible-ASI picker's neighbour)."""
    src = (REPO / "routes" / "characters" / "creation.py").read_text()
    assert "mpmm_asi_races=list(MPMM_ASI_RACES)" in src
    assert "const MPMM_ASI = new Set({{ mpmm_asi_races | tojson }});" in CREATE_HTML


def test_picker_block_is_present():
    assert 'id="mpmm-picks"' in CREATE_HTML
    assert 'name="mpmm-mode" value="two"' in CREATE_HTML
    assert 'name="mpmm-mode" value="three"' in CREATE_HTML


def test_client_replaces_the_default_bonus_dict():
    """The JS must drop the record's bonuses before applying the pick — `bonuses = {}`."""
    body = re.search(r"const _mpmm = MPMM_ASI\.has.*?\n(.*?)\n  document\.getElementById\('asi-bonus'\)",
                     CREATE_HTML, re.S)
    assert body, "MPMM preview block missing from updateAsiPreview()"
    assert "bonuses = {}" in body.group(1)


def test_payload_sends_the_mode_and_picks():
    assert 'asi_mode: MPMM_ASI.has(state.race) ? state.mpmm_mode : ""' in CREATE_HTML
    assert "asi_picks: MPMM_ASI.has(state.race) ? mpmmPicks().map(x => x[0])" in CREATE_HTML


def test_race_change_clears_the_mpmm_picks():
    assert 'state.mpmm_mode = ""; state.mpmm_p2 = ""; state.mpmm_p1 = ""; state.mpmm_t3 = [];' \
        in CREATE_HTML
