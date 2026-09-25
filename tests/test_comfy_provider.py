"""The local ComfyUI provider (bazzite, AMD RX 9060 XT via ROCm).

Why these tests exist:

The Horde integration shipped green against ten mocked tests and then rejected every REAL job,
because the mocks encoded an assumption (HTTP 200) that the live API contradicted (202). So the
point of these is not to re-assert the mock — it is to pin the things that are cheap to get wrong
and expensive to notice: the graph the API actually accepts, and the two-step history/view
handshake. A workflow that is structurally wrong comes back as a 400 that says nothing useful, and
ComfyUI raises nothing on this side, so a silently-missing image would just look like "no art".
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from services import portraits  # noqa: E402

PNG_BYTES = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


# ── the graph ────────────────────────────────────────────────────────────────────────────────────

def test_workflow_is_api_format_with_the_nodes_comfyui_requires():
    wf = portraits.comfy_workflow("a clockwork dragon", 832, 1216, seed=42)
    for node in wf.values():
        assert "class_type" in node and "inputs" in node, node
    classes = {n["class_type"] for n in wf.values()}
    assert {"CheckpointLoaderSimple", "CLIPTextEncode", "EmptyLatentImage",
            "KSampler", "VAEDecode", "SaveImage"} <= classes


def test_workflow_wires_the_prompt_and_dimensions_through():
    wf = portraits.comfy_workflow("a clockwork dragon", 832, 1216, seed=42)
    positive = [n for n in wf.values()
                if n["class_type"] == "CLIPTextEncode" and n["inputs"]["text"].startswith("a clock")]
    assert len(positive) == 1
    assert positive[0]["inputs"]["text"] == "a clockwork dragon"
    latent = next(n for n in wf.values() if n["class_type"] == "EmptyLatentImage")
    assert (latent["inputs"]["width"], latent["inputs"]["height"]) == (832, 1216)
    sampler = next(n for n in wf.values() if n["class_type"] == "KSampler")
    assert sampler["inputs"]["seed"] == 42


def test_the_negative_prompt_is_actually_negative():
    """A positive prompt in the negative slot degrades quality without erroring — invisible."""
    wf = portraits.comfy_workflow("a clockwork dragon", 832, 1216, seed=1)
    texts = [n["inputs"]["text"] for n in wf.values() if n["class_type"] == "CLIPTextEncode"]
    assert "watermark" in texts[1] and "a clockwork dragon" not in texts[1]


def test_save_image_node_id_matches_what_the_poller_reads():
    """The poller hardcodes node "9" for outputs. If the graph renumbers, it must fail loudly."""
    wf = portraits.comfy_workflow("x", 64, 64, seed=1)
    assert "9" in wf and wf["9"]["class_type"] == "SaveImage"


def test_the_checkpoint_name_is_configurable_and_reaches_the_graph():
    wf = portraits.comfy_workflow("x", 64, 64, seed=1, ckpt="something-else.safetensors")
    loader = next(n for n in wf.values() if n["class_type"] == "CheckpointLoaderSimple")
    assert loader["inputs"]["ckpt_name"] == "something-else.safetensors"


# ── the HTTP handshake ───────────────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status=200, payload=None, content=b"", headers=None):
        self.status_code = status
        self._payload = payload
        self.content = content
        self.text = json.dumps(payload) if payload is not None else ""
        self.headers = headers or {}

    def json(self):
        return self._payload


class _Client:
    """Records calls; replays a scripted history so the poll loop can be driven deterministically."""

    def __init__(self, post=None, history=None, view=None):
        self.calls = []
        self._post, self._history, self._view = post, list(history or []), view

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        self.calls.append(("POST", url, json))
        return self._post

    async def get(self, url, params=None):
        self.calls.append(("GET", url, params))
        if "/history/" in url:
            return self._history.pop(0) if self._history else _Resp(payload={})
        return self._view


def _patch(monkeypatch, client, poll=0.0):
    """Replace the HTTP client and collapse the poll delay.

    The sleep stub must close over the ORIGINAL asyncio.sleep: `portraits.asyncio` is the asyncio
    module itself, so patching it is a global patch, and a lambda that calls `asyncio.sleep` would
    call its own replacement and recurse.
    """
    real_sleep = asyncio.sleep
    monkeypatch.setattr(portraits.httpx, "AsyncClient", lambda **kw: client)
    monkeypatch.setattr(portraits.asyncio, "sleep", lambda s: real_sleep(0))


def test_a_finished_job_returns_the_image(monkeypatch):
    hist = _Resp(payload={"job-1": {"status": {"status_str": "success"},
                                    "outputs": {"9": {"images": [{"filename": "dnd_ref_00001_.png",
                                                                  "subfolder": "", "type": "output"}]}}}})
    client = _Client(post=_Resp(status=200, payload={"prompt_id": "job-1"}),
                     history=[hist],
                     view=_Resp(status=200, content=PNG_BYTES,
                                headers={"content-type": "image/png"}))
    _patch(monkeypatch, client)
    raw, err = asyncio.run(portraits.fetch_comfy_image("a dragon", max_wait=6, poll_every=0.01))
    assert err is None, err
    assert raw.startswith("data:image/png;base64,")
    assert client.calls[1][1].endswith("/history/job-1")
    assert client.calls[-1][0] == "GET" and client.calls[-1][1].endswith("/view")


def test_a_still_running_job_is_polled_not_treated_as_done(monkeypatch):
    """An empty history entry means queued/running. Returning early there yields "no image"."""
    client = _Client(post=_Resp(status=200, payload={"prompt_id": "job-2"}),
                     history=[_Resp(payload={}), _Resp(payload={}),
                              _Resp(payload={"job-2": {"status": {"status_str": "success"},
                                                       "outputs": {"9": {"images": [
                                                           {"filename": "f.png", "subfolder": "",
                                                            "type": "output"}]}}}})],
                     view=_Resp(status=200, content=PNG_BYTES,
                                headers={"content-type": "image/png"}))
    _patch(monkeypatch, client)
    raw, err = asyncio.run(portraits.fetch_comfy_image("a dragon", max_wait=6, poll_every=0.01))
    assert err is None, err and "should have waited through the empty polls"
    assert sum(1 for c in client.calls if "/history/" in c[1]) == 3


def test_a_rejected_workflow_reports_the_reason_not_just_the_code(monkeypatch):
    client = _Client(post=_Resp(status=400, payload={"error": {"message": "invalid prompt"}}))
    _patch(monkeypatch, client)
    raw, err = asyncio.run(portraits.fetch_comfy_image("a dragon", max_wait=2))
    assert raw is None
    assert "400" in err and "invalid prompt" in err


def test_an_erroring_job_does_not_hang_the_full_timeout(monkeypatch):
    client = _Client(post=_Resp(status=200, payload={"prompt_id": "job-3"}),
                     history=[_Resp(payload={"job-3": {"status": {"status_str": "error",
                                                                  "messages": ["boom"]},
                                                       "outputs": {}}})])
    _patch(monkeypatch, client)
    raw, err = asyncio.run(portraits.fetch_comfy_image("a dragon", max_wait=60, poll_every=0.01))
    assert raw is None and "failed" in err


def test_an_unreachable_host_says_so_rather_than_timing_out(monkeypatch):
    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise ConnectionRefusedError("refused")

    monkeypatch.setattr(portraits.httpx, "AsyncClient", lambda **kw: _Boom())
    raw, err = asyncio.run(portraits.fetch_comfy_image("a dragon", max_wait=2))
    assert raw is None and "unreachable" in err


def test_comfy_is_selectable_by_name_and_is_not_the_default(monkeypatch):
    """The default must stay a keyless hosted provider: comfy needs another machine to be awake."""
    import importlib
    monkeypatch.delenv("PORTRAIT_PROVIDER", raising=False)
    importlib.reload(portraits)
    assert portraits.PORTRAIT_PROVIDER != "comfy"
    assert portraits.COMFY_URL.startswith("http")


def test_the_checkpoint_is_an_illustration_model_not_a_photo_model():
    """The bug that cost an evening: a photorealism checkpoint faithfully drew photographs.

    "Juggernaut-XL_v9_RunDiffusionPhoto_v2" is trained for photographic subjects, and for prompts about
    clockwork constructs it produced aerial terrain, a nude figure, a shaggy animal and a dead fish.
    Nothing errored and every image looked confident. The name is the only signal available here, so
    it is asserted directly.
    """
    name = portraits.COMFY_CKPT.lower()
    assert "photo" not in name, f"a photo checkpoint cannot draw fantasy illustration: {name}"
    assert "jugger" not in name, f"the photorealism checkpoint that produced the bad output: {name}"


def test_a_turbo_checkpoint_gets_turbo_sampling_settings():
    """Turbo/distilled SDXL wants ~6-8 steps at CFG ~2. At 28 steps and CFG 6.5 it is slow AND wrong."""
    wf = portraits.comfy_workflow("a clockwork dragon", 832, 1216, seed=7)
    sampler = wf["3"]["inputs"]
    if "turbo" in portraits.COMFY_CKPT.lower():
        assert sampler["steps"] <= 10, f"turbo model at {sampler['steps']} steps"
        assert sampler["cfg"] <= 3.0, f"turbo model at CFG {sampler['cfg']}"
    assert sampler["steps"] >= 4 and sampler["cfg"] > 0


def test_the_sampler_settings_reach_the_graph_not_just_the_constants(monkeypatch):
    """A constant nobody wires into the workflow is decoration."""
    monkeypatch.setattr(portraits, "COMFY_STEPS", 9)
    monkeypatch.setattr(portraits, "COMFY_CFG", 1.75)
    monkeypatch.setattr(portraits, "COMFY_SAMPLER", "euler_a")
    sampler = portraits.comfy_workflow("x", 64, 64, seed=1)["3"]["inputs"]
    assert sampler["steps"] == 9 and sampler["cfg"] == 1.75 and sampler["sampler_name"] == "euler_a"


def test_the_sfw_checkpoint_is_preferred():
    """The photo model produced semi-nude photoreal humans twice, in a family reference library.

    A checkpoint name is not a safety control, but when a safe-for-work build of the same model exists
    there is no reason to ship the other one.
    """
    assert "sfw" in portraits.COMFY_CKPT.lower(), (
        f"prefer the -SFW build of an illustration checkpoint: {portraits.COMFY_CKPT}")

