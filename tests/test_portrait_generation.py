"""Portrait generation: keyless provider + honest failures (Sept 2026).

The generator used to run as a background task against an OpenRouter key with a
$0.30 cap. When the cap was spent every portrait failed *silently*: the endpoint
returned 200 with an empty image_url and the wizard said "Generation queued…
Free tier ~50/day". These tests pin the replacement contract:

  - generation is inline, so the caller gets the real outcome,
  - the default provider needs no key at all,
  - a failed generation never deletes a portrait you already had,
  - the image is normalised (downscaled) on the way in, like every other writer.

No network: the provider call is monkeypatched.
"""

import base64
import io
import sqlite3

import pytest

import routes.characters.ai_routes as ai
from js_bundle import read_js


def png_data_url(w=1200, h=900, color=(90, 60, 30)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def make_char(seeded_db, name, user_id=1, **cols):
    cols.setdefault("race", "Human")
    cols.setdefault("class_name", "Fighter")
    cols.setdefault("level", 3)
    cols.setdefault("hp_current", 10)
    cols.setdefault("hp_max", 12)
    keys = list(cols)
    con = sqlite3.connect(str(seeded_db["db_path"]))
    con.execute(
        f"INSERT INTO characters (user_id, name{''.join(', ' + k for k in keys)}) "
        f"VALUES (?, ?{', ?' * len(keys)})",
        [user_id, name, *[cols[k] for k in keys]],
    )
    cid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.commit()
    con.close()
    return cid


def portrait_of(seeded_db, cid):
    con = sqlite3.connect(str(seeded_db["db_path"]))
    row = con.execute("SELECT portrait_url, portrait_prompt FROM characters WHERE id=?",
                      (cid,)).fetchone()
    con.close()
    return (row[0], row[1]) if row else (None, None)


@pytest.fixture
def fake_provider(monkeypatch):
    """Replace the provider with a recorder the test controls."""
    calls = {}

    async def _fake(prompt):
        calls["prompt"] = prompt
        return calls.get("result", (None, "provider unavailable"))

    monkeypatch.setattr(ai, "_generate_portrait_image", _fake)
    return calls


@pytest.fixture(autouse=True)
def _never_paint_on_the_live_gpu(monkeypatch):
    """Fail loudly instead of quietly reaching the running DM on 127.0.0.1:8700.

    The CM's face builder *is* the DM's now, so an unpatched route would queue a
    real GPU render — minutes per test, and a suite that silently depends on
    another service being up. Tests that want the DM stub it with fake_dm.
    """
    from services import dm_portraits

    async def _boom(*a, **kw):
        raise AssertionError("the route called the live DM — request fake_dm in this test")

    monkeypatch.setattr(dm_portraits, "paint", _boom)


@pytest.fixture
def fake_dm(monkeypatch):
    """Stand in for the DM's renderer — the one every CM face comes from."""
    from services import dm_portraits

    calls = {"payloads": [], "prompt": "cinematic 85mm photograph. A hero."}

    async def _fake(payload, prompt_only=False):
        calls["payloads"].append(dict(payload))
        calls["prompt_only"] = prompt_only
        if prompt_only:
            return None, calls["prompt"], None
        image, error = calls.get("result", (None, "the DM did not answer"))
        return image, calls["prompt"], error

    monkeypatch.setattr(dm_portraits, "paint", _fake)
    return calls


class TestKeylessProvider:
    def test_the_default_provider_is_keyless_and_free(self):
        """The default must need no key, no account and no card; paid paths stay opt-in.

        Pollinations held this role until it started answering instant 429s to everything,
        including a bare curl (0.4s, twice) while a bulk run had every request refused. Stable
        Horde replaces it on the same terms — the anonymous key is documented and needs no signup —
        so the invariant being protected is unchanged, only the host.
        """
        import inspect
        import os

        from services import portraits

        src = inspect.getsource(portraits)
        assert 'os.environ.get("PORTRAIT_PROVIDER", "horde")' in src
        assert "stablehorde.net/api/v2" in src
        assert "image.pollinations.ai" in src, "pollinations is still the fallback"
        assert 'if PORTRAIT_PROVIDER == "openrouter"' in src
        # The PROVIDER follows the environment; the CODE default is the keyless public queue, and
        # pointing a deployment at the local GPU (PORTRAIT_PROVIDER=comfy) is a deployment choice.
        # This used to assert the ambient value == "horde", which turned a deliberate deployment
        # decision into a red suite.
        assert portraits.PORTRAIT_PROVIDER == (
            os.environ.get("PORTRAIT_PROVIDER", "horde").strip().lower())
        assert 'os.environ.get("PORTRAIT_PROVIDER", "horde")' in src, \
            "the code default must stay the keyless queue"

    def test_the_horde_default_carries_the_documented_anonymous_key(self):
        """No signup, no account: the anonymous key is what keeps this provider free."""
        import inspect

        from services import portraits

        src = inspect.getsource(portraits.fetch_horde_image)
        assert '"0000000000"' in src
        assert "STABLEHORDE_API_KEY" in src, "a registered key must be honoured without other changes"

    def test_route_aliases_the_service_instead_of_carrying_a_copy(self):
        """One builder, one provider — the batch script and the web path share them."""
        import inspect
        import re

        src = inspect.getsource(ai)
        assert "_fallback_portrait_prompt = _portraits.portrait_prompt" in src
        assert "_generate_portrait_image = _portraits.generate_portrait_image" in src
        for name in ("_fallback_portrait_prompt", "_fetch_pollinations_image",
                     "_generate_portrait_image", "_fetch_openrouter_image"):
            assert not re.search(rf"^(?:async )?def {name}\(", src, re.M), \
                f"{name} is defined twice — the service copy is the only one"

    def test_request_url_carries_no_credentials(self, monkeypatch):
        """Pollinations is keyless — no api_key, no Authorization header."""
        import asyncio
        import httpx

        seen = {}

        class _Resp:
            status_code = 200
            headers = {"content-type": "image/png"}
            content = b"\x89PNG\r\n\x1a\n" + b"0" * 64

        class _Client:
            def __init__(self, **kw): seen["init"] = kw
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url, **kw):
                seen["url"] = url
                return _Resp()

        monkeypatch.setattr(httpx, "AsyncClient", _Client)
        data_url, err = asyncio.run(ai._fetch_pollinations_image("a dwarven cleric"))
        assert err is None and data_url.startswith("data:image/png;base64,")
        assert seen["url"].startswith("https://image.pollinations.ai/prompt/")
        assert "key" not in seen["url"].lower() and "token" not in seen["url"].lower()
        assert "nologo=true" in seen["url"] and "seed=" in seen["url"]

    def test_rate_limit_is_reported_not_swallowed(self, monkeypatch):
        import asyncio
        import httpx

        class _Resp:
            status_code = 429
            headers = {"content-type": "text/plain"}
            content = b"slow down"

        class _Client:
            def __init__(self, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url, **kw): return _Resp()

        monkeypatch.setattr(httpx, "AsyncClient", _Client)
        data_url, err = asyncio.run(ai._fetch_pollinations_image("x"))
        assert data_url is None and "rate limiting" in err


