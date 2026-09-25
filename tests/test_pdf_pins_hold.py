"""The pinned slug -> file mappings must hold, and must agree with _meta.json.

A manual fold re-serialises pdf_map from the ingest engine's own view, which maps WLL onto W8's
7-page file and renames the title to match. repair_manual_paths re-asserts the pins after each
fold, but nothing ran it in between — so the wrong entry sat there silently. These assertions are
the guard: if a fold re-breaks a pin, the suite fails instead of the front end 404ing later.

A path that exists is not evidence it is the right book, so the third check compares the file's
real page count against the highest page marker in the slug's own cached text.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

HERE = pathlib.Path(__file__).resolve().parent.parent
META = HERE / "data" / "manual_data" / "_meta.json"
PINS = HERE / "data" / "manual_data" / "_pdf_pins.json"
CACHE = HERE / "data" / "manual_cache"


def _pins() -> dict:
    if not PINS.exists():
        pytest.skip("no pins file")
    return {k: v for k, v in json.loads(PINS.read_text()).items() if not k.startswith("_")}


def test_pin_matches_the_live_pdf_map():
    """The whole point: the fold's own view must not win over a hand-verified mapping."""
    pm = json.loads(META.read_text())["pdf_map"]
    drift = []
    for slug, pin in _pins().items():
        live = pm.get(slug) or {}
        for field, want in pin.items():
            if live.get(field) != want:
                drift.append(f"{slug}.{field}: {live.get(field)!r} != {want!r}")
    assert not drift, ("a fold re-pointed a pinned slug:\n  " + "\n  ".join(drift) +
                       "\nrun: .venv/bin/python3 scripts/repair_manual_paths.py --apply")


def test_pinned_files_exist_and_hold_the_book():
    for slug, pin in _pins().items():
        path = HERE / "manuals" / pin["path"]
        assert path.exists(), f"{slug}: pinned file is missing: {pin['path']}"
        cache = CACHE / f"{slug}.txt"
        if not cache.exists():
            continue
        marks = [int(m.group(1)) for m in
                 re.finditer(r"---\s*PAGE\s+(\d+)", cache.read_text(errors="replace"))]
        if not marks:
            continue
        fitz = pytest.importorskip("fitz")
        doc = fitz.open(path)
        try:
            assert doc.page_count >= max(marks) - 2, (
                f"{slug}: cache cites p.{max(marks)} but {pin['filename']} has "
                f"{doc.page_count} pages — this is what a shared filename looks like")
        finally:
            doc.close()
