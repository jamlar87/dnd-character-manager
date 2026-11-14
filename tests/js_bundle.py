"""Read a front-end bundle's whole source, as the browser would.

`static/sheet.js` and `static/dm_tools.js` outgrew single files: each is now an entry
script plus ordered parts that load right after it over shared window scope (see
SHEET_JS_PARTS / DM_TOOLS_JS_PARTS in core/http.py). Text-guard tests care about the
code as a whole — the browser concatenates it — so read the bundle, not one part, or a
guard silently stops covering most of the feature.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # tests usually run from the app root, but be safe
    sys.path.insert(0, str(ROOT))

from core.http import DM_TOOLS_JS_PARTS, SHEET_JS_PARTS  # noqa: E402

STATIC = ROOT / "static"

_BUNDLES = {
    "sheet.js": ("sheet.js", SHEET_JS_PARTS),
    "dm_tools.js": ("dm_tools.js", DM_TOOLS_JS_PARTS),
}


def bundle_files(entry: str) -> tuple[str, ...]:
    """All asset names in the bundle, entry script first."""
    first, parts = _BUNDLES[entry]
    return (first, *parts)


def read_js(entry: str) -> str:
    """Concatenated source of a bundle, separated by newlines."""
    return "\n".join((STATIC / name).read_text() for name in bundle_files(entry))
