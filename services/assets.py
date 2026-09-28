"""Writer for generated static assets — the /static/<name>?v=<hash> files.

Every generated asset is referenced with a content hash in the query string, so the version
must be read from the file AFTER it is written. main.static_asset_version() memoizes per
process; a process only ever generates one version of a given asset, and the file can only
change under a running process via a deploy (which restarts it), so memoizing is safe here —
but do NOT use this for a file whose bytes change within one process.
"""

from pathlib import Path


def _static_dir() -> Path:
    from main import STATIC  # late: main imports the route modules

    return STATIC


def write_static_asset(name: str, body: str) -> str:
    """Write static/<name> when the bytes differ, then return its ?v= version."""
    from main import static_asset_version  # late: main imports the route modules

    try:
        target = _static_dir() / name
        if not target.exists() or target.read_text() != body:
            target.write_text(body)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[static-asset] {name} generation failed: {exc}")
        return "0"
    return static_asset_version(name)
