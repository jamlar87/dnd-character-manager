#!/usr/bin/env python3
"""Fill missing monster descriptions from cached PDF text.

Targeted approach: for each _source_manual slug, locate the cached PDF text,
search for each monster name, extract surrounding context, and use an LLM
to pull just the description/flavor text. Updates monsters.json directly.

Usage:
  python3 scripts/fill_monster_descriptions.py --list         # Show gaps per slug
  python3 scripts/fill_monster_descriptions.py --slug TFS     # Fill one slug
  python3 scripts/fill_monster_descriptions.py --all          # Fill all fillable gaps
  python3 scripts/fill_monster_descriptions.py --dry-run TFS  # Show what would be sent
"""
from __future__ import annotations

import json, os, re, sys, time, hashlib
from pathlib import Path
from collections import defaultdict
from typing import Any

HERE = Path(__file__).resolve().parent.parent
MONSTERS_PATH = HERE / "data" / "manual_data" / "monsters.json"
CACHE_DIR = HERE / "data" / "manual_cache"
LLM_CACHE = HERE / "data" / "desc_llm_cache.json"

# Deeper search: also check these related slugs for context
RELATED_SLUGS = {
    "CSF": ["EBT", "TFS", "SDQ"],
    "EBT": ["CSF", "TFS"],
    "TMFRV": ["MPG", "WRKF"],
    "WRKF": ["TMFRV"],
    "VGM": ["CC", "MTF"],
}


def _load_deepseek_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key
    env_file = Path(os.path.expanduser("~/.hermes/.env"))
    if env_file.exists():
        for line in env_file.read_text().split("\n"):
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _call_llm(prompt: str, model: str = "deepseek-chat") -> str | None:
    import urllib.request, urllib.error
    key = _load_deepseek_key()
    if not key:
        return None
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1024,
        "temperature": 0.1,
    }).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  LLM error: {e}")
        return None


def _load_llm_cache() -> dict:
    if LLM_CACHE.exists():
        return json.loads(LLM_CACHE.read_text())
    return {}


def _save_llm_cache(cache: dict) -> None:
    LLM_CACHE.write_text(json.dumps(cache, indent=2))


def load_monsters() -> list[dict]:
    return json.loads(MONSTERS_PATH.read_text())


def save_monsters(monsters: list[dict]) -> None:
    MONSTERS_PATH.write_text(json.dumps(monsters, indent=2, ensure_ascii=False))


def get_gaps_by_slug(monsters: list[dict]) -> dict[str, list[dict]]:
    gaps = defaultdict(list)
    for m in monsters:
        desc = m.get("description") or ""
        if len(desc.strip()) <= 20:
            slug = m.get("_source_manual", "") or "_no_slug"
            gaps[slug].append(m)
    return dict(gaps)


def get_cached_text(slug: str) -> str | None:
    txt_path = CACHE_DIR / f"{slug}.txt"
    if txt_path.exists():
        raw = txt_path.read_text(encoding="utf-8", errors="replace")
        if raw:
            return raw
    return None


