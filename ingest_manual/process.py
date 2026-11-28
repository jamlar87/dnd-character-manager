from __future__ import annotations
import time
from .chapters import _detect_chapters
from .chunk_extract import extract_from_chunk
from .core import CACHE_DIR, SKIP_EXTRACTION
from .dedup import _dedup_within_extraction, _load_base_names, dedup_extraction
from .extract import chunk_text, extract_text
from .jsonio import _load_json, _save_json
from .notify import _send_readout
from .race_subclass import _extract_race_details, _extract_subclass_details, _merge_race_details, _merge_subclass_details, _wire_trait_effects
from .validate import validate_extraction


# ═══════════════════════════════════════════════════════════════════════════════
# Per-chapter processing
# ═══════════════════════════════════════════════════════════════════════════════

def _process_chapter(chapter: dict, slug: str, base_names: dict) -> dict:
    """Run the full extraction pipeline on a single chapter's text.
    Returns {chapter_title, new_entries: {category: [items]}, issues, stats}."""
    ch_title = chapter["title"]
    ch_text = chapter["text"]

    if not ch_text or len(ch_text) < 200:
        return {
            "chapter": ch_title,
            "new_entries": {},
            "issues": [f"Chapter text too short ({len(ch_text)} chars), skipped"],
            "stats": {"chunks": 0, "extracted": 0, "valid": 0, "new": 0,
                      "intra_deduped": 0, "srd_deduped": 0, "rejected": 0},
        }

    # 1. Chunk
    chunks = chunk_text(ch_text)
    if not chunks:
        return {
            "chapter": ch_title,
            "new_entries": {},
            "issues": ["No chunkable text"],
            "stats": {"chunks": 0, "extracted": 0, "valid": 0, "new": 0,
                      "intra_deduped": 0, "srd_deduped": 0, "rejected": 0},
        }

    # 2. Extract from each chunk
    accumulated = {
        "races": [], "spells": [], "magic_items": [], "equipment": [],
        "monsters": [], "npcs": [], "feats": [], "backgrounds": [],
        "subclasses": [], "traps": [],
    }

    for chunk in chunks:
        result = extract_from_chunk(chunk, slug)
        for cat in accumulated:
            if cat in result and result[cat]:
                accumulated[cat].extend(result[cat])
        time.sleep(0.5)

    # 3. Intra-chapter dedup
    before_counts = {cat: len(items) for cat, items in accumulated.items()}
    accumulated = _dedup_within_extraction(accumulated)
    after_counts = {cat: len(items) for cat, items in accumulated.items()}
    intra_deduped = sum(before_counts[c] - after_counts[c] for c in before_counts)

    # 4. Validate
    validated = validate_extraction(accumulated, slug)
    total_extracted = sum(len(v) for v in accumulated.values())
    total_validated = sum(len(v) for v in validated.values())
    rejected = total_extracted - total_validated

    # 5. Dedup vs SRD/base
    new_data = dedup_extraction(validated, base_names)
    total_new = sum(len(v) for v in new_data.values())
    srd_deduped = total_validated - total_new

    # 6. Build readout & return
    return {
        "chapter": ch_title,
        "new_entries": new_data,
        "stats": {
            "chunks": len(chunks),
            "extracted": total_extracted,
            "valid": total_validated,
            "new": total_new,
            "intra_deduped": intra_deduped,
            "srd_deduped": srd_deduped,
            "rejected": rejected,
        },
        "issues": [],
    }


def _print_chapter_readout(result: dict):
    """Print a human-readable chapter result readout + notify Telegram."""
    ch = result["chapter"]
    stats = result["stats"]
    new_data = result["new_entries"]

    lines = []
    lines.append(f"\n── Chapter Readout: {ch} ──")
    lines.append(f"Stats: {stats['chunks']} chunks → {stats['extracted']} raw → "
                 f"{stats['valid']} valid → {stats['new']} new")
    if stats["intra_deduped"]:
        lines.append(f"  {stats['intra_deduped']} intra-chapter duplicates removed")
    if stats["srd_deduped"]:
        lines.append(f"  {stats['srd_deduped']} SRD/base duplicates removed")
    if stats["rejected"]:
        lines.append(f"  {stats['rejected']} entries rejected by validation")

    if stats["new"] == 0:
        lines.append("No new entries in this chapter.")
        _send_readout("\n".join(lines))
        return

    for cat, items in sorted(new_data.items()):
        if not items:
            continue
        names = [i.get("name", "?") for i in items[:8]]
        extra = f" (+{len(items) - 8} more)" if len(items) > 8 else ""
        lines.append(f"  {cat}: {len(items)} — {', '.join(names)}{extra}")

    flags = _check_chapter_quality(new_data)
    if flags:
        for f in flags:
            lines.append(f"  ⚠️ {f}")

    _send_readout("\n".join(lines))


