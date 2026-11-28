from __future__ import annotations
from .discover import discover_manuals


def _build_pdf_map() -> dict:
    """Build a mapping of book slug → display name + PDF path for downstream tooling."""
    manuals = discover_manuals()
    pdf_map = {}
    for m in manuals:
        pdf_map[m["slug"]] = {
            "title": m["title"],
            "filename": m["filename"],
            "path": m["path"],
        }
    return pdf_map