class TestHonestFailure:
    def test_failure_reports_the_reason(self, client, seeded_db, auth_headers, fake_dm):
        fake_dm["result"] = (None, "image service unreachable (ConnectError)")
        r = client.post("/api/ai/portrait", headers=auth_headers,
                        json={"name": "Dain", "race": "Dwarf", "class_name": "Cleric"})
        assert r.status_code == 200
        body = r.json()
        assert body["image_url"] == ""
        assert "unreachable" in body["error"], "the client must learn why there is no image"
        assert body["provider"] == "dm", "faces come from the DM's renderer, not a CM copy"
        assert body["prompt"], "the prompt is still returned so the DM can reuse it"

    def test_failure_keeps_an_existing_portrait(self, client, seeded_db, auth_headers, fake_dm):
        cid = make_char(seeded_db, "Keep My Face", portrait_url=png_data_url(200, 200))
        before, _ = portrait_of(seeded_db, cid)
        fake_dm["result"] = (None, "nope")
        r = client.post("/api/ai/portrait", headers=auth_headers,
                        json={"race": "Elf", "class_name": "Ranger", "character_id": cid})
        assert r.status_code == 200
        after, prompt = portrait_of(seeded_db, cid)
        assert after == before, "a failed generation must not wipe the stored portrait"
        assert prompt, "the new prompt is still recorded"

    def test_success_stores_a_normalised_portrait(self, client, seeded_db, auth_headers, fake_dm):
        cid = make_char(seeded_db, "Fresh Face")
        fake_dm["result"] = (png_data_url(2000, 2000), None)
        r = client.post("/api/ai/portrait", headers=auth_headers,
                        json={"race": "Human", "class_name": "Bard", "character_id": cid})
        assert r.status_code == 200 and r.json()["image_url"]
        stored, _ = portrait_of(seeded_db, cid)
        assert stored.startswith("data:image/webp"), "stored image must be shrunk like every writer"
        assert len(stored) < 800 * 1024

    def test_scope_is_enforced(self, client, seeded_db, auth_headers, fake_dm):
        """character_id must belong to the caller, or nothing is written."""
        other = make_char(seeded_db, "Not Yours", user_id=2)
        fake_dm["result"] = (png_data_url(300, 300), None)
        client.post("/api/ai/portrait", headers=auth_headers,
                    json={"race": "Human", "class_name": "Bard", "character_id": other})
        stored, _ = portrait_of(seeded_db, other)
        assert not stored

    def test_wizard_no_longer_claims_a_free_daily_quota(self):
        """The wizard's portrait-failure copy: no invented "free daily quota", no "queued"
        promise, and the provider's real error shown. The wizard JS is a static asset now, so
        read it there — and the template too, which only matches if someone re-inlines it."""
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        js = "\n".join(p.read_text() for p in sorted((root / "static").glob("create-*.js")))
        html = (root / "templates" / "create.html").read_text()
        for source in (js, html):
            assert "Free tier ~50/day" not in source
            assert "Generation queued" not in source
        assert "data.error" in js, "the wizard shows the real reason"


