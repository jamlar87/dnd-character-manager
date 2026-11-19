"""The AI tier chain's own contract: order, fall-through, and what counts as an answer.

Before this file the chain had NO test of its ordering, its failure path, or its JSON contract —
the one 'AI test' monkeypatched `_call_ai` away — so every failure degraded to a placeholder that
looked like a successful generation (measured 0/5 usable, Oct 2026).
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from routes.characters import ai_routes  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_cooldowns():
    """The cool-down map is module state: a test that leaves a model cooling would fail the next
    test for the wrong reason."""
    ai_routes._MODEL_COOLDOWN.clear()
    yield
    ai_routes._MODEL_COOLDOWN.clear()


# ── _extract_json: dict or nothing ───────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ("```\n{\"a\": 1}\n```", {"a": 1}),                      # fence with no language tag
    ('Here is the JSON: {"a": 1}', {"a": 1}),               # prose-wrapped (free models do this)
    ('Sure!\n```json\n{"a": 1}\n```\nHope that helps.', {"a": 1}),
    ('{"a": {"nested": "}"}, "b": 2}', {"a": {"nested": "}"}, "b": 2}),  # brace inside a string
    ('{"a": 1} and then some trailing prose', {"a": 1}),
])
def test_a_json_object_is_found_however_the_model_wrapped_it(text, expected):
    assert ai_routes._extract_json(text) == expected


@pytest.mark.parametrize("text", [
    "[1,2,3]",            # a bare array: callers do ai.get(...) -> AttributeError -> 500
    "true",
    "42",
    '"just a string"',
    "Thinking Process:\n1. Analyze the request\n2. Output the JSON",
    "",
    None,
])
def test_anything_that_is_not_an_object_is_none(text):
    """The type contract matters: every caller immediately calls .get on the result."""
    assert ai_routes._extract_json(text) is None


# ── the chain: order, fall-through, and the contract check ────────────────────────────────

JSON_PROMPT = 'Return ONLY valid JSON: {"name": "...", "personality": "..."}'
PROSE_PROMPT = "Return ONLY the backstory text — no JSON, no labels, no preamble."


def _stub(monkeypatch, openrouter=None, gemini=None, ollama=None, record=None):
    """Replace the three tiers with fakes that record their calls."""
    calls = record if record is not None else []

    def make(name, value):
        async def fake(prompt):
            calls.append(name)
            if isinstance(value, Exception):
                raise value
            return value
        return fake

    monkeypatch.setattr(ai_routes, "_call_openrouter", make("openrouter", openrouter))
    monkeypatch.setattr(ai_routes, "_call_gemini", make("gemini", gemini))
    monkeypatch.setattr(ai_routes, "_call_ollama", make("ollama", ollama))
    return calls


def test_a_chain_of_thought_echo_falls_through_instead_of_ending_the_chain(monkeypatch):
    """The bug this file exists for: the CoT reply CONTAINS a brace, so the old guard accepted
    it, the chain stopped at tier 1, and the caller got its placeholder."""
    cot = 'The user asks: "Return ONLY valid JSON..." Here is my plan: {"name": ...}'
    calls = _stub(monkeypatch, openrouter=cot, gemini='{"name": "Real", "personality": "ok"}')
    out = asyncio.run(ai_routes._call_ai(JSON_PROMPT, label="test"))
    assert out == '{"name": "Real", "personality": "ok"}'
    assert calls == ["openrouter", "gemini"], "tier 1's answer must be rejected, not returned"


def test_a_safety_classifiers_answer_falls_through(monkeypatch):
    """`openrouter/free` routed a request to nvidia/nemotron-3.5-content-safety, which replied
    "User Safety: safe" — a 200 with no object in it."""
    calls = _stub(monkeypatch, openrouter="User Safety: safe",
                  gemini='{"name": "Real", "personality": "ok"}')
    assert asyncio.run(ai_routes._call_ai(JSON_PROMPT)) is not None
    assert calls == ["openrouter", "gemini"]


def test_an_empty_answer_falls_through(monkeypatch):
    """A reasoning model that spends the whole budget thinking returns EMPTY content."""
    calls = _stub(monkeypatch, openrouter="", gemini="", ollama='{"name": "Local"}')
    assert asyncio.run(ai_routes._call_ai(JSON_PROMPT)) == '{"name": "Local"}'
    assert calls == ["openrouter", "gemini", "ollama"]


def test_a_raising_tier_never_breaks_the_chain(monkeypatch):
    calls = _stub(monkeypatch, openrouter=RuntimeError("provider exploded"),
                  gemini='{"name": "Real"}')
    assert asyncio.run(ai_routes._call_ai(JSON_PROMPT)) == '{"name": "Real"}'
    assert calls == ["openrouter", "gemini"]


def test_the_tiers_are_tried_in_order_and_the_first_usable_answer_wins(monkeypatch):
    calls = _stub(monkeypatch, openrouter='{"name": "One"}', gemini='{"name": "Two"}',
                  ollama='{"name": "Three"}')
    assert asyncio.run(ai_routes._call_ai(JSON_PROMPT)) == '{"name": "One"}'
    assert calls == ["openrouter"], "a usable tier-1 answer must not spend tier 2"


def test_all_tiers_down_returns_none(monkeypatch):
    """None is the signal every caller's fallback keys on; "" would look like an answer."""
    calls = _stub(monkeypatch, openrouter="", gemini=None, ollama=None)
    assert asyncio.run(ai_routes._call_ai(JSON_PROMPT)) is None
    assert calls == ["openrouter", "gemini", "ollama"]


