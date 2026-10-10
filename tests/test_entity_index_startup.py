"""The entity index must be buildable before the manual merge has finished.

`_build_index()` reads `main.ITEM_INDEX`, which `load_manual_data()` is still
filling on a cold start, and iterating a dict mid-merge raises
"dictionary changed size during iteration". The web app never saw it because it
warms the index after the merge — but a probe or tool whose first act is
`from services.entity_search import ...` crashed outright.

A fresh interpreter is required: this is an import-order bug, so it cannot be
reproduced once the test session's `main` has been imported and merged.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

PROBE = """
import sys
sys.path.insert(0, {repo!r})
from services import entity_search as es
rows = es._get_index()
assert len(rows) > 1000, f"only {{len(rows)}} rows"
assert es._get_index() is rows, "index is not cached"
print("ROWS", len(rows))
"""


def test_index_builds_before_the_manual_merge_has_run():
    r = subprocess.run([sys.executable, "-c", PROBE.format(repo=str(REPO))],
                       capture_output=True, text=True, cwd=str(REPO), timeout=300)
    assert r.returncode == 0, f"probe failed:\n{r.stdout[-1500:]}\n{r.stderr[-2500:]}"
    assert "ROWS" in r.stdout, r.stdout[-1000:]
