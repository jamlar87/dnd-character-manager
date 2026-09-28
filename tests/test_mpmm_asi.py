"""Monsters of the Multiverse flexible ASI (Tortle).

MPMM lets the player choose "+2 to one ability score and +1 to a different one, or +1 to three
different ones" (MPMM p.5, "Ability Score Increases" — chapter-wide). The record in RACES already carries a default +2/+1 spread, so a
player's pick must REPLACE that spread — adding would silently grant four points. These tests
pin the server half of that contract, and guard the client half (the wizard JS, now
static/create.js) so the picker cannot drift back into "add" behaviour or stop being wired up.
"""
import re
from pathlib import Path

import pytest

import main
from routes.characters.creation import _race_asi

REPO = Path(__file__).resolve().parent.parent
CREATE_HTML = (REPO / "templates" / "create.html").read_text()
#: the wizard's JS moved out of the template into a versioned static asset
CREATE_JS = (REPO / "static" / "create.js").read_text()


# ── the flag itself ──────────────────────────────────────────────────────────

def test_every_mpmm_sourced_race_uses_the_mpmm_rule():
    """The invariant, rather than a hand-kept list: whatever race the app cites to MPMM must
    use MPMM's choice rule. Ingestion added eight races (Fairy, Genasi, Githyanki, Githzerai,
    Harengon, Satyr, Yuan-ti) whose records are all-zero by design."""
    sourced = {
        n for n, r in main.RACES.items()
        if isinstance(r, dict) and "MPMM" in (str(r.get("_source_slug") or ""),
                                             str(r.get("_source_manual") or ""))
    }
    assert sourced, "no race is tagged MPMM — has the source slug stopped being set?"
    missing = sourced - set(main.MPMM_ASI_RACES)
    assert not missing, f"MPMM races missing from MPMM_ASI_RACES: {sorted(missing)}"
    assert main.MPMM_ASI_RACES


def test_an_ingested_mpmm_race_starts_with_no_increase():
    """MPMM's races carry the choice, not a default — so a blank picker really means nothing.
    The wizard warns about this (create.html sets #mpmm-hint in red for exactly this case)."""
    for race in ("Fairy", "Harengon", "Yuan-ti"):
        assert sum(main.RACES[race]["asi"].values()) == 0, f"{race} should have no fixed ASI"


def test_a_zero_asi_mpmm_race_takes_the_pick_as_its_whole_spread():
    assert _race_asi("Harengon", "", ["dexterity", "constitution"], "two") == \
        {"dexterity": 2, "constitution": 1}
    assert _race_asi("Fairy", "", ["dexterity", "constitution", "wisdom"], "three") == \
        {"dexterity": 1, "constitution": 1, "wisdom": 1}


def test_a_zero_asi_mpmm_race_with_no_pick_stays_at_zero():
    """Honest behaviour: no pick, no increase — the UI is responsible for saying so."""
    assert _race_asi("Harengon", "", [], "") == {}


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
    the flexible-ASI picker's neighbour). The list rides in the cached asset now — the route
    builds the payload and services.reference_assets writes the Set."""
    src = (REPO / "routes" / "characters" / "creation.py").read_text()
    assert '"MPMM_ASI": list(MPMM_ASI_RACES)' in src
    assert 'CREATE_SET_CONSTS = ("FLEXIBLE_ASI", "MPMM_ASI")' in (
        REPO / "services" / "reference_assets.py").read_text()
    assert "const MPMM_ASI = " not in CREATE_HTML, "the const is the asset's job now"


def test_picker_block_is_present():
    assert 'id="mpmm-picks"' in CREATE_HTML
    assert 'name="mpmm-mode" value="two"' in CREATE_HTML
    assert 'name="mpmm-mode" value="three"' in CREATE_HTML


def test_client_replaces_the_default_bonus_dict():
    """The JS must drop the record's bonuses before applying the pick — `bonuses = {}`."""
    body = re.search(r"const _mpmm = isMpmmRace\(\).*?\n(.*?)\n  document\.getElementById\('asi-bonus'\)",
                     CREATE_JS, re.S)
    assert body, "MPMM preview block missing from updateAsiPreview()"
    assert "bonuses = {}" in body.group(1)


