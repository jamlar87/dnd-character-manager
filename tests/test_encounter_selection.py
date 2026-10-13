"""The encounter builder's monster selection: the whole library, and the parameters it is given.

Why these tests exist:

Three defects lived here and none of them was visible from the UI. The pool turned out to be the
whole library already (2201 monsters, every creature in the entity index) — that part was fine. But
the ENVIRONMENT filter's main condition read

    any(e in env_words or e in suitable or e in extra for e in env_words)

and `e in env_words` is true for every element of a non-empty list, so it short-circuited to True
and filtered nothing at all: 2201 of 2201 monsters were accepted for forest, desert and underdark
alike. The negative list then banned frogs, toads, turtles, crocodiles, otters and even a
rhinoceros from land environments, so a Giant Frog could not appear in a FOREST while a Young White
Dragon could appear in a DESERT.

A test that only asserted "an encounter came back" passed through all of it, so these assert the
things a DM would notice: that the setting rules monsters out, and that the difficulty lands on the
DMG budget.
"""

from __future__ import annotations

import json
import random

import pytest

from routes.characters import _load_monster_cache
from routes.dm import _monster_habitat_evidence, _monster_habitats

BUILD = "/api/dm/ai/build-encounter"


def _build(client, headers, **params):
    body = {"encounter_type": "combat", "party_size": 4, "party_level": 8}
    body.update(params)
    r = client.post(BUILD, json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _all_names(*responses):
    return [str(m.get("name", "")) for d in responses for m in (d.get("composition") or [])]


# ── the library ───────────────────────────────────────────────────────────────────────────

def test_the_pool_is_the_whole_monster_library():
    """The builder must be able to pick any creature the app knows, not an SRD subset."""
    pool = _load_monster_cache()
    assert len(pool) > 2000, f"the pool shrank to {len(pool)}"
    assert all(m.get("challenge_rating") not in (None, "") for m in pool), \
        "a monster with no CR can never be placed on a budget"
    # every creature the entity index (search, art, monster pages) serves must be reachable
    from services import entity_search as es

    indexed = {str(e.get("name", "")).lower() for e in es.iter_entities("creature")}
    pickable = {str(m.get("name", "")).lower() for m in pool}
    assert not (indexed - pickable), f"unreachable creatures: {sorted(indexed - pickable)[:5]}"


# ── the environment rules monsters out, not just in ───────────────────────────────────────

def test_cold_immunity_is_evidence_of_a_cold_home():
    """A cold-immune creature is not a desert encounter, whatever its broad type allows."""
    assert "arctic" in _monster_habitat_evidence({"name": "Frost Giant", "damage_immunities": "['cold']"})
    # a dragon's type list is broad by design, which is exactly why evidence has to win
    dragon = {"name": "Young White Dragon", "type": "dragon", "damage_immunities": "['cold']"}
    assert "arctic" in _monster_habitat_evidence(dragon)
    assert "desert" in _monster_habitats(dragon), "the type list still allows it somewhere"
    assert not ({"arctic"} & {"desert"}), "and the conflict table keeps it out of the desert"


def test_a_swim_speed_is_evidence_of_water():
    assert "coastal" in _monster_habitat_evidence(
        {"name": "Aboleth", "speed": json.dumps({"walk": "10 ft.", "swim": "40 ft."})})
    assert "underdark" in _monster_habitat_evidence(
        {"name": "Purple Worm", "speed": json.dumps({"walk": "50 ft.", "burrow": "30 ft."})})


def test_the_environment_parameter_changes_the_pick(client, auth_headers):
    """The measured failure: forest, desert and underdark accepted the same 2201 monsters."""
    arctic = [_build(client, auth_headers, environment="arctic") for _ in range(3)]
    desert = [_build(client, auth_headers, environment="desert") for _ in range(3)]
    for name in _all_names(*arctic):
        assert not _is(name, "sand", "dune", "desert"), f"{name} is not an arctic pick"
    for name in _all_names(*desert):
        assert not _is(name, "frost", "ice", "snow", "winter", "glacial", "yeti"), \
            f"{name} is not a desert pick"


def _is(name, *markers):
    low = name.lower()
    return any(m in low for m in markers)


def test_the_aquatic_ban_does_not_eat_correct_land_picks():
    """It banned frog, toad, turtle, crocodile, otter, beaver and rhinoceros from land settings, so
    a Giant Frog could not appear in a forest — a false positive is worse than a miss, because it
    throws away a pick that was right."""
    import re
    from pathlib import Path

    import routes.dm as dm_routes

    src = Path(dm_routes.__file__).read_text()
    ban = re.search(r"aquatic_keywords = \[(.*?)\]", src, re.S)
    assert ban, "the aquatic ban moved; this test needs updating"
    for wrong in ("frog", "toad", "turtle", "crocodile", "otter", "beaver", "rhinoceros"):
        assert f'"{wrong}"' not in ban.group(1), f"'{wrong}' is not an aquatic creature"
    for right in ("whale", "shark", "octopus", "jellyfish", "sahuagin"):
        assert f'"{right}"' in ban.group(1)


# ── the difficulty parameter ──────────────────────────────────────────────────────────────

SEEDS = (1, 2, 3, 4, 5)


def _build_seeded(client, headers, seed, **params):
    """Seed the selector so the composition is reproducible.

    The route shuffles its candidate pool, so an assertion on one unseeded build is a sample of a
    distribution ~30 points wide: a narrow band fails on a coin toss and a wide one proves nothing.
    Seeding makes the draw a fixed input, so the band below can be tight and still deterministic.

    Why the band is not tighter, measured across 30 unseeded builds per difficulty: the granularity
    of the XP table is coarse at the bottom (an "easy" encounter for four level-3 characters is a
    300 XP budget against 50 XP creatures, so 116% and 166% are neighbours) and the DMG's own
    multiplier for 3-6 creatures doubles everything. Before this pass was fixed the builder aimed at
    85%-150% of budget by construction and the medians were 122-166%; they are 106-133% now.
    """
    state = random.getstate()
    random.seed(seed)
    try:
        return _build(client, headers, **params)
    finally:
        random.setstate(state)


@pytest.mark.parametrize("difficulty,level", [("easy", 3), ("medium", 8), ("hard", 10), ("deadly", 12)])
def test_the_difficulty_lands_on_the_dmg_budget(client, auth_headers, difficulty, level):
    """Adjusted XP against the budget for that level and party — the DMG's own arithmetic, so a
    build that ignores the difficulty parameter cannot pass."""
    pcts = []
    for seed in SEEDS:
        d = _build_seeded(client, auth_headers, seed, environment="forest",
                          difficulty=difficulty, party_level=level)
        xp = d["xp"]
        assert xp["budget"] > 0 and xp["adjusted"] > 0
        assert d["composition"], "an encounter with no creatures is not an encounter"
        assert 40 <= xp["budget_pct"] <= 190, f"{difficulty} L{level} seed {seed}: {xp['budget_pct']}%"
        pcts.append(xp["budget_pct"])
    pcts.sort()
    median = pcts[len(pcts) // 2]
    assert 60 <= median <= 145, f"{difficulty} L{level}: median {median}% of budget ({pcts})"


def test_a_higher_difficulty_buys_more_budget(client, auth_headers):
    easy = _build_seeded(client, auth_headers, 1, environment="forest", difficulty="easy", party_level=8)
    deadly = _build_seeded(client, auth_headers, 1, environment="forest", difficulty="deadly", party_level=8)
    assert deadly["xp"]["budget"] > easy["xp"]["budget"]


def test_the_target_cr_parameter_steers_the_pool(client, auth_headers):
    """A DM asking for CR 5 should not be handed CR 0 swarm filler as the main threat."""
    d = _build_seeded(client, auth_headers, 1, environment="underdark", target_cr="5",
                      difficulty="medium")
    crs = [float(m.get("cr", 0) or 0) for m in (d.get("composition") or [])]
    assert crs, "nothing was selected for an explicit target CR"
    assert max(crs) >= 2, f"target CR 5 produced only {crs}"
