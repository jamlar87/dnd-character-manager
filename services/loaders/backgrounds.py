"""Manual backgrounds -> BACKGROUNDS / BACKGROUND_SOURCES."""

from __future__ import annotations

from services.loaders.files import _load_manual_json

__all__ = ["merge_manual_backgrounds"]


def merge_manual_backgrounds() -> None:
    """Append backgrounds.json to the background list."""
    from core.data import BACKGROUND_SOURCES, BACKGROUNDS, _background_page_map

    # ── Backgrounds ── append to BACKGROUNDS list
    manual_backgrounds = _load_manual_json("backgrounds.json")
    for bg in manual_backgrounds:
        name = bg.get("name", "")
        if name and name not in BACKGROUNDS:
            BACKGROUNDS.append(name)
    if manual_backgrounds:
        print(f"  + Backgrounds: {len(manual_backgrounds)}")
        # Enrich manual background sources
        _bg_added = 0
        for bg in manual_backgrounds:
            name = bg.get("name", "")
            if name and name not in BACKGROUND_SOURCES:
                _mapped = _background_page_map.get(name)
                if _mapped:
                    BACKGROUND_SOURCES[name] = _mapped
                    _bg_added += 1
        if _bg_added:
            print(f"  Manual background sources enriched: {_bg_added}")
