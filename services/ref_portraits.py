"""Reference art for monsters, items and the manual NPC library.

A different problem from character portraits: these are thousands of *shared*
rows (1,936 creatures, 1,559 items, 414 NPCs) and they are not DB rows at all —
they come from the entity index. Storing base64 in a row is impossible here and
a data URL in the HTML is exactly what made /dm-tools 3.3 MB.

So reference art lives as FILES under static/ref-portraits/<kind>/, served by a
route that offers `?size=` thumbnails, and **file existence is the state**:

  - lazy: a request for a missing image starts a background generation and
    answers 404, so the caller keeps showing its letter tile and the picture is
    there on the next view;
  - bulk: scripts/generate_portraits.py --kind prewarms the same directory.

Both paths write through here, so they cannot disagree, and both are resumable.
Generation is serialised (one request at a time) because the provider is a free
tier with rate limits.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import threading
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
ROOT = STATIC / "ref-portraits"

#: kind -> (width, height). Busts are 3:4, objects square on the canvas.
KINDS: dict[str, tuple[int, int]] = {
    "creature": (768, 1024),
    "npc": (768, 1024),
    "item": (768, 768),
}

_INFLIGHT: set[str] = set()
_LOCK = threading.Lock()
# one generation at a time — the free provider rate limits hard
_SEM = asyncio.Semaphore(1)


def slug_for(name: str) -> str:
    """Deterministic, collision-free, human-readable file name."""
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:80] or "unnamed"
    digest = hashlib.md5((name or "").encode("utf-8")).hexdigest()[:6]
    return f"{base}-{digest}"


def path_for(kind: str, name: str) -> Path:
    return ROOT / kind / f"{slug_for(name)}.webp"


def have(kind: str, name: str) -> bool:
    p = path_for(kind, name)
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def prompt_for(kind: str, name: str, subtitle: str = "", snippet: str = "") -> str:
    """Prompt per kind. Same shape as the character prompts (bust/3:4 language
    comes from services.portraits) so the library looks consistent."""
    detail = " ".join((subtitle or "").split())[:120]
    tail = " ".join((snippet or "").split())[:200]
    if kind == "creature":
        return ("Fantasy bestiary illustration of " + (name or "a monster")
                + (f", {detail}." if detail else ".")
                + " Full body, single creature, centred, plain parchment background,"
                  " painterly high fantasy style, dramatic lighting, detailed." + (f" {tail}" if tail else ""))
    if kind == "item":
        return ("RPG item illustration of " + (name or "an item")
                + (f" ({detail})" if detail else "")
                + ". Single object centred on a plain dark background, painterly"
                  " high fantasy style, soft rim light, detailed." + (f" {tail}" if tail else ""))
    # npc / anything else -> the character prompt builder keeps the look consistent
    from services.portraits import npc_prompt
    return npc_prompt(name, notes=tail or snippet or "", race=detail)


def save(kind: str, name: str, data_url: str) -> int:
    """Write the image atomically. Returns bytes written (0 = nothing stored)."""
    from services.images import decode_data_url, thumbnail_bytes
    decoded = decode_data_url(data_url)
    if not decoded:
        return 0
    blob, _media = decoded
    if not blob:
        return 0
    # same shrink rule as every portrait write: WebP, capped at 1024px
    thumb = thumbnail_bytes(blob, 1024)
    out = thumb[0] if thumb else blob
    dest = path_for(kind, name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".webp.tmp")
    tmp.write_bytes(out)
    tmp.replace(dest)          # atomic: a reader never sees a half-written file
    return len(out)


async def generate(kind: str, name: str, subtitle: str = "", snippet: str = "",
                   max_wait: float = 120, retries: int = 2) -> tuple[Path | None, str | None]:
    """Generate one image if it is missing. Returns (path, error)."""
    if kind not in KINDS:
        return None, f"unknown kind '{kind}'"
    if have(kind, name):
        return path_for(kind, name), None

    key = f"{kind}/{name}"
    with _LOCK:
        if key in _INFLIGHT:
            return None, "already generating"
        _INFLIGHT.add(key)
    try:
        from services.portraits import generate_portrait_image
        width, height = KINDS[kind]
        prompt = prompt_for(kind, name, subtitle, snippet)
        last = "no attempt made"
        async with _SEM:
            for attempt in range(retries + 1):
                data, err = await generate_portrait_image(prompt, max_wait=max_wait,
                                                          width=width, height=height)
                if data:
                    size = save(kind, name, data)
                    if size:
                        return path_for(kind, name), None
                    last = "generated image could not be stored"
                else:
                    last = err or "no image returned"
                if attempt < retries:
                    await asyncio.sleep(15 * (attempt + 1))
        return None, last
    finally:
        with _LOCK:
            _INFLIGHT.discard(key)


def kick(kind: str, name: str, subtitle: str = "", snippet: str = "") -> bool:
    """Start a background generation if we can. True = one is now running.

    Used by the image route: the caller gets a 404 and its letter tile, and the
    picture is ready for the next request. Never raises.
    """
    if kind not in KINDS or have(kind, name):
        return False
    key = f"{kind}/{name}"
    with _LOCK:
        if key in _INFLIGHT:
            return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False                      # no loop (CLI) — the batch script is the caller
    loop.create_task(generate(kind, name, subtitle, snippet))
    return True


def stats(kinds: list[str] | None = None) -> dict:
    """How many images exist per kind. Cheap: directory listing, no indexing."""
    out = {}
    for kind in (kinds or list(KINDS)):
        d = ROOT / kind
        n = len([f for f in d.glob("*.webp")]) if d.is_dir() else 0
        try:
            mb = sum(f.stat().st_size for f in d.glob("*.webp")) / 1024 / 1024
        except OSError:
            mb = 0.0
        out[kind] = {"images": n, "mb": round(mb, 1)}
    return out
