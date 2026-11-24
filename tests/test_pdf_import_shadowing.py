"""Guard: routes/characters/pdf.py must not put its own directory on sys.path.

That file is named ``pdf.py``. Inserting ``routes/characters`` into sys.path from
the handler made ``import pdf`` (used by pdf_generator) resolve to the route
module instead of the real ``pdf/`` package, so the first PDF request in a fresh
process died with ``ImportError: cannot import name '_FONTS_DIR' from 'pdf'``.

test_pdf_generation.py could not catch it: it imports pdf_generator at module
scope, so ``sys.modules['pdf']`` is already the real package by the time the
route test runs. The detector therefore has to be a source guard.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROUTE = REPO / "routes" / "characters" / "pdf.py"


def test_route_does_not_insert_own_dirname_on_sys_path():
    src = ROUTE.read_text()
    assert 'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))' not in src
    assert "repo_root" in src, "route must add the repo root (pdf/ package lives there)"
    assert "os.path.dirname(os.path.dirname(os.path.dirname(" in src


def test_pdf_package_is_not_shadowed_by_route_module():
    # A route module named pdf.py is only dangerous via sys.path; confirm the
    # package still wins from the repo root.
    import pdf

    assert hasattr(pdf, "_FONTS_DIR")
    assert Path(pdf.__file__).parent.name == "pdf"
