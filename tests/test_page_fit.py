"""Every cited page must exist in the book it names.

This is the same invariant as test_manual_sources.py::test_page_numbers_within_range, but measured
against the real PDFs instead of a hand-kept table — which is how 141 records citing a 7-page book
with pages 10-168 went unnoticed: the audit compared them against the cached text and reported
"cannot verify", and the table had no entry for the slug. The slug was simply the wrong one.

Skips cleanly when the manuals tree or pymupdf is unavailable.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "scripts"))


def test_every_cited_page_exists_in_its_book():
    page_fit = pytest.importorskip("page_fit")
    counts = page_fit.pdf_page_counts()
    if not counts:
        pytest.skip("no PDF page counts available (manuals tree or pymupdf missing)")
    rows = page_fit.violations(counts)
    assert rows == [], (
        "citations name a page that cannot exist in their book — the slug is almost always the "
        "wrong one, so re-attribute rather than editing the page:\n  " + "\n  ".join(rows[:10])
    )
