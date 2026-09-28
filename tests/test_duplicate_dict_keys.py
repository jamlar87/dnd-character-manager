"""Duplicate dict keys are a silent override, and this codebase has shipped one twice.

Python keeps the LAST value for a repeated key in a dict literal, records nothing, and
raises nothing. The damage shows up as a fix that "did not take": main.py's source-alias
table carried "chapter 2 | dungeon master's tools" -> XGE (the correct book) in one block
and the same key -> DMG in a later block, so the DMG copy won and the XGE fix was dead on
arrival. pyflakes DOES see this class (`dictionary key 'x' repeated with different
values`) but every standing sweep here pipes through `grep "undefined name"`, which
filters the warning away.

Two guards, both cheap:
  * no dict literal anywhere in the app repeats a key (this file);
  * the DM-tools chapter aliases point at the book that actually prints that chapter
    title, so the specific regression cannot come back quietly.
"""
import ast
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent

# App code only: a duplicate key in a build script is dead weight, not a runtime override,
# but it is fixed here too so the sweep stays simple to read.
TARGETS = ("data.py", "main.py", "routes", "services", "scripts")


def _py_files():
    for name in TARGETS:
        path = REPO / name
        if path.is_file():
            yield path
        elif path.is_dir():
            yield from sorted(path.rglob("*.py"))


def _duplicate_keys(text: str):
    """(key, first_line, later_line, values_differ) for every repeated dict-literal key."""
    found = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Dict):
            continue
        seen = {}
        for key_node, value_node in zip(node.keys, node.values):
            if not isinstance(key_node, ast.Constant):
                continue
            key = repr(key_node.value)
            try:
                value = ast.literal_eval(value_node)
            except Exception:
                value = ast.unparse(value_node)
            if key in seen:
                found.append((key, seen[key][0], key_node.lineno, value != seen[key][1]))
            else:
                seen[key] = (key_node.lineno, value)
    return found


def test_no_duplicate_dict_keys():
    bad = []
    for path in _py_files():
        for key, first, later, differs in _duplicate_keys(path.read_text()):
            where = f"{path.relative_to(REPO)}:{later} {key} repeats the key on line {first}"
            bad.append(where + (" with a DIFFERENT value" if differs else ""))
    assert bad == [], "duplicate dict keys — the later entry silently wins: " + "; ".join(bad)


def _chapter_aliases() -> dict:
    src = (REPO / "main.py").read_text()
    for node in ast.walk(ast.parse(src)):
        value = getattr(node, "value", None)
        if (
            isinstance(node, ast.Assign)
            and getattr(node.targets[0], "id", "") == "_chapter_aliases"
            and isinstance(value, ast.Dict)
        ):
            return {k.value: ast.literal_eval(v) for k, v in zip(value.keys, value.values)}
    raise AssertionError("_chapter_aliases not found in main.py")


def test_dm_tools_chapter_aliases_name_the_book_that_prints_that_chapter():
    """XGE Ch2 and Tasha's (DTCOE) Ch4 are both titled "Dungeon Master's Tools"; the DMG
    has no chapter by that name at all (its Ch2 is "Creating a Multiverse", Ch4 "Creating
    Nonplayer Characters"). Verified against the book text in data/manual_cache/*.txt, which
    is also what the test re-checks when the caches are present.
    """
    aliases = _chapter_aliases()
    expected = {
        "chapter 2 | dungeon master's tools": "XGE",
        "chapter 2 dungeon master's tools": "XGE",
        "dungeon master's tools p.?": "XGE",
        "chapter 4 | dungeon master's tools": "DTCOE",
    }
    for key, slug in expected.items():
        assert aliases.get(key, {}).get("slug") == slug, f"{key!r} -> {aliases.get(key)}"

    dmg = REPO / "data" / "manual_cache" / "DMG.txt"
    if dmg.exists():
        text = dmg.read_text(errors="ignore")
        assert not re.search(r"dungeon master'?s tools", text, re.I), \
            "the DMG cache now carries a 'Dungeon Master's Tools' chapter — re-check the aliases"


def test_no_duplicate_name_overrides_in_reference_art():
    """services/ref_portraits._NAME_OVERRIDES decides the noun a reference image is drawn
    from; a repeated key means one of the two nouns never reaches a prompt."""
    src = (REPO / "services" / "ref_portraits.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        value = getattr(node, "value", None)
        if (
            isinstance(node, ast.Assign)
            and any(getattr(t, "id", "") == "_NAME_OVERRIDES" for t in node.targets)
            and isinstance(value, ast.Dict)
        ):
            keys = [k.value for k in value.keys]
            dupes = sorted({k for k in keys if keys.count(k) > 1})
            assert dupes == [], f"_NAME_OVERRIDES repeats these names: {dupes}"
            return
    raise AssertionError("_NAME_OVERRIDES not found in services/ref_portraits.py")
