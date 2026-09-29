"""The AI encounter builder must USE the inputs the DM-tools form sends.

The form (templates/dm_tools.html, `#aiEncounterForm`) collects an encounter type and a
free-text Tone and posts them to /api/dm/ai/build-encounter. The route read both and then
built a narrative prompt from the monster list alone, so a DM could type "sinister" and get
flavor text that ignored it, while the per-archetype design brief (ambush setup, faction
behaviour, social tension) sat in a local variable that nothing interpolated. Nothing
errored: the encounter still came back, just blander and off-brief.

These tests drive the real endpoint (auth + CSRF via the seeded_db fixture) with `_call_ai`
captured, and assert on the prompt the model would actually receive.
"""

from __future__ import annotations

import routes.dm as dm_routes

_ENCOUNTER_JSON = (
    '{"name": "Test Encounter", "description": "desc", '
    '"tactics": "tactics", "dynamic": "dynamic"}'
)


class _CapturePrompt:
    """Stands in for routes.dm._call_ai, recording every prompt it is asked to send."""

    def __init__(self):
        self.prompts = []

    async def __call__(self, prompt="", label=""):
        self.prompts.append(prompt)
        return _ENCOUNTER_JSON


def _build_encounter(client, headers, **payload):
    cap = _CapturePrompt()
    original = dm_routes._call_ai
    dm_routes._call_ai = cap
    try:
        response = client.post("/api/dm/ai/build-encounter", json=payload, headers=headers)
    finally:
        dm_routes._call_ai = original
    return response, cap


def _flavor_prompt(cap):
    """The narrative (phase-2) prompt — the one that carries the DM's brief."""
    assert cap.prompts, "the encounter route never called the model"
    return cap.prompts[-1]


def test_the_requested_tone_reaches_the_prompt(client, seeded_db, auth_headers):
    response, cap = _build_encounter(
        client, auth_headers,
        party_level=5, party_size=4, environment="forest",
        difficulty="medium", encounter_type="skirmish", tone="sinister, tragic",
    )
    assert response.status_code == 200, response.text
    prompt = _flavor_prompt(cap)
    assert "sinister, tragic" in prompt, (
        "the Tone field is read by the route and never reaches the model — the DM types a "
        f"tone and gets flavor text that ignores it. Prompt was:\n{prompt}")


def test_an_empty_tone_adds_no_noise_to_the_prompt(client, seeded_db, auth_headers):
    """Tone is optional: no tone must mean no tone line, not 'Tone: '."""
    response, cap = _build_encounter(
        client, auth_headers,
        party_level=5, party_size=4, environment="dungeon",
        difficulty="medium", encounter_type="skirmish",
    )
    assert response.status_code == 200, response.text
    assert "Tone:" not in _flavor_prompt(cap), "an empty Tone still emitted a line"


def test_the_encounter_type_brief_reaches_the_prompt(client, seeded_db, auth_headers):
    """Each archetype carries a design brief; the narrative must be told about it."""
    for etype, marker in (
        ("ambush", "ambush setup"),
        ("social_combat", "Negotiation first"),
        ("rival_faction", "faction_a"),
    ):
        response, cap = _build_encounter(
            client, auth_headers,
            party_level=7, party_size=4, environment="ruins",
            difficulty="hard", encounter_type=etype,
        )
        assert response.status_code == 200, response.text
        prompt = _flavor_prompt(cap)
        assert marker in prompt, (
            f"the {etype} design brief never reaches the model (expected {marker!r}). "
            f"Prompt was:\n{prompt}")


def test_the_prompt_does_not_ask_the_model_to_repick_the_creatures(client, seeded_db, auth_headers):
    """The composition is picked by the algorithm; the brief must not invite a rewrite.

    The archetype briefs are written for the picking phase ("Pick 2-4 monster types..."),
    so sending one to the narrative phase without that constraint invites inventing
    creatures the encounter does not contain.
    """
    response, cap = _build_encounter(
        client, auth_headers,
        party_level=5, party_size=4, environment="dungeon",
        difficulty="medium", encounter_type="ambush",
    )
    assert response.status_code == 200, response.text
    prompt = _flavor_prompt(cap)
    assert "do NOT add, swap or drop creatures" in prompt
