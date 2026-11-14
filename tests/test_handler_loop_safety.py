"""Loop-safety guards.

The app is a single uvicorn worker: any `async def` handler that does no awaiting runs its sqlite
work directly on the event loop and stalls every other request (measured: 8 concurrent /dm-tools
made /api/health take 1.9s). Handlers therefore stay plain `def` (Starlette threadpool) unless they
genuinely await, and this test fails if one drifts back.
"""
import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP = {"lifespan", "global_exception_handler", "bad_json_body_handler"}


def _handlers():
    files = [ROOT / "main.py"] + sorted((ROOT / "routes").rglob("*.py"))
    for path in files:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and not node.name.startswith("_"):
                yield path.relative_to(ROOT), node


def test_no_async_handler_without_await():
    offenders = []
    for rel, node in _handlers():
        if node.name in SKIP:
            continue
        has_async_op = any(
            isinstance(n, (ast.Await, ast.Yield, ast.YieldFrom, ast.AsyncFor, ast.AsyncWith))
            for n in ast.walk(node)
        )
        if not has_async_op:
            offenders.append(f"{rel}:{node.lineno} async def {node.name}")
    assert not offenders, (
        "these handlers are async but never await, so they block the event loop; "
        "make them plain def:\n" + "\n".join(offenders)
    )


def test_docs_and_openapi_are_not_public():
    import main
    assert main.app.docs_url is None
    assert main.app.redoc_url is None
    assert main.app.openapi_url is None


def test_sync_combat_hp_does_not_block_the_loop():
    """The batch HP poll must hand its sqlite loop to a worker thread."""
    src = (ROOT / "routes/characters/sheet.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "sync_combat_hp")
    calls = {n.func.attr for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "run_sync" in calls, "sync_combat_hp must call anyio to_thread.run_sync"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