def _check_chapter_quality(data: dict) -> list[str]:
    """Quick quality checks on extracted data."""
    flags = []
    for race in data.get("races", []):
        if not race.get("asi"):
            flags.append(f"Race '{race['name']}' missing ASI")
        if not race.get("traits"):
            flags.append(f"Race '{race['name']}' has no traits")
    for spell in data.get("spells", []):
        if not spell.get("level") and spell.get("level") != 0:
            flags.append(f"Spell '{spell['name']}' missing level")
        if not spell.get("school"):
            flags.append(f"Spell '{spell['name']}' missing school")
    for monster in data.get("monsters", []):
        if not monster.get("armor_class"):
            flags.append(f"Monster '{monster['name']}' missing AC")
        if not monster.get("hit_points"):
            flags.append(f"Monster '{monster['name']}' missing HP")
    return flags


# ═══════════════════════════════════════════════════════════════════════════════
# Main extraction pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def process_manual(manual: dict) -> dict | None:
    """Full extraction pipeline for one manual — chapter by chapter."""
    slug = manual["slug"]
    print(f"\n{'='*60}")
    print(f"Processing: {manual['title']} ({slug})")
    print(f"  PDF: {manual['path']} ({manual['size_kb']} KB)")

    if slug in SKIP_EXTRACTION:
        print(f"  SKIPPED: {slug} is covered by SRD base data")
        extract_text(manual)
        return None

    # 1. Extract text (with page markers)
    text = extract_text(manual)
    if not text:
        return None

    # 2. Detect chapters
    chapters = _detect_chapters(text, manual)
    base_names = _load_base_names()

    # 3. Check for existing extraction (chapter-aware state)
    extracted_path = CACHE_DIR / f"{slug}_extracted.json"
    existing = _load_json(extracted_path) if extracted_path.exists() else {}
    if not isinstance(existing, dict):
        existing = {}

    # Detect if old-style extraction exists (flat chunks, no chapters)
    is_old_format = "_chunks_processed" in existing and "_chapters_completed" not in existing
    if is_old_format:
        print("  Old-format extraction found (flat chunks). Starting fresh with chapter mode.\n")
        existing = {}

    chapters_completed = set(existing.get("_chapters_completed", []))
    chapter_errors = existing.get("_chapter_errors", {})

    # Crash recovery: if a chapter was in progress when script died, flag it
    stalled_chapter = existing.get("_current_chapter", "")
    if stalled_chapter and stalled_chapter not in chapters_completed:
        print(f"  ⚠️ Detected interrupted chapter: '{stalled_chapter}' — will re-process\n")
        chapter_errors[stalled_chapter] = "Interrupted (crash/timeout) — re-processing"
        # Remove any partial data from the stalled chapter from accumulated state
        # (since we can't track per-chapter items, we just note it for the readout)

    print(f"  {len(chapters)} chapter(s), {len(chapters_completed)} completed, "
          f"{len(chapter_errors)} with errors\n")

    # 4. Process each chapter
    all_results = []
    total_new = 0
    base_accumulated = {
        "races": existing.get("races", []),
        "spells": existing.get("spells", []),
        "magic_items": existing.get("magic_items", []),
        "equipment": existing.get("equipment", []),
        "monsters": existing.get("monsters", []),
        "npcs": existing.get("npcs", []),
        "feats": existing.get("feats", []),
        "backgrounds": existing.get("backgrounds", []),
        "subclasses": existing.get("subclasses", []),
        "traps": existing.get("traps", []),
    }

    for i, chapter in enumerate(chapters):
        ch_title = chapter["title"]
        if ch_title in chapters_completed:
            print(f"  [{i+1}/{len(chapters)}] {ch_title} — already completed, skipping")
            continue

        print(f"  [{i+1}/{len(chapters)}] {ch_title} "
              f"({len(chapter['text']):,} chars)")

        # Save current-chapter marker BEFORE processing (crash detection)
        _save_json(extracted_path, {**base_accumulated,
                   "_chapters_completed": list(chapters_completed),
                   "_current_chapter": ch_title,
                   "_total_chapters": len(chapters),
                   "_book_slug": slug, "_book_title": manual["title"],
                   "_completed": False, "_timestamp": time.time()})

        try:
            result = _process_chapter(chapter, slug, base_names)
        except Exception as e:
            print(f"  ✖ ERROR processing chapter: {e}")
            chapter_errors[ch_title] = str(e)[:200]
            # Save error state and continue to next chapter
            saved = {**base_accumulated,
                     "_chapters_completed": list(chapters_completed),
                     "_chapter_errors": chapter_errors,
                     "_current_chapter": "",
                     "_total_chapters": len(chapters),
                     "_book_slug": slug, "_book_title": manual["title"],
                     "_completed": False, "_timestamp": time.time()}
            _save_json(extracted_path, saved)
            continue

        all_results.append(result)
        _print_chapter_readout(result)

        # Accumulate
        for cat, items in result["new_entries"].items():
            if cat in base_accumulated:
                base_accumulated[cat].extend(items)

        total_new += result["stats"]["new"]
        chapters_completed.add(ch_title)

        # Save incremental state after each chapter
        saved = {**base_accumulated,
                 "_chapters_completed": list(chapters_completed),
                 "_chapter_errors": chapter_errors,
                 "_current_chapter": "",  # cleared: chapter completed successfully
                 "_total_chapters": len(chapters),
                 "_book_slug": slug,
                 "_book_title": manual["title"],
                 "_completed": False,
                 "_timestamp": time.time()}
        _save_json(extracted_path, saved)

        if len(chapters) > 1:
            time.sleep(1)  # breathe between chapters

    if not chapters_completed:
        return None

    # 5. Race second-pass extraction (on full book text, catches multi-chapter races)
    races_found = base_accumulated.get("races", [])
    if races_found and text:
        print(f"\n  Race detail extraction for {len(races_found)} race(s)...")
        for i, race in enumerate(races_found):
            race_name = race.get("name", "")
            if not race_name:
                continue
            details = _extract_race_details(race_name, text)
            if details:
                races_found[i] = _merge_race_details(race, details)
        base_accumulated['races'] = races_found

    # 5b. Subclass second-pass extraction (catches features split across chunks)
    subclasses_found = base_accumulated.get('subclasses', [])
    if subclasses_found and text:
        print(f'\n  Subclass detail extraction for {len(subclasses_found)} subclass(es)...')
        for i, sc in enumerate(subclasses_found):
            sc_name = sc.get('name', '')
            sc_class = sc.get('class', '')
            if not sc_name:
                continue
            details = _extract_subclass_details(sc_name, sc_class, text)
            if details:
                subclasses_found[i] = _merge_subclass_details(sc, details)
        base_accumulated['subclasses'] = subclasses_found

    # 6. Final cross-chapter dedup
    base_accumulated = _dedup_within_extraction(base_accumulated)

    # 7. Final validate
    validated = validate_extraction(base_accumulated, slug)

    # 8. Final dedup vs SRD/base
    new_data = dedup_extraction(validated, base_names)

    # 9. Trait effects auto-wiring
    races_to_wire = new_data.get("races", [])
    if races_to_wire:
        print(f"\n  Auto-wiring trait effects for {len(races_to_wire)} race(s)...")
        for race in races_to_wire:
            wired = _wire_trait_effects(race)
            if wired:
                race["_effects"] = wired

    # 10. Final save
    final = {**new_data,
             "_chapters_completed": list(chapters_completed),
             "_chapter_errors": chapter_errors,
             "_current_chapter": "",  # all done
             "_total_chapters": len(chapters),
             "_book_slug": slug,
             "_book_title": manual["title"],
             "_completed": True,
             "_timestamp": time.time()}
    _save_json(extracted_path, final)

    # 11. Final report
    total_new = sum(len(v) for v in new_data.values())
    print(f"\n  ✓ Extraction complete: {total_new} new entries across {len(chapters)} chapter(s)")
    for cat, items in sorted(new_data.items()):
        if items:
            names = [i.get("name", "?") for i in items[:5]]
            suffix = f" (+{len(items)-5} more)" if len(items) > 5 else ""
            print(f"    {cat}: {len(items)} — {', '.join(names)}{suffix}")

    return new_data
