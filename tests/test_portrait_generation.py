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
        if not os.environ.get("PORTRAIT_PROVIDER"):
            assert portraits.PORTRAIT_PROVIDER == "horde", "the paid path must be opt-in"

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
    def test_failure_reports_the_reason(self, client, seeded_db, auth_headers, fake_provider):
        fake_provider["result"] = (None, "image service unreachable (ConnectError)")
        r = client.post("/api/ai/portrait", headers=auth_headers,
                        json={"race": "Dwarf", "class_name": "Cleric"})
        assert r.status_code == 200
        body = r.json()
        assert body["image_url"] == ""
        assert "unreachable" in body["error"], "the client must learn why there is no image"
        assert body["provider"] == ai.PORTRAIT_PROVIDER
        assert body["prompt"], "the prompt is still returned so the DM can reuse it"

    def test_failure_keeps_an_existing_portrait(self, client, seeded_db, auth_headers, fake_provider):
        cid = make_char(seeded_db, "Keep My Face", portrait_url=png_data_url(200, 200))
        before, _ = portrait_of(seeded_db, cid)
        fake_provider["result"] = (None, "nope")
        r = client.post("/api/ai/portrait", headers=auth_headers,
                        json={"race": "Elf", "class_name": "Ranger", "character_id": cid})
        assert r.status_code == 200
        after, prompt = portrait_of(seeded_db, cid)
        assert after == before, "a failed generation must not wipe the stored portrait"
        assert prompt, "the new prompt is still recorded"

    def test_success_stores_a_normalised_portrait(self, client, seeded_db, auth_headers, fake_provider):
        cid = make_char(seeded_db, "Fresh Face")
        fake_provider["result"] = (png_data_url(2000, 2000), None)
        r = client.post("/api/ai/portrait", headers=auth_headers,
                        json={"race": "Human", "class_name": "Bard", "character_id": cid})
        assert r.status_code == 200 and r.json()["image_url"]
        stored, _ = portrait_of(seeded_db, cid)
        assert stored.startswith("data:image/webp"), "stored image must be shrunk like every writer"
        assert len(stored) < 800 * 1024

    def test_scope_is_enforced(self, client, seeded_db, auth_headers, fake_provider):
        """character_id must belong to the caller, or nothing is written."""
        other = make_char(seeded_db, "Not Yours", user_id=2)
        fake_provider["result"] = (png_data_url(300, 300), None)
        client.post("/api/ai/portrait", headers=auth_headers,
                    json={"race": "Human", "class_name": "Bard", "character_id": other})
        stored, _ = portrait_of(seeded_db, other)
        assert not stored

    def test_wizard_no_longer_claims_a_free_daily_quota(self):
        from pathlib import Path
        html = (Path(__file__).resolve().parent.parent / "templates" / "create.html").read_text()
        assert "Free tier ~50/day" not in html
        assert "Generation queued" not in html
        assert "data.error" in html, "the wizard shows the real reason"


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


class TestGenderAndNpcMode:
    """Three gaps found while auditing the creation wizard.

    gender was read by the endpoint and then never used (so a female character
    could come back bearded); the DM's NPC editor had upload but no way to
    generate; and the wizard's generation raced the reference-art backfill for
    the same free quota.
    """

    def test_genderize_names_the_subject_in_the_framing_clause(self):
        from services import portraits

        base = "Bust portrait, 3:4 aspect ratio. A dwarf barbarian."
        assert portraits.genderize(base, "female").startswith(
            "Bust portrait, 3:4 aspect ratio, female subject.")
        assert portraits.genderize(base, "") == base, "blank must not touch the prompt"
        assert portraits.genderize(base, "Halfling") == base, "never guess at gender"
        assert portraits.genderize("Frameless prompt.", "male").endswith("The subject is male.")

    def test_route_puts_gender_into_the_prompt(self, client, seeded_db, auth_headers, fake_provider):
        fake_provider["result"] = (png_data_url(), None)
        r = client.post("/api/ai/portrait", headers=auth_headers,
                        json={"race": "Dwarf", "class_name": "Barbarian", "gender": "female"})
        assert r.status_code == 200, r.text[:200]
        assert "female subject" in fake_provider["prompt"]
        assert "female subject" in r.json()["prompt"]

    def test_unknown_gender_leaves_the_prompt_alone(self, client, seeded_db, auth_headers, fake_provider):
        fake_provider["result"] = (None, "stop")
        client.post("/api/ai/portrait", headers=auth_headers,
                    json={"race": "Dwarf", "class_name": "Barbarian", "gender": ""})
        assert "subject." not in fake_provider["prompt"]

    def test_npc_mode_builds_the_prompt_from_the_npc(self, client, seeded_db, auth_headers, fake_provider):
        fake_provider["result"] = (None, "stop")
        r = client.post("/api/ai/portrait", headers=auth_headers,
                        json={"npc": {"name": "Dermot Wurder", "notes": "a nervous scribe",
                                      "role": "scribe"}})
        assert r.status_code == 200
        prompt = fake_provider["prompt"]
        assert "Dermot Wurder" in prompt and "nervous scribe" in prompt
        assert "upper body only, close-up composition" in prompt, "should be npc_prompt, not the class table"

    def test_generation_claims_interactive_priority(self, client, seeded_db, auth_headers, monkeypatch):
        """The backfill must see a user's generation as in-flight, then released."""
        from services import ref_portraits

        seen = {}

        async def _fake(prompt):
            seen["during"] = ref_portraits.interactive_active()
            return (None, "stop here")

        monkeypatch.setattr(ai, "_generate_portrait_image", _fake)
        assert not ref_portraits.interactive_active()
        client.post("/api/ai/portrait", headers=auth_headers,
                    json={"race": "Human", "class_name": "Fighter"})
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
        import pathlib

        src = pathlib.Path("static/dm_tools.js").read_text()
        assert "npcPortraitGenerate()" in src, "the NPC editor needs a Generate button"
        assert "npc: {name: name" in src, "and it must use the endpoint's npc mode"
        assert "data.image_url" in src