def test_payload_sends_the_mode_and_picks():
    assert 'asi_mode: isMpmmRace() ? state.mpmm_mode : ""' in CREATE_JS
    assert "asi_picks: isMpmmRace() ? mpmmPicks().map(x => x[0])" in CREATE_JS


def test_race_change_clears_the_mpmm_picks():
    assert 'state.mpmm_mode = ""; state.mpmm_p2 = ""; state.mpmm_p1 = ""; state.mpmm_t3 = [];' \
        in CREATE_JS


# ── the subrace-shaped entries (Genasi) ──────────────────────────────────────

def test_a_genasi_subrace_can_pick():
    """MPMM lists the four Genasi as races of their own (MPMM p.16-17: "Genasi, Air/Earth/Fire/
    Water") while this app models ONE race carrying the four element subraces. Every check used
    to read the race name, so the "Air Genasi"/"Earth Genasi" entries in MPMM_ASI_RACES could
    never be reached: the picker never appeared and a pick would have been ignored server-side."""
    assert _race_asi("Genasi", "Air Genasi", ["strength", "dexterity"], "two") == {
        "strength": 2, "dexterity": 1}


def test_all_four_genasi_elements_are_listed():
    """Fire and Water were missing from the set although the same book lists all four."""
    assert {"Air Genasi", "Earth Genasi", "Fire Genasi", "Water Genasi"} <= set(main.MPMM_ASI_RACES)
    assert _race_asi("Genasi", "Fire Genasi", ["strength", "dexterity", "wisdom"], "three") == {
        "strength": 1, "dexterity": 1, "wisdom": 1}


def test_a_genasi_without_a_pick_keeps_its_eepc_default():
    """The choice is optional: blank keeps the record's spread plus the subrace's (EEPC p.9)."""
    assert _race_asi("Genasi", "Air Genasi", [], "") == {"constitution": 2, "dexterity": 1}
    assert _race_asi("Genasi", "", [], "") == {"constitution": 2}


#: MPMM races this app has not ingested yet — inert by design, kept so the picker works the day
#: one is added (MPMM p.18-19). An entry that is neither here nor a real race/subrace is rot.
NOT_YET_INGESTED = {"Githyanki", "Githzerai"}


def test_every_mpmm_entry_matches_a_real_race_or_subrace():
    """The Genasi entries sat unreachable for who knows how long because nothing checked that an
    entry still names something the app has."""
    known = set(main.RACES)
    subraces = {s for r in main.RACES.values() for s in (r.get("subraces") or [])}
    orphans = sorted(n for n in main.MPMM_ASI_RACES
                     if n not in known | subraces and n not in NOT_YET_INGESTED)
    assert not orphans, f"MPMM_ASI_RACES entries match no race or subrace: {orphans}"


# ── client half: the same either-name rule ───────────────────────────────────

def test_the_client_matches_the_subrace_too():
    assert "function isMpmmRace()" in CREATE_JS
    assert "MPMM_ASI.has(state.subrace)" in CREATE_JS
    # no check may go back to the bare race name
    assert "MPMM_ASI.has(state.race) && mpmmPicks()" not in CREATE_JS
    assert 'asi_mode: MPMM_ASI.has(state.race)' not in CREATE_JS
    assert CREATE_JS.count("isMpmmRace()") >= 5


def test_changing_the_subrace_clears_the_picks():
    """A pick belongs to the subrace that offered it (Air and Fire Genasi are different MPMM
    races), so switching element cannot leave the previous spread in place."""
    body = re.search(r"function selectSubrace\(s\) \{(.*?)\n  state\.subrace = s;", CREATE_JS, re.S)
    assert body, "selectSubrace no longer sets state.subrace"
    assert 'state.mpmm_mode = ""' in body.group(1)
