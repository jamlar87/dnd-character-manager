#!/usr/bin/env python3
"""Backfill the PHB's spells, which the original ingestion never extracted.

Only 7 of the spell names in the PHB's own text are in spells.json — `silence` among the missing,
which is why a trait granting it ("Silent Steps") could only be typed as "Special". The book's text
is already cached, so this re-extracts from the same source the original pass used, per spell, and
folds the result through scripts/append_extraction.py (append-only, existing-wins) rather than
touching the merged files directly.

Design notes:
  * The page for each spell comes from the cache's own PAGE markers, injected here — not from the
    model. A model that invents pages is exactly how 141 records ended up citing pages that cannot
    exist.
  * Nothing is written unless the model returns a well-formed entry with a real description; a
    malformed block is logged and skipped, never guessed.
  * Resumable: results accumulate in the output extraction file, so a timeout or a restart picks up
    where it stopped.

Run: .venv/bin/python3 scripts/backfill_phb_spells.py [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

CACHE = HERE / "data" / "manual_cache"
MERGED = HERE / "data" / "manual_data"
OUT = CACHE / "PHB_spells_extracted.json"
LOG = HERE / "data" / "phb_spells_backfill.log"

HEADING = re.compile(r"^([A-Z][A-Z'\u2019\- ]{2,40})\s*$")
# Tolerant of how OCR renders a level line: "2nd-level illusion (ritual)", "2nd–level Abjuration",
# "2nd level illusion", "CANTrip". The stricter form missed ~150 spells outright — including
# *silence*, the spell that started this whole thread — because one character of the line was off.
LEVEL = re.compile(r"^\s*(?:\d(?:st|nd|rd|th)\s*[-\u2013\u2014]?\s*level|can\s*trip)\b[^\n]{0,50}$",
                   re.I)
PAGE = re.compile(r"---\s*PAGE\s+(\d+)")

PROMPT = """Extract the spell described below into JSON. The text is OCR'd from a rulebook, so \
repair obvious character-level garbling, but do NOT invent anything that is not in the text.

Return ONLY a JSON object with these keys:
  "name"         the spell's name in title case
  "level"        0 for a cantrip, otherwise the number
  "school"       the school of magic, lower case (e.g. "evocation")
  "casting_time" e.g. "1 action", "1 bonus action", "1 reaction"
  "range"        e.g. "60 feet", "Self", "Touch"
  "components"   e.g. "V, S, M (a bit of fur)"
  "duration"     e.g. "Instantaneous", "1 minute"
  "concentration" true or false
  "ritual"       true or false
  "description"  the spell's rules text, cleaned of the numbering and line breaks
  "higher_levels" the "At Higher Levels" text, or ""

Spell text:
---
{block}
---
"""


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def spell_blocks() -> list[tuple[str, int, str]]:
    """[(name, printed page, text)] for every spell in the PHB's descriptions chapter."""
    text = (CACHE / "PHB.txt").read_text(errors="replace")
    start = text.upper().find("SPELL DESCRIPTIONS")
    body = text[start:] if start > 0 else text
    # Page markers must be indexed against the WHOLE file, not this slice: the chapter's first
    # marker is p.213, so the opening spells (Aid, Alarm, Alter Self) have no marker before them in
    # the slice and were being written with page 0 — a citation that cannot exist, in the very
    # script meant to fix impossible citations.
    page_marks = [(m.start(), int(m.group(1))) for m in PAGE.finditer(text)]
    first_page = page_marks[0][1] if page_marks else 0

    lines = body.splitlines()
    heads: list[tuple[int, str, int]] = []  # (first heading line, name, page)
    for i, raw in enumerate(lines):
        # Anchor on the level line ("3rd-level abjuration"), then walk back over the run of
        # ALL-CAPS lines above it: the PHB wraps long names ("PROTECTION FROM" / "ENERGY"), and
        # anchoring on the caps line instead produced fragments like "Of Life" and "Objects".
        if not LEVEL.match(raw.strip()):
            continue
        parts: list[str] = []
        j = i - 1
        while j >= 0 and len(parts) < 3:
            candidate = lines[j].strip()
            if not candidate or not HEADING.match(candidate):
                break
            parts.insert(0, candidate)
            j -= 1
        if not parts:
            continue
        name = " ".join(parts).strip().title()
        if len(name) < 3 or name.lower().startswith(("chapter", "appendix")):
            continue
        offset = start + sum(len(x) + 1 for x in lines[:j + 1])
        page = [p for pos, p in page_marks if pos <= offset]
        heads.append((j + 1, name, page[-1] if page else first_page))

    out: list[tuple[str, int, str]] = []
    for idx, (i, name, page) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        block = "\n".join(lines[i:end]).strip()
        if len(block) > 120:
            out.append((name, page, block[:4000]))
    return out


def load_existing() -> set[str]:
    names: set[str] = set()
    try:
        for s in json.loads((MERGED / "spells.json").read_text()):
            names.add(str(s.get("name", "")).strip().lower())
    except Exception:
        pass
    return names


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="process at most N spells")
    ap.add_argument("--batch", type=int, default=3, help="spells per LLM call")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import ingest_manual as ing

    blocks = spell_blocks()
    existing = load_existing()
    log(f"PHB spell blocks found: {len(blocks)}")

    state = {"_completed": False, "_book_title": "Player's Handbook",
             "_source_file": "PHB.pdf", "spells": []}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text())
            if isinstance(prev.get("spells"), list):
                state = prev
                existing |= {str(s.get("name", "")).strip().lower() for s in prev["spells"]}
        except Exception:
            pass

    todo = [(n, p, b) for n, p, b in blocks if n.lower() not in existing]
    log(f"already present: {len(blocks) - len(todo)} | to extract: {len(todo)}")
    if args.limit:
        todo = todo[: args.limit]
    if not todo:
        state["_completed"] = True
        OUT.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n")
        log("nothing to extract")
        return 0

    added = 0
    for i in range(0, len(todo), args.batch):
        chunk = todo[i: i + args.batch]
        prompt = "\n\n".join(
            f"### Spell {j + 1} (printed page {p})\n{b}" for j, (_, p, b) in enumerate(chunk)
        )
        prompt = PROMPT.format(block=prompt) + (
            "\nReturn a JSON array of objects (one per spell above), same keys."
            if len(chunk) > 1 else ""
        )
        if args.dry_run:
            log(f"[dry-run] would send {len(chunk)} spell(s): {[c[0] for c in chunk]}")
            continue
        raw = ing._call_llm(prompt)
        parsed = ing._try_parse_json(raw or "")
        items = parsed if isinstance(parsed, list) else (
            [parsed] if isinstance(parsed, dict) else []
        )
        if not items:
            log(f"  batch failed ({[c[0] for c in chunk]}): no JSON returned")
            continue
        for item, (name, page, _) in zip(items, chunk):
            if not isinstance(item, dict) or not item.get("description"):
                log(f"  skipped {name!r}: no description in the response")
                continue
            # The page is ours, not the model's.
            item["name"] = str(item.get("name") or name).strip()
            item["source"] = f"(Player's Handbook, p.{page})"
            item["_source_manual"] = "PHB"
            item.pop("classes", None)
            state["spells"].append(item)
            added += 1
        log(f"  {i + len(chunk)}/{len(todo)} — added {added} so far")
        OUT.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n")

    state["_completed"] = True
    OUT.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n")
    log(f"done: {added} spell(s) written to {OUT.name} — fold with "
        f"`scripts/append_extraction.py PHB`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
