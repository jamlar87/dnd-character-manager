"""--force must actually regenerate, not hand back the existing file.

generate() short-circuits when a portrait already exists, which is right for the lazy path and for
resuming a bulk run. But it silently defeated the redo: --force selected the construct rows and
every one returned in 0s carrying the old, wrong picture — logged as success, so nothing failed.

These assertions pin both directions, with the provider stubbed so no image is requested.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def _stub(monkeypatch, called):
    from services import ref_portraits as rp

    monkeypatch.setattr(rp, "have", lambda kind, name: True)   # art already exists on disk
    monkeypatch.setattr(rp, "path_for", lambda kind, name: pathlib.Path("/tmp/never-written.webp"))

    async def fake_image(prompt, **kwargs):
        called.append(prompt)
        return None, "stopped here on purpose — no image is requested in a unit test"

    monkeypatch.setattr("services.portraits.generate_portrait_image", fake_image)


def test_without_force_an_existing_portrait_is_not_regenerated(monkeypatch):
    called: list[str] = []
    _stub(monkeypatch, called)
    from services import ref_portraits as rp

    path, err = asyncio.run(rp.generate("item", "Carriage"))
    assert err is None, "an existing portrait is returned as-is, with no error"
    assert not called, "without force it must not hit the provider"


def test_force_reaches_the_provider(monkeypatch):
    called: list[str] = []
    _stub(monkeypatch, called)
    from services import ref_portraits as rp

    asyncio.run(rp.generate("item", "Carriage", "Mounts and Vehicles", "", force=True))
    assert called, ("with force it must call the provider — otherwise --force selects rows, "
                    "generates nothing, and reports success")


def test_forced_prompt_is_the_construct_one(monkeypatch):
    called: list[str] = []
    _stub(monkeypatch, called)
    from services import ref_portraits as rp

    asyncio.run(rp.generate("item", "Airship", "Vehicle", "", force=True))
    assert called
    prompt = called[0]
    for word in ("no people", "no crew", "no creature"):
        assert word in prompt, "a forced redo must use the current prompt, not a stale one"
