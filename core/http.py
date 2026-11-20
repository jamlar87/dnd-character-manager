"""Jinja environment, the HTML render helper and static-asset cache busting."""
from __future__ import annotations

import hashlib

from fastapi import Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader

from core.config import STATIC, TEMPLATES
from core.security import get_current_user

# autoescape is ON (Jinja's default is OFF, which is how a character named
# `"><img src=x onerror=...>` executed in every other account's dashboard — the shared
# characters block renders other users' names). Templates that must emit markup say so
# with `|safe`; `|tojson` already escapes for an HTML context.
_jinja = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)

_STATIC_ASSET_VERSIONS: dict[str, str] = {}

# A few front-end features outgrew one file but still load as ordered classic scripts
# sharing window scope (functions cross-reference across the whole bundle, and the
# templates were written against that). The entry file stays the same name so templates
# and tests keep addressing /static/sheet.js; the parts load right after it. A bundle's
# ?v= is the hash of every part, so editing any part busts the cache for all of them.
SHEET_JS_PARTS: tuple[str, ...] = (
    "sheet/status.js",
    "sheet/spells.js",
    "sheet/popups.js",
    "sheet/edit.js",
    "sheet/features.js",
    "sheet/inventory.js",
    "sheet/equip.js",
    "sheet/inventory-ui.js",
    "sheet/levelup.js",
    "sheet/story.js",
    "sheet/summons.js",
    "sheet/live.js",
)

DM_TOOLS_JS_PARTS: tuple[str, ...] = (
    "dm/reference.js",
    "dm/encounters.js",
    "dm/maps.js",
    "dm/encounter-open.js",
    "dm/npcs.js",
    "dm/traps.js",
    "dm/campaigns.js",
    "dm/items.js",
    "dm/loot.js",
    "dm/combat.js",
    "dm/combat-ui.js",
    "dm/combat-conditions.js",
    "dm/builder.js",
    "dm/rollfeed.js",
)


def static_asset_version(filename: str) -> str:
    """Content hash of a static asset, for ?v= cache busting.

    Hand-bumped ?v=N numbers go stale the moment someone edits the file; a hash
    of the bytes on disk can't. Memoized per process — assets only change on
    deploy, which restarts the process. Lives here so the sheet routes and the
    DM tools routes share one implementation.
    """
    if filename not in _STATIC_ASSET_VERSIONS:
        try:
            digest = hashlib.sha1((STATIC / filename).read_bytes()).hexdigest()[:10]
        except Exception:  # pragma: no cover - defensive
            digest = "1"
        _STATIC_ASSET_VERSIONS[filename] = digest
    return _STATIC_ASSET_VERSIONS[filename]


def static_bundle_version(entry: str, parts: tuple[str, ...]) -> str:
    """Content hash over an entry script and the parts that load after it."""
    key = "\x00".join((entry, *parts))
    if key not in _STATIC_ASSET_VERSIONS:
        digest = hashlib.sha1()
        for name in (entry, *parts):
            try:
                digest.update((STATIC / name).read_bytes())
            except Exception:  # pragma: no cover - defensive
                digest.update(b"missing")
        _STATIC_ASSET_VERSIONS[key] = digest.hexdigest()[:10]
    return _STATIC_ASSET_VERSIONS[key]



# Hand-pinned ?v= numbers go stale silently: the file changes, the URL does not, so
# the browser AND Cloudflare keep serving the old script — that is how char-portrait.js
# sat at ?v=1 across several edits and the new tile never reached a browser. Templates
# call static_asset_version('file.js'), which hashes the content.
_jinja.globals["static_asset_version"] = static_asset_version

# Templates used to hand-roll `{% set mod = (character[stat] - 10) // 2 %}` in a
# dozen includes; one filter instead, sharing services/rules.ability_mod.
from services.rules import ability_mod as _ability_mod

_jinja.filters["ability_mod"] = _ability_mod

def _render(template: str, request: Request | None = None, **ctx) -> HTMLResponse:
    user = get_current_user(request) if request else None
    content = _jinja.get_template(template).render(
        user=user,
        request=request,
        **ctx
    )
    return HTMLResponse(content)