class TestCreateWizardWriterIsUnified:
    def test_create_rejects_a_bad_portrait(self, client, seeded_db, auth_headers):
        r = client.post("/api/character/create", headers=auth_headers, json={
            "name": "Bad Art", "race": "Human", "class_name": "Fighter", "level": 1,
            "abilities": {"strength": 10, "dexterity": 10, "constitution": 10,
                          "intelligence": 10, "wisdom": 10, "charisma": 10},
            "portrait_url": "javascript:alert(1)",
        })
        assert r.status_code == 400 and "error" in r.json()

    def test_create_stores_a_downscaled_portrait(self, client, seeded_db, auth_headers):
        r = client.post("/api/character/create", headers=auth_headers, json={
            "name": "Good Art", "race": "Human", "class_name": "Fighter", "level": 1,
            "abilities": {"strength": 10, "dexterity": 10, "constitution": 10,
                          "intelligence": 10, "wisdom": 10, "charisma": 10},
            "portrait_url": png_data_url(2400, 1800),
        })
        assert r.status_code == 200, r.text[:200]
        stored, _ = portrait_of(seeded_db, r.json()["id"])
        assert stored.startswith("data:image/webp") and len(stored) < 800 * 1024


class TestOneGeneratorForBothApps:
    """The CM's face comes from the DM's renderer — one prompt, one seed, one checkpoint.

    Two builders meant one hero owned two faces: the party card's portrait and the
    sheet's portrait disagreed. Every PC/NPC request now goes out as the identity
    the DM keys its cache on, so a face painted at the table is the face the sheet
    shows, and a cold render is the same face either app would have painted.
    """

    def test_genderize_names_the_subject_in_the_framing_clause(self):
        from services import portraits

        base = "Bust portrait, 3:4 aspect ratio. A dwarf barbarian."
        assert portraits.genderize(base, "female").startswith(
            "Bust portrait, 3:4 aspect ratio, female subject.")
        assert portraits.genderize(base, "") == base, "blank must not touch the prompt"
        assert portraits.genderize(base, "Halfling") == base, "never guess at gender"
        assert portraits.genderize("Frameless prompt.", "male").endswith("The subject is male.")

    def test_a_sheet_request_sends_identity_not_a_prompt(self, client, seeded_db, auth_headers, fake_dm):
        """The DM rebuilds the prompt from its own row; the CM only names the hero."""
        cid = make_char(seeded_db, "Keyed Hero", race="Dwarf", subrace="Hill", class_name="Barbarian")
        fake_dm["result"] = (png_data_url(), None)
        r = client.post("/api/ai/portrait", headers=auth_headers,
                        json={"race": "Elf", "class_name": "Wizard", "character_id": cid})
        assert r.status_code == 200, r.text[:200]
        payload = fake_dm["payloads"][-1]
        assert payload["source_char_id"] == cid
        assert payload["name"] == "Keyed Hero"
        assert payload["class_name"] == "Barbarian", "the stored sheet wins over the caller's copy"
        assert "prompt" not in payload, "no CM-authored prompt may travel"
        assert r.json()["prompt"] == fake_dm["prompt"], "the sheet shows the DM's words"

    def test_gender_no_longer_shapes_the_prompt(self, client, seeded_db, auth_headers, fake_dm):
        """Parity with the party card: the DM has no gender field, so the CM must not add one.

        A CM-only 'female subject' clause would change the DM's key inputs and hand
        one hero two faces — the exact bug this unification removes.
        """
        fake_dm["result"] = (png_data_url(), None)
        client.post("/api/ai/portrait", headers=auth_headers,
                    json={"name": "Sera", "race": "Dwarf", "class_name": "Barbarian",
                          "gender": "female"})
        payload = fake_dm["payloads"][-1]
        assert "gender" not in payload
        assert "female subject" not in str(payload)

    def test_npc_mode_forwards_the_npc_not_a_prompt(self, client, seeded_db, auth_headers, fake_dm):
        fake_dm["result"] = (None, "stop")
        r = client.post("/api/ai/portrait", headers=auth_headers,
                        json={"npc": {"name": "Dermot Wurder", "notes": "a nervous scribe",
                                      "role": "scribe"}})
        assert r.status_code == 200
        payload = fake_dm["payloads"][-1]
        assert payload == {"kind": "npc", "name": "Dermot Wurder", "race": "", "role": "scribe",
                           "notes": "a nervous scribe"}

    def test_prompt_only_previews_the_dm_prompt_without_rendering(self, client, seeded_db, auth_headers, fake_dm):
        cid = make_char(seeded_db, "Preview Hero")
        r = client.post("/api/ai/portrait", headers=auth_headers,
                        json={"character_id": cid, "prompt_only": True})
        assert r.status_code == 200
        body = r.json()
        assert body["prompt"] == fake_dm["prompt"] and body["image_url"] == ""
        assert fake_dm["prompt_only"] is True, "a preview must never queue a render"

    def test_a_hand_written_prompt_still_paints_locally(self, client, seeded_db, auth_headers,
                                                        fake_provider, fake_dm):
        """Free text has no home in the DM's identity-keyed builder, so that one case stays local."""
        fake_provider["result"] = (png_data_url(), None)
        r = client.post("/api/ai/portrait", headers=auth_headers,
                        json={"race": "Human", "class_name": "Fighter",
                              "custom_prompt": "a scarred duelist in a red cloak"})
        assert r.status_code == 200
        assert "a scarred duelist in a red cloak" in fake_provider["prompt"]
        assert r.json()["provider"] == ai.PORTRAIT_PROVIDER
        assert not fake_dm["payloads"], "the DM must not be asked to paint free text"

    def test_generation_claims_interactive_priority(self, client, seeded_db, auth_headers, monkeypatch):
        """The backfill must see a user's generation as in-flight, then released."""
        from services import dm_portraits, ref_portraits

        seen = {}

        async def _fake(payload, prompt_only=False):
            seen["during"] = ref_portraits.interactive_active()
            return (None, "the DM prompt", "stop here")

        monkeypatch.setattr(dm_portraits, "paint", _fake)
        assert not ref_portraits.interactive_active()
        client.post("/api/ai/portrait", headers=auth_headers,
                    json={"name": "Duellist", "race": "Human", "class_name": "Fighter"})
        assert seen.get("during") is True
        assert not ref_portraits.interactive_active(), "the claim must always be released"

    def test_backfill_yields_while_a_user_generation_runs(self):
        import asyncio
        import importlib.util
        import pathlib
        import time as _time

        from services import ref_portraits

        spec = importlib.util.spec_from_file_location(
            "gen_portraits", pathlib.Path("scripts/generate_portraits.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        ref_portraits.mark_interactive()
        started = _time.time()
        try:
            async def _run():
                return await mod._yield_to_interactive()

            async def _release():
                await asyncio.sleep(0.4)
                ref_portraits.clear_interactive()

            async def _both():
                return await asyncio.gather(_run(), _release())

            yielded, _ = asyncio.run(_both())
            assert yielded is True, "the batch should report that it stood down"
            assert _time.time() - started >= 0.4, "and it should actually have waited"
        finally:
            ref_portraits.clear_interactive()

    def test_backfill_does_not_wait_when_nobody_is_generating(self):
        import asyncio
        import importlib.util
        import pathlib
        import time as _time

        from services import ref_portraits

        spec = importlib.util.spec_from_file_location(
            "gen_portraits2", pathlib.Path("scripts/generate_portraits.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ref_portraits.clear_interactive()
        started = _time.time()
        assert asyncio.run(mod._yield_to_interactive()) is False
        assert _time.time() - started < 0.2

    def test_npc_editor_offers_generation(self):
        src = read_js("dm_tools.js")
        assert "npcPortraitGenerate()" in src, "the NPC editor needs a Generate button"
        assert "npc: {name: name" in src, "and it must use the endpoint's npc mode"
        assert "data.image_url" in src
