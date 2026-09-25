"""Reference art (monsters / items / NPCs): file store, lazy generation, route.

3,909 shared images cannot live in a DB column or in the HTML, so they are files
under static/ref-portraits/<kind>/ and file existence is the state. These tests
pin that contract:

  - a slug is deterministic and name-distinct (two names never collide),
  - writes are atomic and always end up WebP,
  - the route serves a file with edge-cache headers, and
  - when a file is missing the route kicks ONE background generation — but only
    for a name the index actually knows, so a typo can't cost a request.

No network and no pollution of the real static/ref-portraits directory.
"""

import base64
import io

import pytest
import routes.ref_images as ref_route
from services import ref_portraits


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Point the store at a temp dir so tests never touch the real library."""
    monkeypatch.setattr(ref_portraits, "ROOT", tmp_path / "ref-portraits")
    return tmp_path / "ref-portraits"


def png_data_url(w=64, h=64, color=(120, 90, 40)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class TestSlugsAndStore:
    def test_slug_is_deterministic(self):
        assert ref_portraits.slug_for("Aboleth") == ref_portraits.slug_for("Aboleth")

    def test_different_names_do_not_collide(self):
        a = ref_portraits.slug_for("Bag of Holding (Type I)")
        b = ref_portraits.slug_for("Bag of Holding Type I")
        assert a != b, "punctuation-only differences must not share a file"
        assert ref_portraits.slug_for("Acid (vial)") != ref_portraits.slug_for("Acid vial")

    def test_slug_is_filename_safe(self):
        slug = ref_portraits.slug_for("Aazael, the Dread Knight / CR 6")
        assert slug == slug.lower()
        assert all(c.isalnum() or c == "-" for c in slug)
        assert "/" not in slug

    def test_save_then_have_then_read(self):
        assert ref_portraits.have("creature", "Aboleth") is False
        written = ref_portraits.save("creature", "Aboleth", png_data_url())
        assert written > 0
        assert ref_portraits.have("creature", "Aboleth") is True
        blob = ref_portraits.path_for("creature", "Aboleth").read_bytes()
        assert blob.startswith(b"RIFF"), "stored file must be WebP"
        assert not list(ref_portraits.path_for("creature", "Aboleth").parent.glob("*.tmp")), \
            "the temp file must be renamed away"

    def test_save_rejects_junk(self):
        assert ref_portraits.save("item", "Nonsense", "not-an-image") == 0
        assert ref_portraits.have("item", "Nonsense") is False

    def test_stats_counts_files(self):
        ref_portraits.save("item", "Abacus", png_data_url())
        ref_portraits.save("item", "Acid (vial)", png_data_url())
        assert ref_portraits.stats(["item"])["item"]["images"] == 2


class TestPrompts:
    def test_prompts_are_kind_specific(self):
        creature = ref_portraits.prompt_for("creature", "Aboleth", "CR 10 · aberration", "Enslave.")
        item = ref_portraits.prompt_for("item", "Bag of Holding", "Wondrous item")
        npc = ref_portraits.prompt_for("npc", "Ayo Jabe", "Water Genasi")
        assert "bestiary" in creature and "Aboleth" in creature
        # Plain items lead with "Fantasy object:" — "item illustration" was retired because "item"
        # points a diffusion model at a small hand-held object (see the note in ref_portraits).
        assert "Fantasy object" in item and "Bag of Holding" in item
        assert "Bust portrait" in npc, "NPCs reuse the character prompt language"
        assert len({creature, item, npc}) == 3

    def test_unknown_kind_is_refused(self):
        assert ref_portraits.prompt_for("spellbook", "Fireball") is not None   # falls back to npc
        with pytest.raises(KeyError):
            ref_portraits.KINDS["spellbook"]


class TestRoute:
    def test_serves_a_type(self, client, auth_headers):
        ref_portraits.save("creature", "Aboleth", png_data_url(400, 500))
        full = client.get("/api/ref-image/creature/Aboleth", headers=auth_headers)
        assert full.status_code == 200 and full.headers["content-type"] == "image/webp"
        assert full.headers["x-ref-image"] == "ready"
        assert "public" in full.headers["cache-control"], "shared art must be edge-cacheable"
        thumb = client.get("/api/ref-image/creature/Aboleth?size=80", headers=auth_headers)
        assert thumb.status_code == 200 and len(thumb.content) < len(full.content)

    def test_missing_file_kicks_one_generation(self, client, auth_headers, monkeypatch):
        calls = []
        monkeypatch.setattr(ref_portraits, "kick",
                            lambda *a, **k: (calls.append(a), True)[1])
        r = client.get("/api/ref-image/creature/Aboleth", headers=auth_headers)
        assert r.status_code == 404
        assert r.headers["x-ref-image"] == "generating"
        assert r.headers["cache-control"] == "no-store", "a 404 must not be cached"

    def test_unknown_entity_does_not_kick(self, client, auth_headers, monkeypatch):
        """A typo or a probe name must not spend a free-tier request."""
        calls = []
        monkeypatch.setattr(ref_portraits, "kick",
                            lambda *a, **k: (calls.append(a), True)[1])
        r = client.get("/api/ref-image/creature/Definitely Not A Monster", headers=auth_headers)
        assert r.status_code == 404
        assert r.headers["x-ref-image"] == "unknown"
        assert calls == [], "unknown names must not be generated"

    def test_unknown_kind_is_404(self, client, auth_headers):
        assert client.get("/api/ref-image/spellbook/Fireball", headers=auth_headers).status_code == 404

    def test_anonymous_cannot_read_the_library(self, client):
        """A cookie-less request is redirected: this route can spend generations,
        so it must never be an open image-generation proxy."""
        saved = dict(client.cookies)
        client.cookies.clear()
        try:
            r = client.get("/api/ref-image/creature/Aboleth", follow_redirects=False)
            assert r.status_code == 303 and r.headers.get("location") == "/login"
        finally:
            client.cookies.update(saved)

    def test_stats_endpoint(self, client, auth_headers):
        ref_portraits.save("npc", "Ayo Jabe", png_data_url())
        body = client.get("/api/ref-image-stats", headers=auth_headers).json()
        assert body["npc"]["images"] == 1 and "creature" in body


class TestGeneration:
    def test_existing_image_short_circuits_the_provider(self, monkeypatch):
        import asyncio
        ref_portraits.save("item", "Abacus", png_data_url())

        async def _boom(*a, **k):
            raise AssertionError("provider must not be called when the file exists")

        monkeypatch.setattr("services.portraits.generate_portrait_image", _boom)
        path, err = asyncio.run(ref_portraits.generate("item", "Abacus"))
        assert err is None and path.is_file()

    def test_second_caller_does_not_duplicate_work(self, monkeypatch):
        """Two requests for the same missing image start one generation."""
        import asyncio
        started = []

        async def _slow(prompt, max_wait=90, width=768, height=1024):
            started.append(prompt)
            await asyncio.sleep(0.2)
            return png_data_url(), None

        monkeypatch.setattr("services.portraits.generate_portrait_image", _slow)

        async def _both():
            return await asyncio.gather(ref_portraits.generate("item", "Abacus"),
                                        ref_portraits.generate("item", "Abacus"))

        (p1, e1), (p2, e2) = asyncio.run(_both())
        assert len(started) == 1, "the in-flight set must dedupe concurrent requests"
        assert (e1 is None) ^ (e2 is None), "one wins, the other is told it is in flight"

    def test_failure_leaves_no_file(self, monkeypatch):
        import asyncio

        async def _fail(prompt, max_wait=90, width=768, height=1024):
            return None, "image service is rate limiting — try again in a minute"

        monkeypatch.setattr("services.portraits.generate_portrait_image", _fail)
        path, err = asyncio.run(ref_portraits.generate("npc", "Nobody", retries=0))
        assert path is None and "rate limiting" in err
        assert not ref_portraits.have("npc", "Nobody")

    def test_kick_returns_false_without_a_loop(self):
        assert ref_portraits.kick("item", "Abacus") is False, \
            "no event loop (CLI) — the batch script is the caller there"
