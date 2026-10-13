"""The AI features' client wiring and honesty, pinned where the failures were.

Each guard here is a defect found by auditing the live app against its own client code; the
server-side chain has its own file (tests/test_ai_chain.py).
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _read(rel):
    return (REPO / rel).read_text()


# ── a failure must not look like success ──────────────────────────────────────────────────

def test_relationship_generation_checks_the_http_status_not_just_an_error_field():
    """FastAPI errors carry `detail`, so reading only `result.error` let a 404 render as
    "✓ Generated!" with the name field set to the string "undefined"."""
    js = _read("static/sheet.js")
    fn = js[js.index("async function generateRelationship"):]
    fn = fn[:fn.index("\n}\n") + 3]
    assert "!resp.ok" in fn, "an HTTP error must be handled before the fields are filled"
    assert "result.detail" in fn, "FastAPI puts the reason in `detail`"
    assert "ai_generated === false" in fn, "the server's flag has to reach the user"


def test_the_spells_button_does_not_report_a_rejected_request_as_already_full():
    js = _read("static/sheet.js")
    fn = js[js.index("async function aiSelectSpells"):]
    fn = fn[:fn.index("\n}\n") + 3]
    assert "!r.ok" in fn and "d.detail" in fn
    assert fn.index("!r.ok") < fn.index("data.added"), "the status check comes first"


def test_the_sheet_renders_the_servers_own_portrait_error():
    """Rule: the UI must not invent a reason (it used to claim a 50/day quota the server never
    sent, while telling the user to wait for an image that was not coming)."""
    js = _read("static/sheet.js")
    assert "data.error" in js
    assert "Free tier limited to 50 requests/day" not in js


def test_the_portrait_retry_button_calls_a_function_this_page_defines():
    """It called generatePortrait(), which exists only in create.js — a dead button."""
    js = _read("static/sheet.js")
    assert 'onclick="regenerateSheetPortrait()"' in js
    assert 'onclick="generatePortrait()"' not in js
    assert "async function regenerateSheetPortrait()" in js
    # and the sheet page must not be relying on create.js
    scripts = _read("templates/_sheet_scripts.html")
    assert "create.js" not in scripts


def test_the_npc_sheet_flag_reports_what_happened():
    """Was hardcoded True: a total AI outage answered 200 with ai_generated: true and an empty
    description."""
    py = _read("routes/characters/relationships.py")
    assert '"ai_generated": bool(ai_text)' in py
    assert '"ai_generated": True' not in py
    assert "_extract_json" in py, "one parser, not a second hand-rolled one"


def test_the_reported_portrait_provider_is_the_one_that_dispatched():
    route = _read("routes/characters/ai_routes.py")
    service = _read("services/portraits.py")
    assert 'os.environ.get("PORTRAIT_PROVIDER", "pollinations")' not in route, \
        "a second default for the same env var misreports every failure"
    assert 'os.environ.get("PORTRAIT_PROVIDER", "horde")' in service
    assert "PORTRAIT_PROVIDER = _portraits.PORTRAIT_PROVIDER" in route


def test_get_prompt_does_not_spend_a_generation():
    js = _read("static/sheet.js")
    fn = js[js.index("async function fetchPortraitPrompt"):]
    fn = fn[:fn.index("\n}\n") + 3]
    assert "prompt_only: true" in fn
    route = _read("routes/characters/ai_routes.py")
    assert 'if data.get("prompt_only"):' in route
    assert "PORTRAIT_PROVIDER, \"prompt_only\": True" in route


# ── a second click must not cost a second AI call ─────────────────────────────────────────

def test_the_ai_encounter_submit_is_disabled_while_it_runs():
    js = _read("static/dm_tools.js")
    fn = js[js.index("async function generateAiEncounter"):]
    fn = fn[:fn.index("\n}\n") + 3]
    assert "submit.disabled = true" in fn and "finally" in fn
    assert "aiEncounterSubmit" in _read("templates/dm_tools.html")


# ── AI text is data, not markup ───────────────────────────────────────────────────────────

def test_ai_encounter_text_is_escaped_and_saved_without_interpolation():
    js = _read("static/dm_tools.js")
    assert "${dmEsc(enc.name)}" in js
    assert "${dmEsc(enc.description)}" in js
    assert "saveAiEncounterFromResult()" in js
    # the old handler interpolated model output into an onclick string
    assert "onclick=\"saveAiEncounter('${" not in js


def test_an_ai_built_npc_does_not_get_xp_from_its_hit_points():
    js = _read("static/dm_tools.js")
    assert "xp_reward: build.hit_points" not in js
    assert "xp_reward: 0," in js


def test_a_deterministic_endpoint_is_not_advertised_as_ai():
    """The spells picker scores a list; it calls no model. It was labelled "🤖 AI"."""
    html = _read("templates/sheet.html")
    assert "🤖 AI</button>" not in html
    spells = _read("routes/characters/spells.py")
    assert "_call_ai" not in spells, "if this endpoint ever becomes AI-backed, relabel it"


# ── the CPU-bound image path and art that is not art ─────────────────────────────────────

def test_map_thumbnails_are_encoded_off_the_event_loop():
    src = _read("routes/maps.py")
    assert "await run_in_threadpool(thumbnail_bytes" in src
    assert "run_in_threadpool(detect" in src, "same convention as the neighbouring route"


def test_reference_art_is_verified_to_be_an_image_before_it_is_stored():
    """save() wrote any decodable payload verbatim as <slug>.webp, so an HTML error page with an
    image content-type became permanent art that have() then reported as present."""
    src = _read("services/ref_portraits.py")
    fn = src[src.index("def save("):]
    fn = fn[:fn.index("\n\ndef ")]
    assert "Image.open" in fn and "verify()" in fn
    assert "return 0" in fn, "an unusable payload must not be stored"
    assert "len(blob) < 64" in fn, "a plain sanity floor for a decodable-but-empty payload"
    assert "width < 32 or height < 32" in fn, "a 1x1 pixel is not art"