def _find_best_context(text: str, monster_name: str, context_chars: int = 3000) -> str | None:
    """Find the best match: prefer context near stat block data over index/ToC entries."""
    if not text:
        return None

    patterns = [re.escape(monster_name)]
    base = re.sub(r'\s*\(.*?\)\s*$', '', monster_name)
    if base != monster_name:
        patterns.append(re.escape(base))

    # Also try with lowercase first word (D&D PDFs often inconsistent)
    words = monster_name.split()
    if len(words) > 1:
        lower_first = words[0].lower() + ' ' + ' '.join(words[1:])
        if lower_first != monster_name:
            patterns.append(re.escape(lower_first))

    stat_indicators = ['Armor Class', 'Hit Points', 'Hit Point', 'STR', 'DEX', 'CON', 
                       'INT', 'WIS', 'CHA', 'AC ', 'HP ', 'Speed ', 'Challenge']

    best_match = None
    best_score = -999

    for pat in patterns:
        for match in re.finditer(pat, text, re.IGNORECASE):
            start = max(0, match.start() - context_chars // 2)
            end = min(len(text), match.end() + context_chars // 2)

            # Snap to page boundary
            pm = text.rfind("--- PAGE ", max(0, start - 200), start)
            if pm >= 0:
                start = pm

            # Snap end to page boundary
            pn = text.find("--- PAGE ", end, end + 200)
            if pn >= 0:
                end = pn

            context = text[start:end].strip()

            # Score this match:
            score = 0
            nearby = text[max(0, match.start()-500):min(len(text), match.end()+500)]

            # +1 for each stat indicator nearby
            for ind in stat_indicators:
                if ind in nearby:
                    score += 2

            # -5 for being in a very short context (index/ToC entry)
            if len(nearby) < 300:
                score -= 5

            # -3 if context looks like a table of contents (lots of dots and page numbers)
            toc_dots = nearby.count('..') + nearby.count('.....')
            if toc_dots > 2:
                score -= 5

            # +3 if there's a full paragraph (>100 chars) before the name that isn't stat block
            before_name = context[:match.start() - start].strip()
            if len(before_name) > 100:
                # Check if it's prose (contains full sentences) not stat data
                if not any(ind in context for ind in stat_indicators):
                    score += 3

            if score > best_score:
                best_score = score
                best_match = context

    return best_match


DESC_PROMPT = """You are extracting monster descriptions from D&D 5e PDF text. Below is a section of text from a PDF. 

I need you to find the monster named "{monster_name}" and extract ONLY its flavor/description text — the paragraph(s) that describe what the monster looks like, its behavior, habitat, or lore. 

This text might be labeled as "Description" or just be a paragraph before the stat block. Do NOT include:
- Stat block data (AC, HP, speed, abilities, actions, traits, saves, skills, etc.)
- Table of contents entries
- Index entries
- Page numbers

If you find the monster's description, respond with ONLY the description text, nothing else.
If you cannot find a description for this monster (only a stat block, no flavor text), respond with exactly "NO_DESC_FOUND".

PDF text context:
```
{context}
```"""


def list_gaps():
    monsters = load_monsters()
    gaps = get_gaps_by_slug(monsters)
    total = sum(len(v) for v in gaps.values())
    print(f"Total monsters: {len(monsters)}")
    print(f"Missing descriptions: {total}")
    print()
    print(f"{'Slug':12s} {'Count':5s}  {'Has text?':10s}  Sample monsters")
    print("-" * 65)
    for slug in sorted(gaps.keys()):
        items = gaps[slug]
        has_text = "YES" if get_cached_text(slug) else "no"
        names = [m["name"][:35] for m in items[:3]]
        print(f"{slug:12s} {len(items):5d}  {has_text:10s}  {', '.join(names)}")
    print(f"\n{total} total gaps across {len(gaps)} source slugs")


def fill_slug(slug: str, dry_run: bool = False) -> int:
    monsters = load_monsters()
    gaps = get_gaps_by_slug(monsters)
    items = gaps.get(slug, [])
    if not items:
        print(f"No gaps for slug '{slug}'")
        return 0

    # Collect text sources: primary slug + related slugs
    texts = {}
    for s in [slug] + RELATED_SLUGS.get(slug, []):
        t = get_cached_text(s)
        if t:
            texts[s] = t

    if not texts:
        print(f"No cached text for slug '{slug}' or its relations")
        return 0

    llm_cache = _load_llm_cache()
    filled = 0
    skipped = 0

    print(f"Processing {slug}: {len(items)} monsters missing descriptions")
    for s, t in texts.items():
        print(f"  Cached text: {s} ({len(t):,} chars)")

    for i, monster in enumerate(items):
        name = monster["name"]
        print(f"  [{i+1}/{len(items)}] {name[:55]}... ", end="", flush=True)

        cache_key = f"{slug}_{name}"
        if cache_key in llm_cache:
            cached = llm_cache[cache_key]
            if cached and cached != "NO_DESC_FOUND":
                monster["description"] = cached
                filled += 1
                print("OK (cached)")
            else:
                skipped += 1
                print("-- (cached no-desc)")
            continue

        # Try each cached text, starting with primary
        context = None
        for s in [slug] + RELATED_SLUGS.get(slug, []):
            if s in texts:
                context = _find_best_context(texts[s], name)
                if context:
                    break

        if not context:
            print("not found in text")
            skipped += 1
            llm_cache[cache_key] = "NO_DESC_FOUND"
            continue

        if dry_run:
            print(f"DRY-RUN ({len(context)} chars)")
            continue

        prompt = DESC_PROMPT.format(monster_name=name, context=context)
        result = _call_llm(prompt)
        if result and result != "NO_DESC_FOUND":
            monster["description"] = result
            llm_cache[cache_key] = result
            filled += 1
            print(f"OK ({len(result)} chars)")
        else:
            llm_cache[cache_key] = "NO_DESC_FOUND"
            skipped += 1
            print("-- no desc found")

        if (i + 1) % 5 == 0:
            _save_llm_cache(llm_cache)
        time.sleep(1.0)

    _save_llm_cache(llm_cache)

    if not dry_run and filled > 0:
        save_monsters(monsters)
        print(f"\nSaved: {filled} filled, {skipped} skipped for {slug}")

    return filled


def fill_all():
    gaps = get_gaps_by_slug(load_monsters())
    total = 0
    for slug in sorted(gaps.keys()):
        if slug == "_no_slug":
            continue
        if get_cached_text(slug) or slug in RELATED_SLUGS:
            print(f"\n{'='*60}")
            total += fill_slug(slug)
        else:
            print(f"\nSkipping {slug}: no cached text ({len(gaps[slug])} monsters)")
    print(f"\n{'='*60}")
    print(f"Done. Total filled: {total}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "--list":
        list_gaps()
    elif cmd == "--dry-run" and len(sys.argv) > 2:
        fill_slug(sys.argv[2], dry_run=True)
    elif cmd == "--slug" and len(sys.argv) > 2:
        fill_slug(sys.argv[2])
    elif cmd == "--all":
        fill_all()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__.strip())
