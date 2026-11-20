"""Portrait painting lives in the AI DM. This module is only the courier.

A hero used to be painted twice — once by the DM for the table, once by this app
for the sheet — with two prompt builders, two seeds and two checkpoints. Same
name, two faces. The DM's renderer is the one kept: one prompt, one seed, one
model, so a face painted at the table is the face on the sheet, and the reverse.

So this app holds no prompt for characters any more. It posts the sheet to
``POST {DM_URL}/api/portrait/paint`` and stores what comes back. The DM rebuilds
the same identity strings its party card uses, which is why the bytes that come
back are the bytes the table already has (an existing face returns in ~0s).

If the DM is down there is deliberately NO fallback to the old providers: the
old providers are exactly the second face this was built to remove.
"""

from __future__ import annotations

import os

import httpx

#: The DM runs on the same box (systemd user unit, port 8700). Loopback, not the
#: Cloudflare hostname — the paint endpoint refuses tunnel traffic on purpose.
DM_URL = os.environ.get("DM_URL", "http://127.0.0.1:8700").rstrip("/")

#: How long to hold the request open while the DM's GPU works. The browser is
#: waiting at the other end, so this is bounded on both sides.
DM_WAIT = int(os.environ.get("DM_PORTRAIT_WAIT", "150"))

#: Must outlast DM_WAIT, or the socket gives up before the answer does.
TIMEOUT = float(os.environ.get("DM_PORTRAIT_TIMEOUT", "200"))


async def paint(payload: dict, wait_s: int | None = None,
                prompt_only: bool = False) -> tuple[str, str, str]:
    """Ask the DM for one face.

    Returns ``(data_url, prompt, error)``. ``prompt`` comes back even when the
    image does not, so the sheet can show the words the renderer will use.
    """
    body = dict(payload)
    body["wait_s"] = DM_WAIT if wait_s is None else wait_s
    if prompt_only:
        body["prompt_only"] = True
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            res = await client.post(f"{DM_URL}/api/portrait/paint", json=body)
    except Exception as exc:                 # noqa: BLE001 — an unreachable DM is a status, not a crash
        return "", "", f"the DM's renderer is unreachable at {DM_URL} ({type(exc).__name__})"
    if res.status_code != 200:
        return "", "", f"the DM's renderer refused ({res.status_code}): {res.text[:200]}"
    try:
        data = res.json() or {}
    except Exception:                        # noqa: BLE001
        return "", "", "the DM's renderer sent something that is not JSON"
    prompt = str(data.get("prompt") or "")
    image = str(data.get("image") or "")
    if prompt_only:
        return "", prompt, ""
    if image:
        return image, prompt, ""
    if data.get("failed"):
        return "", prompt, "the DM's renderer failed to paint this face"
    return "", prompt, "the DM is still painting — press Generate again in a moment"


async def ready() -> bool:
    """Is the renderer there at all? Used by callers that want to explain the failure."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            res = await client.get(f"{DM_URL}/api/avatar.json", params={"name": "probe", "kind": "npc"})
        return res.status_code == 200
    except Exception:                        # noqa: BLE001
        return False
