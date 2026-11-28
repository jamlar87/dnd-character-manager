#!/usr/bin/env python3
"""D&D Manual Ingestion Engine — extract structured data from reference PDFs.

Usage:
  python3 ingest_manual.py --list          List manuals and extraction status
  python3 ingest_manual.py "Volo's Guide"  Deep-sweep a specific manual
  python3 ingest_manual.py --all           Process all un-extracted manuals
  python3 ingest_manual.py --merge         Rebuild merged data from extractions

For each manual this:
  1. Extracts full text via pdftotext (cached)
  2. Chunks into LLM-friendly sections (~8000 chars)
  3. Sends each chunk to Gemini/Ollama to extract ALL data types at once
  4. Validates extracted fields against known D&D 5e values
  5. Saves per-manual structured JSON
  6. Merges all manuals' data into app-loadable JSON files

Data extracted: races, subraces, racial traits, spells, magic items,
  equipment, monsters, NPCs, feats, backgrounds, subclasses.
"""

from __future__ import annotations
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from .core import (
    CACHE_DIR,
    CHUNK_CHARS,
    CHUNK_OVERLAP,
    HERE,
    LLM_TIMEOUT,
    MANUALS_DIR,
    OUTPUT_DIR,
    SKIP_EXTRACTION,
    VALID_ABILITIES,
    VALID_RARITIES,
    VALID_SIZES,
    VALID_SKILLS,
    VALID_SPELL_SCHOOLS,
    _LOG_PATH,
    _LOG_PATH,
    _TeeLogger,
    sys,
)
from .notify import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    _load_telegram_env,
    _notify_telegram,
    _send_readout,
)
from .llm import (
    _call_deepseek,
    _call_gemini,
    _call_llm,
    _call_ollama,
    _call_openrouter,
    _load_deepseek_key,
    _load_env_key,
)
from .jsonio import (
    _OCR_FIXES,
    _clean_json_controls,
    _close_truncated_json,
    _extract_json,
    _fix_ocr_artifacts,
    _load_json,
    _load_json_list,
    _save_json,
    _try_parse_json,
)
from .discover import (
    _derive_slug,
    discover_manuals,
)
from .extract import (
    _extract_pdftotext,
    _extract_pymupdf,
    _ocr_pymupdf,
    chunk_text,
    extract_text,
)
from .chunk_extract import (
    EXTRACTION_PROMPT,
    extract_from_chunk,
)
from .race_subclass import (
    RACE_DETAIL_PROMPT,
    SUBCLASS_DETAIL_PROMPT,
    TRAIT_EFFECTS_PROMPT,
    _extract_race_details,
    _extract_subclass_details,
    _merge_race_details,
    _merge_subclass_details,
    _wire_trait_effects,
)
from .validate import (
    _FEATURE_OWNER,
    _empty_result,
    _normalize_ability_key,
    _strip_wrong_features,
    validate_extraction,
)
from .dedup import (
    _QUALITY_KEYWORDS,
    _QUALITY_PENALTY,
    _dedup_within_extraction,
    _load_base_names,
    _normalize_name,
    _pick_better,
    _quality_score,
    _structural_bonus,
    dedup_extraction,
)
from .chapters import (
    CHAPTER_OVERRIDES,
    _detect_chapters,
    _detect_from_text,
    _detect_from_toc,
    _extract_chapter_text,
    _split_by_page,
)
from .process import (
    _check_chapter_quality,
    _print_chapter_readout,
    _process_chapter,
    process_manual,
)
from .merge import (
    _MERGE_FORCE,
    _apply_post_merge_fixups,
    _normalize_merged_sources,
    list_manuals,
    main,
    merge_all_extractions,
)
from .pdfmap import (
    _build_pdf_map,
)