def test_a_prose_prompt_accepts_prose(monkeypatch):
    """Two callers want narrative text, not JSON (history, manual summary). The old guard only
    rejected CoT when the prompt contained a brace, so those two were never checked at all."""
    prose = "Born in the salt marshes of the Sword Coast, she learned to read the tide."
    _stub(monkeypatch, openrouter=prose)
    assert asyncio.run(ai_routes._call_ai(PROSE_PROMPT)) == prose


def test_json_expected_can_be_stated_explicitly(monkeypatch):
    """A prompt whose text carries no brace but whose caller parses JSON."""
    calls = _stub(monkeypatch, openrouter="no object here", gemini='{"name": "Real"}')
    out = asyncio.run(ai_routes._call_ai("Design an NPC please.", expect_json=True))
    assert out == '{"name": "Real"}' and calls == ["openrouter", "gemini"]


def test_a_hanging_tier_is_abandoned_and_the_next_one_runs(monkeypatch):
    async def hangs(prompt):
        await asyncio.sleep(30)
        return '{"name": "TooLate"}'

    async def quick(prompt):
        return '{"name": "Quick"}'

    monkeypatch.setattr(ai_routes, "AI_TIER_TIMEOUT", 0.15)
    monkeypatch.setattr(ai_routes, "_call_openrouter", hangs)
    monkeypatch.setattr(ai_routes, "_call_gemini", quick)
    monkeypatch.setattr(ai_routes, "_call_ollama", quick)
    t0 = asyncio.get_event_loop_policy().new_event_loop().time
    out = asyncio.run(ai_routes._call_ai(JSON_PROMPT))
    assert out == '{"name": "Quick"}', "a hung tier must not hold the request open"


# ── the provider tiers: what they send, and what they report ──────────────────────────────

