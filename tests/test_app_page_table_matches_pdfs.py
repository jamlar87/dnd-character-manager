"""The app's citation validator must not under-count a book's pages.

services/data_loader.py keeps its own `max_pages` table, separate from the test suite's
PDF_PAGE_RANGES. Both drifted from the PDFs: the app said "W max 9" for a 30-page file, so ten
legitimate records were flagged as out of range and the reference reported them as suspect — on the
front end, as a warning nobody would notice. This ties the app's table to the PDFs themselves.

A table that is too LARGE is harmless (it lets a bad citation through); too SMALL flags good data.
Only the small direction is asserted.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "scripts"))


def _app_table() -> dict[str, int]:
    src = (HERE / "services" / "data_loader.py").read_text()
    block = re.search(r"max_pages = \{(.*?)\n    \}", src, re.S).group(1)
    return {k: int(v) for k, v in re.findall(r'"([A-Z0-9]+)"\s*:\s*(\d+)', block)}


def test_app_page_table_is_never_smaller_than_the_real_pdf():
    import page_fit

    meta = json.loads((HERE / "data" / "manual_data" / "_meta.json").read_text())
    real = page_fit.pdf_page_counts(meta.get("pdf_map", {}))
    if not real:
        return  # no PDFs on this machine — nothing to compare against
    app = _app_table()
    assert app, "could not parse max_pages from services/data_loader.py"
    short = {s: (app[s], n) for s, n in real.items() if s in app and app[s] < n}
    assert not short, f"app max_pages under-counts these books: {short}"
