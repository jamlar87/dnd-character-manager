"""Import-hygiene guard: `core/` must never depend on `main`, and nothing under
routes/ or services/ may reach back into main.py.

Why: main.py builds the app and then imports the route modules at the bottom.
Every `from main import X` in a route/service made that a cycle — importing any
single route module dragged in app creation, the whole data corpus and every
other route. The shared helpers now live in `core/` (which imports only stdlib,
third-party packages, `data` and `services/`), and main re-exports them so
`from main import X` keeps working for tests.

These tests fail at import-collection time if the cycle comes back.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT

# Modules that used to import main and must stand alone.
STANDALONE_MODULES = [
    "routes.dm",
    "routes.maps",
    "routes.characters.all",
    "services.combat",
    "pdf_generator",
]

# `from main import ...` here is legitimate only for the app object / TestClient
# wiring. Keep this list at 0-2 justified entries.
MAIN_IMPORT_ALLOWLIST: dict[str, str] = {}


def _run_probe(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(HERE),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "HOME": "/tmp"},
    )


@pytest.mark.parametrize("module", STANDALONE_MODULES)
def test_module_imports_without_main(module: str) -> None:
    code = (
        "import sys, importlib\n"
        f"importlib.import_module({module!r})\n"
        "assert 'main' not in sys.modules, 'importing %s pulled in main' % "
        f"{module!r}\n"
    )
    proc = _run_probe(code)
    assert proc.returncode == 0, (
        f"standalone import of {module} failed:\n{proc.stdout}\n{proc.stderr}"
    )


def test_core_never_imports_main() -> None:
    """`import core.<mod>` must not load main, and no core file may import it."""
    offenders: list[str] = []
    for path in sorted((HERE / "core").glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "main" or alias.name.startswith("main."):
                        offenders.append(f"{path.name}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                # level 0 == absolute import
                if node.level == 0 and node.module and (
                    node.module == "main" or node.module.startswith("main.")
                ):
                    offenders.append(f"{path.name}:{node.lineno} from {node.module} import ...")
    assert not offenders, "core/ must not import main:\n" + "\n".join(offenders)

    code = (
        "import sys\n"
        "import core.config, core.db, core.security, core.http, core.data, "
        "core.features, core.constants\n"
        "assert 'main' not in sys.modules, 'importing core pulled in main'\n"
    )
    proc = _run_probe(code)
    assert proc.returncode == 0, (
        f"core modules are not main-free:\n{proc.stdout}\n{proc.stderr}"
    )


def test_no_from_main_imports_in_routes_or_services() -> None:
    """No `from main import ...` under routes/ or services/ outside the allowlist."""
    offenders: list[str] = []
    for sub in ("routes", "services"):
        for path in sorted((HERE / sub).rglob("*.py")):
            rel = path.relative_to(HERE).as_posix()
            if rel in MAIN_IMPORT_ALLOWLIST:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "main" or alias.name.startswith("main."):
                            offenders.append(f"{rel}:{node.lineno} import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and node.module and (
                        node.module == "main" or node.module.startswith("main.")
                    ):
                        offenders.append(f"{rel}:{node.lineno} from {node.module} import ...")
    assert not offenders, (
        "routes/ and services/ must import from core/, not main:\n" + "\n".join(offenders)
    )
    assert len(MAIN_IMPORT_ALLOWLIST) <= 2, "allowlist is capped at 2 justified entries"


def test_pdf_generator_does_not_import_main() -> None:
    path = HERE / "pdf_generator.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names if a.name.split(".")[0] == "main"]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and node.module.split(".")[0] == "main":
                offenders.append(node.module)
    assert not offenders, f"pdf_generator.py must not import main: {offenders}"