def test_openrouter_sends_a_live_model_and_a_workable_token_cap(monkeypatch):
    """The pin was retired under us: poolside/laguna-xs-2.1:free answers 404 'no longer available
    on OpenRouter' on EVERY call, and every free model still listed is a reasoning model that spent
    the whole 1400-token cap thinking (measured 2/2 empty each, Oct 2026). A dead id in the default
    is a wasted hop per AI call, so the default must not name one."""
    seen = {}

    async def fake_post(url, payload, headers=None, timeout=20.0):
        seen["url"] = url
        seen["model"] = payload["model"]
        seen["max_tokens"] = payload["max_tokens"]
        return 200, {"choices": [{"message": {"content": '{"name": "x"}'},
                                  "finish_reason": "stop"}],
                     "usage": {"completion_tokens": 12}}, ""

    monkeypatch.setattr(ai_routes, "_post_json", fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    out = asyncio.run(ai_routes._call_openrouter(JSON_PROMPT))
    assert out == '{"name": "x"}'
    assert seen["model"] == ai_routes.OPENROUTER_MODELS[0]
    assert "laguna" not in str(ai_routes.OPENROUTER_MODELS), "the retired pin must not be a default"
    assert seen["max_tokens"] == ai_routes.AI_MAX_TOKENS and seen["max_tokens"] >= 1024


def test_a_retired_model_is_skipped_for_the_long_window_instead_of_every_call(monkeypatch):
    """Measured: the retired pin 404'd on every trap and encounter generation. A 404 'no longer
    available' is a config fact, so one hop per 6h, not one hop per AI call."""
    calls = []

    async def fake_post(url, payload, headers=None, timeout=20.0):
        calls.append(payload["model"])
        return 404, {"error": {"message": "poolside/laguna-xs-2.1:free is no longer available on "
                                           "OpenRouter: no provider serves this model anymore."}}, "404"

    monkeypatch.setattr(ai_routes, "_post_json", fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(ai_routes, "OPENROUTER_MODELS", ["retired:free", "alive:free"])
    monkeypatch.setattr(ai_routes, "AI_MODEL_COOLDOWN_MAX", 6 * 3600)

    assert asyncio.run(ai_routes._call_openrouter(JSON_PROMPT)) is None
    assert calls == ["retired:free", "alive:free"], "a 404 must not end the tier"
    assert ai_routes._cooling("openrouter/retired:free") > 5 * 3600, "not the 5-minute window"

    calls.clear()
    asyncio.run(ai_routes._call_openrouter(JSON_PROMPT))
    assert calls == ["alive:free"] or calls == [], "the retired id must not cost a hop again"


def test_the_router_alias_gets_one_retry_before_the_next_tier(monkeypatch):
    """`openrouter/free` re-rolls the underlying model and answered the 16-key trap 1 of 2 tries;
    its two neighbours (gemini key invalid, ollama box off) are dead, so a retry is the cheapest
    remaining chance at an answer."""
    calls = []

    async def fake_post(url, payload, headers=None, timeout=20.0):
        calls.append(payload["model"])
        if len(calls) == 1:
            return 200, {"choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                         "usage": {"completion_tokens": 1400,
                                   "completion_tokens_details": {"reasoning_tokens": 1400}}}, ""
        return 200, {"choices": [{"message": {"content": '{"name": "Retry"}'},
                                  "finish_reason": "stop"}], "usage": {}}, ""

    monkeypatch.setattr(ai_routes, "_post_json", fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(ai_routes, "OPENROUTER_MODELS", ["openrouter/free"])
    assert ai_routes.OPENROUTER_ROUTER_ALIASES >= {"openrouter/free"}
    assert asyncio.run(ai_routes._call_openrouter(JSON_PROMPT)) == '{"name": "Retry"}'
    assert len(calls) == 2


def test_a_pinned_model_is_not_retried_on_empty_content(monkeypatch):
    """A pin would spend the same budget on the same model and come back empty again."""
    calls = []

    async def fake_post(url, payload, headers=None, timeout=20.0):
        calls.append(payload["model"])
        return 200, {"choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                     "usage": {}}, ""

    monkeypatch.setattr(ai_routes, "_post_json", fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(ai_routes, "OPENROUTER_MODELS", ["pinned:free"])
    assert asyncio.run(ai_routes._call_openrouter(JSON_PROMPT)) is None
    assert calls == ["pinned:free"]


def test_an_openrouter_truncation_or_empty_answer_moves_to_the_next_model(monkeypatch):
    """finish_reason='length' with empty content is a reasoning model out of budget."""
    calls = []

    async def fake_post(url, payload, headers=None, timeout=20.0):
        calls.append(payload["model"])
        if len(calls) == 1:
            return 200, {"choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                         "usage": {"completion_tokens": 1400,
                                   "completion_tokens_details": {"reasoning_tokens": 1400}}}, ""
        return 200, {"choices": [{"message": {"content": '{"name": "Second"}'},
                                  "finish_reason": "stop"}], "usage": {}}, ""

    monkeypatch.setattr(ai_routes, "_post_json", fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(ai_routes, "OPENROUTER_MODELS", ["model-a:free", "model-b:free"])
    assert asyncio.run(ai_routes._call_openrouter(JSON_PROMPT)) == '{"name": "Second"}'
    assert len(calls) == 2, "an unusable first model must not end the tier"


def test_gemini_walks_its_models_because_the_quota_is_per_model(monkeypatch):
    """Measured: gemini-3.6-flash 429 ("limit: 20 ... retry in 23h"), gemini-2.5-flash 200 on the
    SAME key. A single hardcoded model made the whole tier depend on one model's daily cap."""
    tried, logged = [], []

    async def fake_post(url, payload, headers=None, timeout=20.0):
        model = url.split("/models/")[1].split(":")[0]
        tried.append(model)
        if model == ai_routes.GEMINI_MODELS[0]:
            return 429, {"error": {"code": 429, "message": "Quota exceeded ... limit: 20"}}, "429 body"
        return 200, {"candidates": [{"content": {"parts": [{"text": '{"name": "Gem"}'}]}}]}, ""

    monkeypatch.setattr(ai_routes, "_post_json", fake_post)
    monkeypatch.setattr("builtins.print", lambda *a, **k: logged.append(" ".join(map(str, a))))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    assert asyncio.run(ai_routes._call_gemini(JSON_PROMPT)) == '{"name": "Gem"}'
    assert tried == list(ai_routes.GEMINI_MODELS[:2])
    assert any("429" in line and "Quota exceeded" in line for line in logged), \
        "the provider's own message must reach the log, not a KeyError('candidates')"


def test_a_missing_key_skips_its_tier_without_a_call(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("no HTTP call may happen without a key")

    monkeypatch.setattr(ai_routes, "_post_json", boom)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert asyncio.run(ai_routes._call_openrouter(JSON_PROMPT)) is None
    assert asyncio.run(ai_routes._call_gemini(JSON_PROMPT)) is None


def test_the_chain_has_a_budget_and_the_local_tier_cannot_hold_it_open(monkeypatch):
    """The budgets are a policy, not free numbers.

    History: the Ollama tier allowed 120s while the local model was llama3.1:8b, so a
    reachable-but-hung box blocked ~160s across tiers — the caps came down to 25/70. The tier is
    now gemma4-26b-a4b, which needs room for ~2k hidden reasoning tokens (measured 2-8s for a
    short JSON answer, ~60s worst case at 4000 tokens), so the caps went back up. What must stay
    true: one tier can always finish, the whole chain stays inside a bounded page wait, and the
    local model keeps a slice of the budget the cloud tiers cannot spend.
    """
    assert 60 <= ai_routes.AI_TIER_TIMEOUT <= 120, "local gemma4 needs >=60s per call"
    assert ai_routes.AI_TIER_TIMEOUT <= ai_routes.AI_CHAIN_BUDGET <= 240, "bounded page wait"
    assert 0 < ai_routes.AI_LOCAL_RESERVE < ai_routes.AI_CHAIN_BUDGET
    assert "192.168.1.31" not in ai_routes.OLLAMA_URL or "OLLAMA_URL" in Path(
        ai_routes.__file__).read_text()


def test_two_hung_cloud_tiers_cannot_starve_the_local_one(monkeypatch):
    """The failure this guards: openrouter and gemini both hang to their cap, the budget drains to
    zero, and the tier that actually answers is skipped as 'budget spent' — the user gets a
    placeholder while a perfectly good local model sat idle."""
    called = {"openrouter": 0, "gemini": 0, "ollama": 0}

    def hung(name):
        async def _fn(prompt):
            called[name] += 1
            await asyncio.sleep(3)          # outlives the tier cap
            return None
        return _fn

    async def local(prompt):
        called["ollama"] += 1
        return '{"personality": "stoic"}'

    monkeypatch.setattr(ai_routes, "_call_openrouter", hung("openrouter"))
    monkeypatch.setattr(ai_routes, "_call_gemini", hung("gemini"))
    monkeypatch.setattr(ai_routes, "_call_ollama", local)
    # Scaled-down copy of the real policy (240/120/75): 6s chain, 1.5s per tier, 2s held back.
    # Both cloud tiers hang to their cap and the local tier still runs. Without the reserve the
    # second cloud tier would consume the last of the budget and never be interrupted.
    monkeypatch.setattr(ai_routes, "AI_CHAIN_BUDGET", 6.0)
    monkeypatch.setattr(ai_routes, "AI_TIER_TIMEOUT", 1.5)
    monkeypatch.setattr(ai_routes, "AI_LOCAL_RESERVE", 2.0)

    out = asyncio.run(ai_routes._call_ai('Return JSON: {"personality": "..."}', label="test"))

    assert called["openrouter"] == 1 and called["gemini"] == 1
    assert called["ollama"] == 1, "the local tier must still get its turn"
    assert out and "stoic" in out


# ── a capped model is skipped instead of costing a hop on every call ─────────────────────

def test_a_429_stops_that_model_being_tried_on_the_next_call(monkeypatch):
    """Measured cost without this: ~4s per call, on three consecutive live runs, while the pinned
    model was rate-limited."""
    calls = []

    async def fake_post(url, payload, headers=None, timeout=20.0):
        calls.append(payload["model"])
        return 429, {"error": {"message": "Provider returned error"}}, "429"

    monkeypatch.setattr(ai_routes, "_post_json", fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(ai_routes, "OPENROUTER_MODELS", ["model-a:free", "model-b:free"])

    assert asyncio.run(ai_routes._call_openrouter(JSON_PROMPT)) is None
    assert calls == ["model-a:free", "model-b:free"]

    calls.clear()
    asyncio.run(ai_routes._call_openrouter(JSON_PROMPT))
    # Both models returned 429, so both are cooling and the tier spends NOTHING on the retry —
    # which is the whole saving.
    assert calls == [], "both were cooling: neither should be called again"
    assert ai_routes._cooling("openrouter/model-a:free") > 0
    assert ai_routes._cooling("openrouter/model-b:free") > 0


def test_the_providers_own_retry_hint_sets_the_window(monkeypatch):
    """Gemini says 'retry in 23h7m44.2s'; a blind 5 minutes would call it again all day."""
    hints = [
        ("Quota exceeded ... Please retry in 23h7m44.238006659s.", 23 * 3600 + 7 * 60 + 44.2),
        ("Please retry in 45s.", 45),
        ("retry in 12m", 720),
        ("no hint here", None),
        ("", None),
    ]
    for message, expected in hints:
        got = ai_routes._retry_hint_seconds(message)
        if expected is None:
            assert got is None, message
        else:
            assert got == pytest.approx(expected, abs=1), (message, got)

    monkeypatch.setattr(ai_routes, "AI_MODEL_COOLDOWN_MAX", 6 * 3600)
    ai_routes._cool_down("gemini/gemini-3.6-flash", "quota ... retry in 23h7m44s")
    left = ai_routes._cooling("gemini/gemini-3.6-flash")
    assert 5.9 * 3600 < left <= 6 * 3600, f"the hint is capped, got {left}"


def test_a_blip_does_not_cool_a_model_down(monkeypatch):
    """A 500 is transient; only a rate limit says something about this model today."""
    async def fake_post(url, payload, headers=None, timeout=20.0):
        return 500, {"error": {"message": "upstream hiccup"}}, "500"

    monkeypatch.setattr(ai_routes, "_post_json", fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    asyncio.run(ai_routes._call_openrouter(JSON_PROMPT))
    assert all(_zero(ai_routes._cooling(k)) for k in list(ai_routes._MODEL_COOLDOWN) or
               [f"openrouter/{m}" for m in ai_routes.OPENROUTER_MODELS]), "500 must not cool down"


def _zero(seconds):
    return seconds == 0


def test_a_cool_down_expires(monkeypatch):
    monkeypatch.setattr(ai_routes, "AI_MODEL_COOLDOWN", 30)
    ai_routes._cool_down("openrouter/x:free", "")
    assert ai_routes._cooling("openrouter/x:free") > 0
    ai_routes._MODEL_COOLDOWN["openrouter/x:free"] = 0            # as if the window elapsed
    assert ai_routes._cooling("openrouter/x:free") == 0
    assert "openrouter/x:free" not in ai_routes._MODEL_COOLDOWN, "an expired entry is dropped"


def test_the_chain_still_reaches_a_healthy_tier_past_cooling_models(monkeypatch):
    """The point of the whole feature: a capped model must not block the answer."""
    # Only the first tier is capped; the second must answer without the first being retried.
    ai_routes._cool_down("openrouter/model-a:free", "429")
    monkeypatch.setattr(ai_routes, "OPENROUTER_MODELS", ["model-a:free"])
    monkeypatch.setattr(ai_routes, "GEMINI_MODELS", ["gemini-3.6-flash", "gemini-2.5-flash"])

    async def fake_post(url, payload, headers=None, timeout=20.0):
        if "generativelanguage" in url:
            return 200, {"candidates": [{"content": {"parts": [{"text": '{"name": "Backup"}'}]}}]}, ""
        raise AssertionError("a cooling model must not be called")

    monkeypatch.setattr(ai_routes, "_post_json", fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    assert asyncio.run(ai_routes._call_ai(JSON_PROMPT)) == '{"name": "Backup"}'
