#!/usr/bin/env python3
"""Probe: can OpenRouter's free models do the extraction work the sweep does?

Feeds one real chunk (MPMM pp.32-37, which includes the Tortle entry) through the engine's
own EXTRACTION_PROMPT and parser, so the comparison is apples to apples with DeepSeek.
Run: .venv/bin/python3 scripts/probe_openrouter_extraction.py
"""
import json
import pathlib
import re
import sys
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import ingest_manual as ing  # noqa: E402

CANDIDATES = (
    "qwen/qwen3.8-27b:free",
    "nvidia/nemotron-3.5-lightning:free",
    "google/gemma-4-31b-it:free",
)


def load_key() -> str:
    env = pathlib.Path.home() / ".hermes" / ".env"
    for line in env.read_text().split("\n"):
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def call(model: str, prompt: str, key: str) -> str:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 4096,
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}",
                 "X-Title": "DnD ingestion probe"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"]


def make_chunk() -> str:
    txt = (HERE / "data" / "manual_cache" / "MPMM.txt").read_text()
    parts = re.split(r"--- PAGE (\d+) ---", txt)
    pages = {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}
    return "".join(pages.get(n, "") for n in range(32, 38))[:7000]


def main() -> int:
    key = load_key()
    if not key:
        print("no OPENROUTER_API_KEY found")
        return 1

    chunk = make_chunk()
    print(f"chunk: {len(chunk):,} chars — MPMM pp.32-37 (includes the Tortle entry)")
    prompt = ing.EXTRACTION_PROMPT.replace("{text}", chunk)

    # Gemini first: also free, and the engine already speaks to it. Free tiers throw
    # transient 503/429s, so retry before judging.
    gem_only = "--gemini-only" in sys.argv
    raw = None
    t0 = time.time()
    for attempt in range(1, 4):
        t0 = time.time()
        raw = ing._call_gemini(prompt)
        if raw:
            break
        print(f"  gemini attempt {attempt}: no response after {time.time() - t0:.1f}s")
        time.sleep(8)
    parsed = ing._try_parse_json(raw) if raw else None
    counts = {k: len(v) for k, v in (parsed or {}).items() if isinstance(v, list) and v}
    low = (raw or "").lower()
    races = [r.get("name") for r in (parsed or {}).get("races", []) if isinstance(r, dict)]
    print(f"\ngemini-2.5-flash (free tier)")
    print(f"  {time.time() - t0:.1f}s | parsed={bool(parsed)} | items={counts}")
    print(f"  Tortle={'tortle' in low} claws dice={('1d6' in low or '1d4' in low)} "
          f"Nature's Intuition={('nature' + chr(39) + 's intuition') in low}")
    print(f"  races found: {races[:8]}")
    if "--dump" in sys.argv:
        print("\n  --- raw head ---")
        print("  " + (raw or "")[:300].replace("\n", "\n  "))
        print("\n  --- parsed shape ---")
        p = parsed if isinstance(parsed, dict) else {}
        for k, v in list(p.items())[:10]:
            kind = type(v).__name__
            extra = f" len={len(v)}" if isinstance(v, (list, dict)) else ""
            print(f"    {k}: {kind}{extra}")
        print(f"    raw starts: {(raw or '')[:60]!r}")
    if gem_only:
        return 0

    for model in CANDIDATES:
        t0 = time.time()
        try:
            raw = call(model, prompt, key)
        except Exception as exc:  # noqa: BLE001
            print(f"\n{model}\n  FAILED after {time.time() - t0:.1f}s: {type(exc).__name__}: {exc}")
            continue
        dt = time.time() - t0
        parsed = ing._try_parse_json(raw) if raw else None
        counts = {k: len(v) for k, v in (parsed or {}).items() if isinstance(v, list) and v}
        low = (raw or "").lower()
        races = [r.get("name") for r in (parsed or {}).get("races", []) if isinstance(r, dict)]
        has_natures = "nature's intuition" in low
        has_dice = "1d6" in low or "1d4" in low
        print(f"\n{model}")
        print(f"  {dt:.1f}s | parsed={bool(parsed)} | items={counts}")
        print(f"  mentions Tortle={('tortle' in low)} claws dice={has_dice} "
              f"Nature's Intuition={has_natures}")
        print(f"  races found: {races[:8]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
