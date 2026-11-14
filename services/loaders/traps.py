"""Manual traps -> MANUAL_TRAPS."""

from __future__ import annotations

from services.loaders.files import _load_manual_json

__all__ = ["merge_manual_traps"]


def merge_manual_traps() -> None:
    """Load traps.json into the manual trap registry."""
    from core.data import MANUAL_TRAPS

    manual_traps = _load_manual_json("traps.json")
    if manual_traps:
        MANUAL_TRAPS.clear(); MANUAL_TRAPS.extend(manual_traps)
        print(f"  + Traps: {len(manual_traps)}")
