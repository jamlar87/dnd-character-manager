"""Stable Horde provider: submit, poll, download — and the failure paths that matter.

Horde is crowdsourced, so the shape of the interaction is not a single request: a job is submitted,
then polled until a volunteer's GPU finishes it, then the image is fetched from wherever it landed.
The provider it replaced answered in one shot, so every one of these paths is new and none of them
is exercised by the existing portrait tests.
"""
from __future__ import annotations

import asyncio
import base64
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from services import portraits  # noqa: E402

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()


class _Resp:
    def __init__(self, status_code=200, payload=None, content=b"", ctype="image/webp"):
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.headers = {"content-type": ctype}

    def json(self):
        return self._payload


class _FakeClient:
    """Scripted httpx.AsyncClient: one POST, then a queue of GETs."""

    def __init__(self, post, gets, record):
        self._post, self._gets, self._record = post, list(gets), record

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        self._record["post"] = {"url": url, "json": json, "headers": headers}
        if isinstance(self._post, Exception):
            raise self._post
        return self._post

    async def get(self, url, headers=None, **kwargs):
        self._record.setdefault("gets", []).append({"url": url, "headers": headers})
        if not self._gets:
            raise AssertionError(f"unexpected GET {url}")
        nxt = self._gets.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _run(monkeypatch, post, gets, **kwargs):
    record: dict = {}
    monkeypatch.setattr(portraits.httpx, "AsyncClient",
                        lambda **_: _FakeClient(post, gets, record))
    monkeypatch.setattr(portraits.asyncio, "sleep", _no_sleep)
    result = asyncio.run(portraits.fetch_horde_image(
        "a brass clockwork soldier", poll_every=0.001, **kwargs))
    return result, record


async def _no_sleep(*_args, **_kwargs):
    return None


def test_a_finished_job_returns_a_data_url(monkeypatch):
    monkeypatch.delenv("STABLEHORDE_MODEL", raising=False)
    post = _Resp(202, {"id": "job-1"})          # 202 Accepted is what the live API returns
    gets = [
        _Resp(200, {"done": False, "queue_position": 12}),
        _Resp(200, {"done": False, "queue_position": 3}),
        _Resp(200, {"done": True, "generations": [{"img": "https://cdn.example/x.webp"}]}),
        _Resp(200, content=b"pretend-webp", ctype="image/webp"),
    ]
    (data_url, error), record = _run(monkeypatch, post, gets)
    assert error is None, "202 Accepted is a success — requiring 200 discarded every real job"
    assert data_url.startswith("data:image/webp;base64,")
    assert base64.b64decode(data_url.split(",", 1)[1]) == b"pretend-webp"
    assert record["post"]["url"] == portraits.HORDE_ASYNC
    assert record["post"]["json"]["params"]["width"] == 768
    # Bound to the constant, not a literal: the model was pinned here once before and the test had
    # to be edited when the default changed, which is exactly how a stale literal goes unnoticed.
    assert record["post"]["json"]["models"] == [portraits.HORDE_MODELS[0]]


def test_the_anonymous_key_is_sent_when_nothing_is_configured(monkeypatch):
    monkeypatch.delenv("STABLEHORDE_API_KEY", raising=False)
    post = _Resp(200, {"id": "job-1"})
    gets = [_Resp(200, {"done": True, "generations": [{"img": "u"}]}), _Resp(200, content=b"x")]
    (data_url, error), record = _run(monkeypatch, post, gets)
    assert record["post"]["headers"]["apikey"] == "0000000000"
    assert data_url and error is None


def test_a_registered_key_is_honoured(monkeypatch):
    monkeypatch.setenv("STABLEHORDE_API_KEY", "abc123")
    post = _Resp(200, {"id": "job-1"})
    gets = [_Resp(200, {"done": True, "generations": [{"img": "u"}]}), _Resp(200, content=b"x")]
    _, record = _run(monkeypatch, post, gets)
    assert record["post"]["headers"]["apikey"] == "abc123"


def test_a_cued_item_is_not_called_an_item_and_has_no_category_leak():
    """"RPG item illustration of Carriage (Mounts and Vehicles)" produced a soft timber mass.

    Two faults in one line: "item" leads a diffusion model to a small hand-held object, and the
    parenthesised text is the record's filing category, not a description of the thing.
    """
    from services.ref_portraits import prompt_for

    p = prompt_for("item", "Carriage", "Mounts and Vehicles · Common")
    assert "Mounts and Vehicles" not in p, "the category is a filing label, not a description"
    assert "(" not in p, "no parenthetical category leaks into the prompt"
    assert not p.startswith("RPG item"), "'item' points the model at a small object"
    assert "built vessel of timber" in p, "the vehicle cue must still apply"
    assert "constructed object" in p


