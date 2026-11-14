"""Reading route source when a route module is a package.

The sheet and the DM tool were each one large module; they are now packages of
sub-modules. Source-scanning regression tests still want to read "the sheet's
code", so this resolves a repo-relative path to every .py file that makes it up:
a plain module resolves to itself, a package resolves to its whole tree.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def route_sources(*rels: str) -> list[tuple[str, str]]:
    """[(repo-relative name, text)] for each file behind the given paths."""
    found: list[tuple[str, str]] = []
    for rel in rels:
        path = ROOT / rel
        if not path.exists() and rel.endswith(".py"):
            # "routes/dm.py" is now the package "routes/dm/"
            path = ROOT / rel[:-3]
        if path.is_dir():
            for f in sorted(path.rglob("*.py")):
                found.append((str(f.relative_to(ROOT)), f.read_text()))
        else:
            found.append((rel, path.read_text()))
    return found


def route_source(rel: str) -> str:
    """The concatenated source behind one route module or package."""
    return "\n".join(text for _, text in route_sources(rel))
