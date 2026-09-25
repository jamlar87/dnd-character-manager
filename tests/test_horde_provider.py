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
    assert record["post"]["json"]["models"] == ["stable_diffusion"]


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


def test_the_default_provider_falls_back_to_pollinations(monkeypatch):
    """A stalled horde queue must not hand the caller an empty portrait."""
    monkeypatch.setattr(portraits, "PORTRAIT_PROVIDER", "horde")

    async def _horde(prompt, **kwargs):
        return None, "Stable Horde did not finish within 600s (the free queue is slow)"

    async def _pollinations(prompt, **kwargs):
        return "data:image/webp;base64," + PNG, None

    monkeypatch.setattr(portraits, "fetch_horde_image", _horde)
    monkeypatch.setattr(portraits, "fetch_pollinations_image", _pollinations)
    monkeypatch.setattr(portraits, "normalize_portrait", lambda raw, max_px: (raw, None), raising=False)

    import services.images as images
    monkeypatch.setattr(images, "normalize_portrait", lambda raw, max_px: (raw, None))

    raw, error = asyncio.run(portraits.generate_portrait_image("x"))
    assert error is None
    assert raw.startswith("data:image/")


def test_both_providers_failing_names_both(monkeypatch):
    monkeypatch.setattr(portraits, "PORTRAIT_PROVIDER", "horde")

    async def _fail(prompt, **kwargs):
        return None, "provider said no"

    monkeypatch.setattr(portraits, "fetch_horde_image", _fail)
    monkeypatch.setattr(portraits, "fetch_pollinations_image", _fail)
    raw, error = asyncio.run(portraits.generate_portrait_image("x"))
    assert raw is None
    assert "pollinations fallback" in error, "the second failure must not be hidden"