def test_a_plain_item_keeps_its_own_wording():
    """Potions and rings are genuinely items — the fix must not cosmeticise every item prompt."""
    from services.ref_portraits import prompt_for

    p = prompt_for("item", "Potion of Healing", "Wondrous Items · Common")
    assert "RPG item illustration" in p
    assert "constructed object" not in p


def test_the_horde_model_is_sdxl_class_not_sd15():
    """SD1.5 rendered the Carriage as a soft mass; the pool size of the chosen model is the lever.

    Kudos drive queue priority and a 0-kudos account queues last, so the model's worker count is
    what actually governs throughput here.
    """
    import inspect

    from services import portraits

    src = inspect.getsource(portraits)
    assert 'HORDE_MODELS = ("AlbedoBase XL 3.1",)' in src
    assert "NSFW" not in src.split("HORDE_MODELS = ")[1].split("\n")[0], \
        "a family fantasy library must not default to an NSFW fine-tune"


def test_the_generator_reads_dotenv_like_the_app_does():
    """A key in .env must reach the batch script, not only the web app.

    main.py loads .env, but scripts/generate_portraits.py never imports main (it builds the entity
    index at import), so before this the generator only saw exported variables.
    """
    import inspect

    from services import portraits

    src = inspect.getsource(portraits)
    assert "_env_path" in src, "the shared module must load .env for both callers"
    assert "setdefault" in src, "a real environment variable must still win over the file"


def test_a_rejected_submit_reports_the_status(monkeypatch):
    (data_url, error), _ = _run(monkeypatch, _Resp(429), [])
    assert data_url is None
    assert "429" in error


def test_a_faulted_job_fails_instead_of_polling_forever(monkeypatch):
    post = _Resp(200, {"id": "job-1"})
    gets = [_Resp(200, {"done": False}), _Resp(200, {"faulted": True})]
    (data_url, error), _ = _run(monkeypatch, post, gets)
    assert data_url is None
    assert "could not fulfil" in error


def test_a_job_that_never_finishes_says_so(monkeypatch):
    """The free queue can simply be slow; the error must name the wait, not blame the prompt."""
    post = _Resp(200, {"id": "job-1"})
    gets = [_Resp(200, {"done": False}) for _ in range(40)]
    (data_url, error), _ = _run(monkeypatch, post, gets, max_wait=1)
    assert data_url is None
    assert "did not finish within" in error


def test_an_unreachable_service_is_not_an_exception(monkeypatch):
    (data_url, error), _ = _run(monkeypatch, OSError("network down"), [])
    assert data_url is None
    assert "unreachable" in error


def test_a_job_without_an_id_is_reported(monkeypatch):
    (data_url, error), _ = _run(monkeypatch, _Resp(200, {"message": "no id here"}), [])
    assert data_url is None
    assert "no job id" in error


def test_the_default_provider_does_not_fall_back_to_the_watermarked_one(monkeypatch):
    """A stalled horde queue must fail visibly, not silently produce watermarked art.

    Pollinations stamps pollinations.ai on its output despite nologo=true. One watermarked image
    among 5,700 records would be easy to miss, so the fallback is gone rather than merely avoided.
    """
    monkeypatch.setattr(portraits, "PORTRAIT_PROVIDER", "horde")
    called = {"pollinations": 0}

    async def _horde(prompt, **kwargs):
        return None, "Stable Horde did not finish within 600s (the free queue is slow)"

    async def _pollinations(prompt, **kwargs):
        called["pollinations"] += 1
        return "data:image/webp;base64," + PNG, None

    monkeypatch.setattr(portraits, "fetch_horde_image", _horde)
    monkeypatch.setattr(portraits, "fetch_pollinations_image", _pollinations)

    raw, error = asyncio.run(portraits.generate_portrait_image("x"))
    assert raw is None
    assert "did not finish within" in error, "the real reason must reach the caller"
    assert called["pollinations"] == 0, "no watermarked fallback"


def test_pollinations_is_still_available_when_named(monkeypatch):
    """It is opt-in by name, not deleted — the code path must still work."""
    monkeypatch.setattr(portraits, "PORTRAIT_PROVIDER", "pollinations")

    async def _pollinations(prompt, **kwargs):
        return "data:image/webp;base64," + PNG, None

    monkeypatch.setattr(portraits, "fetch_pollinations_image", _pollinations)
    import services.images as images
    monkeypatch.setattr(images, "normalize_portrait", lambda raw, max_px: (raw, None))

    raw, error = asyncio.run(portraits.generate_portrait_image("x"))
    assert error is None
    assert raw.startswith("data:image/")
