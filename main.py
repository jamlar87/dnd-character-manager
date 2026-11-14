"""D&D Character Manager — FastAPI webapp with multi-user character tracking."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import hashlib
import secrets
from contextlib import asynccontextmanager
from pathlib import Path


from core.config import (  # paths + .env load live in core/config.py
    HERE, DATA_DIR, PAGE_MAP_DIR, DB_PATH, TEMPLATES, STATIC,
    SECRET_KEY, APP_ENV, SESSION_TTL_DAYS, SRD_CACHE, MANUAL_DATA,
)

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from jinja2 import Environment, FileSystemLoader

from data import (
    ABILITY_NAMES, SKILL_ABILITIES, ALL_SKILLS,
    BACKGROUNDS, BACKGROUND_INFO, ALIGNMENTS, DRACONIC_ANCESTRIES,
    STARTING_EQUIPMENT, ASI_LEVELS,
    RACES, CLASSES, RACE_NAMES,
    FEATS, FEAT_BY_NAME,
    FEATURE_DESCRIPTIONS, FEATURE_ACTION_TYPES,
    FULL_CASTERS, HALF_CASTERS, PACT_CASTERS, PREPARED_CASTERS, SPELLS_KNOWN_CASTERS,
    SUBCLASS_LEVELS, SUBCLASS_FEATURES, SUBCLASS_FEATURE_REPLACEMENTS,
    LIMITED_USE, METAMAGIC_OPTIONS, METAMAGIC_LEVELS, METAMAGIC_PICKS,
    INVOCATION_LEVELS, INVOCATION_PICKS, INVOCATION_OPTIONS,
    PACT_BOON_OPTIONS, PACT_BOON_LEVELS,
    MANEUVER_LEVELS, MANEUVER_OPTIONS,
    TOTEM_SPIRIT_OPTIONS, HUNTERS_PREY_OPTIONS,
    FAVORED_ENEMY_OPTIONS, FAVORED_TERRAIN_OPTIONS,
    INFUSION_OPTIONS,
    MULTICLASS_PREREQS, MULTICLASS_PROFICIENCIES,
    EXPERTISE_LEVELS, RACIAL_TRAIT_DESCS,
    RECOMMENDED_FEATS, SCALED_EQUIPMENT,
)

# ── Item helpers (extracted to services/items.py) ─────────────────────────
from services.items import (
    _resolve_item_key, _split_curse_text, _build_item_description,
    _build_item_type, _resolve_source, _extract_srd_dice,
    _item_rarity_for_level,
)

# ── Combat / racial-trait helpers (extracted to services/combat.py) ────────
from services.combat import (
    get_racial_trait_effects, _build_racial_traits, _subrace_traits, _parse_enhancement,
    _build_inventory_attacks,
    _build_character_attacks, _build_charged_item_attacks, _normalize_equipped, _get_named_item_types, _equipped_names, get_character_armor_profs, _resolve_armor_item,
    check_armor_proficiency_from_set,
)


# ── Shared helpers/constants (extracted to core/, which never imports main) ─
# Re-exported here so `from main import X` keeps working for tests and callers.
from core.security import (
    _get_user, _get_user_by_token, _hash, _is_admin, _verify,
    get_current_user, require_user,
)
from core.http import (
    _render,
)
from core.db import (
    get_db,
)
from core.data import (
    ITEM_INDEX, MANUALS_BASE, MANUAL_TRAPS, SRD_SPELLS, _get_source_slug_map,
    _manual_titles, _scan_manual_pdfs,
    FLEXIBLE_ASI_RACES, MPMM_ASI_RACES, RICH_RACE_DESCS,
)
from services.schema import (
    HAS_PORTRAIT_SQL, hydrate_json_columns,
)
from services.db_schema import (
    init_db,
)

# ── Data loaders + manual-data merge (extracted to services/data_loader.py) ──
# Imported before SRD_LEVELS is built below; data_loader has no module-level
# main import, so this is safe from circulars.
from services.data_loader import (
    _load_srd_class_data, _load_spell_dice, _load_json_cache, _load_manual_json,
    load_manual_data,
)




# ── App ─────────────────────────────────────────────────────────────────────

# /docs, /redoc and /openapi.json are off: the schema is unauthenticated and enumerates every
# route and parameter shape to anyone who can reach :8300, which is a free map for the IDOR and
# upload probes. Nothing in the app or its tests reads them; re-enable behind auth if ever needed.
app = FastAPI(title="D&D Character Manager", docs_url=None, redoc_url=None, openapi_url=None)

# ── Static files ──────────────────────────────────────────────────────────────
from starlette.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

# ── Logging ───────────────────────────────────────────────────────────────────
import logging as _logging, logging.handlers as _logging_handlers, time as _time, uuid as _uuid

_logging.basicConfig(
    level=_logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        _logging.StreamHandler(),
        _logging_handlers.RotatingFileHandler(
            str(DATA_DIR / "app.log"), maxBytes=5*1024*1024, backupCount=3
        ),
    ],
)
_log = _logging.getLogger(__name__)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    req_id = _uuid.uuid4().hex[:12]
    start = _time.time()
    try:
        response = await call_next(request)
        elapsed = _time.time() - start
        # The client address rides on every line because uvicorn's own access log is disabled
        # (it printed the same request a second time, ~1800 duplicate lines/hour from the
        # combat-HP poll alone). cf-connecting-ip is the real address behind Cloudflare.
        client_ip = (request.headers.get("cf-connecting-ip")
                     or (request.headers.get("x-forwarded-for", "").split(",")[0].strip())
                     or (request.client.host if request.client else "-"))
        _log.info("%s %s %s %.0fms ip=%s", request.method, request.url.path, response.status_code, elapsed * 1000, client_ip)
        response.headers.setdefault("X-Request-ID", req_id)
        return response
    except Exception as e:
        elapsed = _time.time() - start
        _log.error("%s %s 500 %.0fms | %s: %s", request.method, request.url.path, elapsed * 1000, type(e).__name__, e, exc_info=True)
        return HTMLResponse("Internal Server Error", status_code=500, headers={"X-Request-ID": req_id})


# Native form posts that must never be blocked by the CSRF header requirement.
# See security_middleware: a browser cannot set x-csrf-token on a form submit.
#
# "/change-password" belongs here for the same reason as the three above: its template is a
# native <form method="post">, and the CSRF glue in static/layout.js only patches
# window.fetch, which a native form submit never uses. Without this entry the page answered
# 403 for every logged-in user — verified against this app before fixing. The same-origin
# Origin check in security_middleware still applies to it (and to these paths) whenever the
# request carries a session cookie, so the exemption is not a cross-site hole for a
# logged-in victim; the three auth entry points above are reachable cookie-less by design.
CSRF_FORM_POST_PATHS = {"/login", "/register", "/reset-password", "/change-password"}


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Apply security headers and protect cookie-authenticated writes."""
    csrf_cookie = request.cookies.get("csrf_token")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.cookies.get("dnd_token"):
        # Same-origin is checked for every write, including the form posts below:
        # that is the part of CSRF defence that actually applies to them.
        origin = request.headers.get("origin")
        if origin:
            expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if origin.rstrip("/") != expected.rstrip("/"):
                return JSONResponse({"error": "Cross-origin request rejected"}, status_code=403)
        # Auth entry points are native <form> posts, and a browser cannot attach
        # x-csrf-token to one. They must also stay reachable while a *stale*
        # dnd_token cookie is still in the browser: a cookie whose session row is
        # gone used to make the login form itself answer 403 forever, so the user
        # could never log back in without clearing cookies by hand.
        if request.url.path not in CSRF_FORM_POST_PATHS:
            supplied = request.headers.get("x-csrf-token", "")
            if not csrf_cookie or not supplied or not secrets.compare_digest(csrf_cookie, supplied):
                return JSONResponse({"error": "CSRF token required"}, status_code=403)
    response = await call_next(request)
    # Static assets: skip the CSRF cookie and mark them cacheable. Cloudflare
    # only caches responses WITHOUT Set-Cookie, so the cookie here was forcing
    # cf-cache-status: BYPASS on every sheet.js / sheet.css request — the whole
    # asset went to the origin each time. Assets are version-busted with ?v=
    # (content hash for the sheet assets), so a 4h TTL is safe.
    # Reference art is the same kind of thing as /static/: shared by every
    # user, never changes for a given entity. A cookie here means CF BYPASS.
    if request.url.path.startswith(("/static/", "/api/ref-image/")) or (
            request.url.path.startswith("/api/dm/map/") and request.url.path.endswith("/thumb")):
        # Reference art is REWRITTEN IN PLACE behind a stable URL: the filename is a slug derived from
        # the record name, so regenerating a portrait does not change its address. A long max-age
        # therefore serves stale art to the browser and to Cloudflare for hours (measured: a second
        # request returned cf-cache-status: HIT on a just-regenerated file, and max-age was 4 hours).
        # Short max-age plus must-revalidate means a conditional request, so an unchanged file still
        # answers 304 and costs almost nothing.
        response.headers.setdefault("Cache-Control", "public, max-age=300, must-revalidate")
    elif request.url.path == "/api/health":
        # A liveness probe has no browser and no forms, so the CSRF cookie it used to mint was
        # pure noise — and a Set-Cookie makes a response uncacheable for Cloudflare, which would
        # freeze a stale "degraded" verdict if anything ever cached it. no-store states the
        # intent: a health answer is only true for the moment it was measured.
        response.headers.setdefault("Cache-Control", "no-store")
    elif not csrf_cookie:
        response.set_cookie("csrf_token", secrets.token_urlsafe(32), httponly=False, secure=APP_ENV in {"production", "prod"}, samesite="lax", path="/")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    return response

# ── Compression ───────────────────────────────────────────────────────────────
# The front-end ships ~600KB of un-minified JS across the sheet and dm-tools bundles and the
# origin sent it raw: `curl -I /static/sheet.js` answered content-length 324864 with no
# Content-Encoding. Cloudflare compresses for the browser, so the cost landed on LAN clients
# and on every cache-MISS fetch, where the whole payload crossed the wire.
#
# Range requests are excluded on purpose: a 206 carries byte offsets into the UNCOMPRESSED
# body, so compressing it would hand the client a file the range does not describe.
# text/event-stream is already excluded inside Starlette (the live table channel is SSE).
class _CompressibleGZip(GZipMiddleware):
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and Headers(scope=scope).get("range"):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


# Last added = outermost, so this wraps the security middleware and the request logger too.
app.add_middleware(_CompressibleGZip, minimum_size=1024, compresslevel=6)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    _log.error("Unhandled %s on %s %s: %s", type(exc).__name__, request.method, request.url.path, exc, exc_info=True)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Internal server error"}, status_code=500)
    return HTMLResponse("Internal Server Error", status_code=500)


@app.exception_handler(json.JSONDecodeError)
async def bad_json_body_handler(request: Request, exc: json.JSONDecodeError):
    """A missing or malformed JSON body is a bad request, not a server fault.

    There are ~80 `await request.json()` call sites in this app and most of them do not guard the
    call, so a body-less POST (curl, a stale page, a script) was a 500 with a traceback — the player
    key route was still doing it. Guarding every call site is a rewrite; one handler is the root
    fix, and it means a future route inherits the right behaviour instead of repeating the bug.
    """
    _log.info("Bad JSON body on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse({"error": "expected a JSON body"}, status_code=400)







# ── Health ────────────────────────────────────────────────────────────────────
# No monitoring endpoint existed: GET /api/health answered 404, so an uptime check had to
# fetch `/` and infer health from a 200. This answers the one question that matters — is the
# process up and can it reach its database — without touching auth or user data.

_APP_STARTED = _time.time()


@app.get("/api/health", response_class=JSONResponse)
def api_health():
    db_state, status = "ok", 200
    try:
        conn = get_db()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
    except Exception as exc:                       # noqa: BLE001 - any failure means degraded
        db_state, status = f"{type(exc).__name__}: {exc}", 503
    return JSONResponse(
        {"status": "ok" if status == 200 else "degraded",
         "db": db_state,
         "uptime_s": int(_time.time() - _APP_STARTED)},
        status_code=status,
    )


# ── Routes: Landing ───────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/dashboard", 303)
    # What is actually in the library, for the landing page's "what's inside" strip. These are the
    # same cached accessors the DM tools use (load_manual_data() populates a cache and returns
    # None, it does not hand back the data). A failure just means the strip is not shown.
    counts = {}
    try:
        from routes.characters import _load_monster_cache   # local: avoids an import cycle
        counts = {
            "monsters": len(_load_monster_cache() or []),
            "spells": len(SRD_SPELLS or []),
            "races": len(_load_manual_json("races.json") or []),
            "items": len(_load_manual_json("magic_items.json") or [])
                     + len(_load_manual_json("equipment.json") or []),
        }
    except Exception as exc:      # never break the landing page for a decoration
        print(f"[landing] library counts unavailable: {type(exc).__name__}: {exc}", flush=True)
        counts = {}
    return _render("landing.html", request=request, library=counts)

# ── Routes: Dashboard ───────────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = require_user(request)
    db = get_db()
    # An admin CAN see every character in the database, but the dashboard is their own workspace
    # first: defaulting to everyone's buried the owner's 16 characters under 9 strangers', which is
    # what an admin reported ("I'm seeing ALL characters instead of just my own/public"). Own +
    # shared is now the default for every account; an admin opts into the full list with ?all=1.
    # The link is rendered only for admins, and the parameter is ignored for anyone else.
    admin_all = _is_admin(user) and request.query_params.get("all") in ("1", "true", "yes")
    # Explicit column list: `SELECT c.*` dragged every portrait blob (25 rows, ~20 MB of base64)
    # through SQLite into Python on every dashboard render just to be blanked out below.
    # Inline data: URLs are cut in SQL (substr reads only the 5-char prefix, not the blob);
    # real http(s) URLs still come back so cards can point straight at them, and
    # has_portrait tells the card whether /portrait-image will have something to serve.
    _cols = ("c.id, c.user_id, c.name, c.race, c.subrace, c.class_name, c.subclass, c.level, "
             "c.hp_current, c.hp_max, c.ac, c.background, c.shared, c.created_at, "
             "c.skills, c.features, c.inventory, c.equipped, c.languages, c.conditions, "
             f"{HAS_PORTRAIT_SQL}, "
             "CASE WHEN substr(c.portrait_url,1,5) = 'data:' THEN '' "
             "ELSE COALESCE(c.portrait_url,'') END AS portrait_url")
    if admin_all:
        chars = [dict(r) for r in db.execute(
            f"SELECT {_cols}, u.email as owner_email FROM characters c "
            "LEFT JOIN users u ON c.user_id = u.id "
            "ORDER BY c.shared ASC, c.created_at DESC"
        ).fetchall()]
    else:
        chars = [dict(r) for r in db.execute(
            f"SELECT {_cols}, u.email as owner_email FROM characters c "
            "LEFT JOIN users u ON c.user_id = u.id "
            "WHERE c.user_id = ? OR (c.shared = 1 AND c.user_id != ?) "
            "ORDER BY c.shared ASC, c.created_at DESC",
            (user["id"], user["id"])
        ).fetchall()]
    db.close()
    # Load favorites and sort: favorites first
    try:
        favs = json.loads(user.get("favorites", "[]"))
    except (json.JSONDecodeError, TypeError):
        favs = []
    fav_set = set(int(f) for f in favs if str(f).isdigit())
    # Sort: favorites first, then by creation date descending
    fav_chars = [c for c in chars if c["id"] in fav_set]
    other_chars = [c for c in chars if c["id"] not in fav_set]
    fav_chars.sort(key=lambda c: c.get("created_at", "") or "", reverse=True)
    other_chars.sort(key=lambda c: c.get("created_at", "") or "", reverse=True)
    chars = fav_chars + other_chars
    for c in chars:
        hydrate_json_columns(c)
        # Portraits are multi-MB base64 data URLs; the card only needs to know
        # whether one exists (the tile points at /portrait-image). Keeping the
        # blob out of the template context saves tens of MB per dashboard render.
        # has_portrait now comes from the SELECT itself; only make sure no data: URL
        # can sneak into the template context.
        _p = (c.get("portrait_url") or "").strip()
        c["portrait_url"] = "" if _p.startswith("data:") else _p
    return _render("dashboard.html", request=request, characters=chars, current_user_id=user["id"], favorites=favs,
                   all_classes=sorted({c.get("class_name") for c in chars if c.get("class_name")}),
                   can_see_all=_is_admin(user), admin_all=admin_all)


# ── Toggle favorite ────────────────────────────────────────────────────
@app.post("/api/characters/{char_id}/favorite")
def toggle_favorite(char_id: int, request: Request):
    user = require_user(request)
    db = get_db()
    try:
        favs = json.loads(user.get("favorites", "[]"))
    except (json.JSONDecodeError, TypeError):
        favs = []
    fav_set = set(str(f) for f in favs)
    sid = str(char_id)
    if sid in fav_set:
        favs = [f for f in favs if str(f) != sid]
    else:
        favs.append(char_id)
    db.execute("UPDATE users SET favorites=? WHERE id=?", (json.dumps(favs), user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"favorited": sid not in fav_set, "favorites": favs})


# ── Character routes moved to routes/characters.py — registered in startup

# ── Custom trap CRUD ──────────────────────────────────────────────────────────

@app.get("/api/dm/traps", response_class=JSONResponse)
def dm_list_traps(request: Request):
    """List all traps — manual data + custom user traps."""
    user = require_user(request)
    db = get_db()
    all_traps = list(MANUAL_TRAPS)
    custom = [dict(r) for r in db.execute(
        "SELECT * FROM dm_custom_traps WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],)
    ).fetchall()]
    db.close()
    for ct in custom:
        all_traps.append({
            "name": ct["name"],
            "type": ct["type"],
            "danger": ct["danger"],
            "trigger": ct.get("trigger", ""),
            "detection": {"dc": ct.get("detection_dc"), "skill": ct.get("detection_skill", "Perception"), "detail": ct.get("detection_detail", "")},
            "disarm": {"dc": ct.get("disarm_dc"), "method": ct.get("disarm_method", ""), "detail": ct.get("disarm_detail", "")},
            "effect": ct.get("effect", ""),
            "save_dc": ct.get("save_dc"),
            "save_ability": ct.get("save_ability", "Dexterity"),
            "damage": ct.get("damage", ""),
            "damage_type": ct.get("damage_type", ""),
            "area": ct.get("area", ""),
            "description": ct.get("description", ""),
            "_custom_id": ct["id"],
        })
    return JSONResponse({"traps": all_traps})


@app.post("/api/dm/traps/create", response_class=JSONResponse)
async def dm_create_trap(request: Request):
    """Create a new custom trap."""
    user = require_user(request)
    data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "Name required"}, status_code=400)
    db = get_db()
    cur = db.execute("""
        INSERT INTO dm_custom_traps (user_id, name, type, danger, trigger,
            detection_dc, detection_skill, detection_detail,
            disarm_dc, disarm_method, disarm_detail,
            effect, save_dc, save_ability, damage, damage_type, area, description)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        user["id"], name,
        data.get("type", "mechanical"),
        data.get("danger", "dangerous"),
        data.get("trigger", ""),
        data.get("detection_dc"),
        data.get("detection_skill", "Perception"),
        data.get("detection_detail", ""),
        data.get("disarm_dc"),
        data.get("disarm_method", ""),
        data.get("disarm_detail", ""),
        data.get("effect", ""),
        data.get("save_dc"),
        data.get("save_ability", "Dexterity"),
        data.get("damage", ""),
        data.get("damage_type", ""),
        data.get("area", ""),
        data.get("description", ""),
    ))
    db.commit()
    trap_id = cur.lastrowid
    db.close()
    return JSONResponse({"ok": True, "id": trap_id})


@app.post("/api/dm/traps/{trap_id}/update", response_class=JSONResponse)
async def dm_update_trap(trap_id: int, request: Request):
    """Update a custom trap."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    trap = db.execute(
        "SELECT id FROM dm_custom_traps WHERE id = ? AND user_id = ?",
        (trap_id, user["id"])
    ).fetchone()
    if not trap:
        db.close()
        return JSONResponse({"error": "Trap not found"}, status_code=404)
    db.execute("""
        UPDATE dm_custom_traps SET name=?, type=?, danger=?, trigger=?,
            detection_dc=?, detection_skill=?, detection_detail=?,
            disarm_dc=?, disarm_method=?, disarm_detail=?,
            effect=?, save_dc=?, save_ability=?, damage=?, damage_type=?, area=?, description=?
        WHERE id=?
    """, (
        (data.get("name") or "").strip(),
        data.get("type", "mechanical"),
        data.get("danger", "dangerous"),
        data.get("trigger", ""),
        data.get("detection_dc"),
        data.get("detection_skill", "Perception"),
        data.get("detection_detail", ""),
        data.get("disarm_dc"),
        data.get("disarm_method", ""),
        data.get("disarm_detail", ""),
        data.get("effect", ""),
        data.get("save_dc"),
        data.get("save_ability", "Dexterity"),
        data.get("damage", ""),
        data.get("damage_type", ""),
        data.get("area", ""),
        data.get("description", ""),
        trap_id
    ))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@app.post("/api/dm/traps/{trap_id}/delete", response_class=JSONResponse)
def dm_delete_trap(trap_id: int, request: Request):
    """Delete a custom trap."""
    user = require_user(request)
    db = get_db()
    db.execute(
        "DELETE FROM dm_custom_traps WHERE id = ? AND user_id = ?",
        (trap_id, user["id"])
    )
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


# ── Item search & description endpoints ──────────────────────────────────────

@app.get("/api/items/search", response_class=JSONResponse)
def search_items(q: str = "", limit: int = 0, type: str = "", rarity: str = "",
                       source: str = ""):
    """Search equipment + magic items by name, type, rarity, and description.

    Ranking: exact name > name prefix > name substring > field match
    (type/rarity/source/description). Empty q returns all items filtered by
    the optional type/rarity filters, alphabetically.

    `source` scopes results to books: "FGFD" or "FGFD,TTP" ("" = every book).
    """
    query = q.strip().lower()
    type_q = type.strip().lower()
    rarity_q = rarity.strip().lower()
    # Lazy import — routes.characters.helpers imports main at module scope.
    from routes.characters.helpers import parse_source_filter, source_matches
    src_slugs = parse_source_filter(source)

    def _brief(item: dict, score: int = 0) -> dict:
        desc = item.get("description") or item.get("desc") or ""
        if isinstance(desc, list):
            desc = " ".join(desc)
        return {
            "name": item["name"],
            "type": item["type"],
            "rarity": item.get("rarity", ""),
            "source": item.get("source", ""),
            "cost": item.get("cost", ""),
            "weight": item.get("weight"),
            "dice": item.get("dice", ""),
            "base_weapon": item.get("base_weapon", ""),
            "description": desc[:300],
            "concentration": "concentration" in desc.lower(),
            "_score": score,
        }

    results = []
    if not query:
        # All items (filtered by type/rarity if given), alphabetical
        filtered_total = 0
        for key in sorted(ITEM_INDEX.keys()):
            item = ITEM_INDEX[key]
            if type_q and type_q not in item["type"].lower():
                continue
            if rarity_q and rarity_q not in (item.get("rarity") or "").lower():
                continue
            if src_slugs and not source_matches(item.get("source", ""), src_slugs):
                continue
            filtered_total += 1
        for key in sorted(ITEM_INDEX.keys()):
            item = ITEM_INDEX[key]
            if type_q and type_q not in item["type"].lower():
                continue
            if rarity_q and rarity_q not in (item.get("rarity") or "").lower():
                continue
            if src_slugs and not source_matches(item.get("source", ""), src_slugs):
                continue
            results.append(_brief(item))
            if limit and len(results) >= limit:
                break
        return JSONResponse({"results": results, "total": filtered_total})

    # Ranked search
    scored = []
    for key, item in ITEM_INDEX.items():
        name = item["name"].lower()
        item_type = item["type"].lower()
        item_rarity = (item.get("rarity") or "").lower()
        item_src = (item.get("source") or "").lower()
        desc = item.get("description") or item.get("desc") or ""
        if isinstance(desc, list):
            desc = " ".join(desc).lower()
        else:
            desc = desc.lower()

        if type_q and type_q not in item_type:
            continue
        if rarity_q and rarity_q not in item_rarity:
            continue
        if src_slugs and not source_matches(item.get("source", ""), src_slugs):
            continue

        if name == query:
            score = 0  # exact name
        elif name.startswith(query):
            score = 1  # name prefix
        elif query in name:
            score = 2  # name substring
        elif query in item_type or query in item_rarity or query in item_src:
            score = 3  # type/rarity/source match
        elif query in desc:
            score = 4  # description match
        else:
            continue
        scored.append((score, name, item))

    scored.sort(key=lambda x: (x[0], x[1]))
    results = [_brief(item, score) for score, _, item in scored]
    if limit:
        results = results[:limit]
    return JSONResponse({"results": results, "total": len(results)})


@app.get("/api/items/describe", response_class=JSONResponse)
def describe_item(name: str = ""):
    """Get full description and metadata for a single item."""
    if not name or not name.strip():
        return JSONResponse({"error": "No item name provided"}, status_code=400)
    item = _resolve_item_key(name)
    if not item:
        return JSONResponse({"name": name, "description": "No description available.", "type": "Unknown"})
    # Split curse text from description for hidden rendering
    desc = item.get("description") or item.get("desc", "")
    if isinstance(desc, list):
        desc = " ".join(desc)
    safe_desc, curse_text = _split_curse_text(desc)
    enriched = dict(item)
    enriched["description"] = safe_desc
    if curse_text:
        enriched["curse"] = curse_text
    return JSONResponse(enriched)


# ── Canonical feature/spell-slot helpers ────────────────────────────────────
# Implementations live in services/leveling.py. Lazy import inside the body —
# importing at module level creates a circular import (services/leveling
# imports from main). The wrappers keep `from main import ...` callers working.

# ── Startup ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_app):
    init_db()
    # Expired rows are swept once at start and then lazily (see services.sessions) instead of
    # writing a DELETE+COMMIT on every authenticated request.
    try:
        from services.sessions import sweep_expired_sessions
        sweep_expired_sessions(DB_PATH, force=True)
    except Exception as exc:  # pragma: no cover - never block startup on cleanup
        print(f"[sessions] startup sweep failed: {exc}")
    # Register route modules (deferred to avoid circular imports)
    from routes.auth import router as auth_router
    if not any(r.path == "/login" for r in _app.routes):
        _app.include_router(auth_router)
    from routes.dm import router as dm_router
    if not any(r.path == "/dm-tools" for r in _app.routes):
        _app.include_router(dm_router)
    from routes.characters import router as char_router
    if not any(r.path == "/create" for r in _app.routes):
        _app.include_router(char_router)
    from routes.ref_images import router as ref_router
    if not any(r.path == "/api/ref-image/{kind}/{name}" for r in _app.routes):
        _app.include_router(ref_router)
    from routes.maps import router as maps_router
    if not any(r.path == "/api/dm/maps" for r in _app.routes):
        _app.include_router(maps_router)
    from routes.pack import router as pack_router
    if not any(r.path == "/api/dm/campaign/import" for r in _app.routes):
        _app.include_router(pack_router)
    # The table channel: the shared roll feed and its live streams.
    from routes.table import router as table_router
    if not any(r.path == "/api/campaign/{camp_id}/roll" for r in _app.routes):
        _app.include_router(table_router)
        from routes.undo import router as undo_router

        _app.include_router(undo_router)
    # Prewarm the nav search's internal entity index (~7k rows, ~0.5s) so the
    # first keystroke isn't paying for it. Non-fatal if it fails.
    try:
        from services.entity_search import index_stats
        _st = index_stats()
        _log.info("entity index ready: %s rows %s", _st["total"], _st["counts"])
    except Exception as _exc:  # pragma: no cover - defensive
        _log.warning("entity index prewarm failed: %s", _exc)
    # Write the sheet's character-independent reference tables to
    # static/sheet-ref.js (must run after the manual data is loaded above).
    try:
        from routes.characters.sheet import ensure_sheet_reference_asset
        _log.info("sheet reference asset: v%s", ensure_sheet_reference_asset())
    except Exception as _exc:  # pragma: no cover - defensive
        _log.warning("sheet reference asset failed: %s", _exc)
    yield


app.router.lifespan_context = lifespan

# ── Reference Manual Lookup ─────────────────────────────────────────────────
# Ingested manuals from data/manual_data/ + cached extracts from data/manual_cache/

@app.get("/api/reference/manuals", response_class=JSONResponse)
def list_manuals():
    """List available reference manuals — PDFs on disk + all ingested manuals. No auth."""
    result = {"count": 0, "manuals": [], "ingested": [], "path": str(MANUALS_BASE)}

    # 1. PDFs in the manuals directory (recursive, deduped — see _scan_manual_pdfs)
    pdfs = _scan_manual_pdfs()
    result["manuals"] = [p.name for p in pdfs]
    result["count"] = len(pdfs)

    # 2. Ingested manuals (meta.json slug_map)
    meta = _load_manual_json("_meta.json") or {}
    pdf_map = meta.get("pdf_map", {}) if isinstance(meta, dict) else {}
    slug_map = _get_source_slug_map()
    result["ingested"] = [
        {"slug": s, "title": slug_map.get(s, {}).get("display", info.get("title", s))}
        for s, info in pdf_map.items()
    ]

    return JSONResponse(result)


@app.get("/api/reference/manual-file/{path:path}")
def serve_manual_file(path: str):
    """Serve a PDF from the DnD-Manuals directory by relative path.
    Path is relative to MANUALS_BASE (e.g. 'Manuals/D&D 5E - Monster Manual.pdf').
    """
    # Security: block directory traversal
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=403, detail="Invalid path")
    full_path = (MANUALS_BASE / path).resolve()
    if not full_path.exists() or full_path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=404, detail="PDF not found")
    return FileResponse(
        full_path,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=\"{full_path.name}\""},
    )


@app.post("/api/reference/query", response_class=JSONResponse)
async def query_reference(request: Request):
    """Query reference manuals for PHB-grounding. Requires auth.
    Accepts: {"query": "dwarf racial traits", "source": "phb"}"""
    require_user(request)
    data = await request.json()
    query = data.get("query", "")
    source = data.get("source", "phb")  # "phb", "dmg", "xanathars", "all"

    # Map source to filename patterns
    source_map = {
        "phb": "Player's Handbook",
        "dmg": "Dungeon Master's Guide",
        "xanathars": "Xanathar's Guide",
        "all": "",
    }
    pattern = source_map.get(source, source)

    if not MANUALS_BASE.exists():
        return JSONResponse({"results": [], "warning": "Manual directory not available"})

    # Find matching PDFs
    import glob
    pdfs = sorted(glob.glob(str(MANUALS_BASE / "*.pdf")) + glob.glob(str(MANUALS_BASE / "*/*.pdf")))
    matches = [p for p in pdfs if pattern.lower() in Path(p).name.lower()]

    return JSONResponse({
        "query": query,
        "source": source,
        "manuals_found": len(matches),
        "files": [Path(p).name for p in matches],
        "note": "Reference lookups are manual — use the PDFs directly. Future: pdfplumber extraction.",
        "path_hint": str(MANUALS_BASE),
    })


# ── Reference PDF Viewer ─────────────────────────────────────────────────────


@app.get("/api/reference/source-map", response_class=JSONResponse)
def source_map():
    """Return slug→display_name map so the frontend can resolve source strings."""
    return JSONResponse(_get_source_slug_map())


@app.get("/api/reference/manual-titles", response_class=JSONResponse)
def manual_titles():
    """Real manuals only, with clean titles — the "filter by manual" picker list.

    Unlike /api/reference/source-map (which must resolve every source alias),
    this drops chapter/appendix aliases, map/screen PDFs and duplicate files.
    """
    return JSONResponse({"manuals": _manual_titles()})


# ── Internal entity search (nav search bar's "Internal data" section) ───────
# Every entity the app knows about — SRD core + ingested manual JSON — as typed,
# linkable rows, so the nav search lists items/creatures/NPCs/feats/races/
# traits/spells alongside manual page hits. Index lives in services/entity_search.py.

@app.get("/api/reference/entities", response_class=JSONResponse)
def reference_entities(request: Request, q: str = "", source: str = "",
                             kind: str = "", limit: int = 0, per_kind: int = 3,
                             offset: int = 0):
    """Internal-entity hits for `q`, optionally scoped to books.

    Without `kind`: capped preview (per_kind per kind) plus the TRUE per-kind
    totals, so the nav panel can label categories "3 of 145" and offer to expand.
    With `kind=K`: that kind only, uncapped total, one alphabetical page at a time
    (`limit`, default 50) — this is what clicking a category fetches.
    """
    require_user(request)
    from routes.characters.helpers import parse_source_filter  # lazy: helpers imports main
    from services.entity_search import KIND_META, search_kind, search_with_totals

    sources = parse_source_filter(source)
    q = q or ""

    if kind:
        if kind not in KIND_META:
            return JSONResponse({"error": f"Unknown entity kind '{kind}'"}, status_code=400)
        page = search_kind(q, kind, offset=max(0, int(offset or 0)),
                           limit=max(1, min(int(limit or 50), 200)), sources=sources)
        return JSONResponse({
            "query": q,
            "count": len(page["results"]),
            "kinds": KIND_META,
            "sources": sorted(sources) if sources else [],
            **page,
        })

    limit = max(1, min(int(limit or 40), 100))
    per_kind = max(1, min(int(per_kind or 3), 20))
    data = search_with_totals(q, limit=limit, per_kind=per_kind, sources=sources)
    counts: dict[str, int] = {}
    for row in data["results"]:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    return JSONResponse({
        "query": q,
        "count": len(data["results"]),
        "counts": counts,
        "total_counts": data["total_counts"],
        "total": data["total"],
        "kinds": KIND_META,
        "sources": sorted(sources) if sources else [],
        "results": data["results"],
    })


@app.get("/api/reference/entity", response_class=JSONResponse)
def reference_entity(request: Request, kind: str = "", name: str = ""):
    """Detail payload for one internal entity — powers the nav search modal."""
    require_user(request)
    from services.entity_search import KIND_META, entity_detail

    if kind not in KIND_META:
        return JSONResponse({"error": f"Unknown entity kind '{kind}'"}, status_code=400)
    detail = entity_detail(kind, name)
    if not detail:
        return JSONResponse({"error": f"No {kind} named '{name}'"}, status_code=404)
    return JSONResponse(detail)

# ── Manual titles for pickers (the "filter by manual" dropdown) ─────────────
# _get_source_slug_map() is deliberately alias-heavy: it must resolve EVERY
# source string the data can contain (chapter/appendix aliases included), which
# made it a bad source for a user-facing book list — it exposed 84 chapter
# aliases plus map/screen PDFs, and some PDFs carried a drifted label (slug "W"
# is Warlock-007.pdf but was displaying another book's title).
#
# The picker instead lists the real PDFs behind pdf_map with a clean title:
# basename → curated title, generic prettifier as fallback, non-content PDFs
# (maps, screens, character sheets) dropped, and files that resolve to the same
# path collapsed into one entry.

@app.get("/api/reference/open/{slug}")
def open_manual(slug: str, page: int = 0):
    """Serve a reference manual PDF.

    Slug is the pdf_map key (e.g. 'PHB', 'DMG', 'XGE').

    `page` is accepted for compatibility but does nothing — the server cannot set a URL fragment, so
    the caller must append `#page=N` to jump, which every call site does
    (`?page=N#page=N`). N is a PDF page: that is what the ingest stores in `source_page`.

    Without the fragment this serves page 1, which is how the map list's source links appeared to
    "not go to the exact page".
    """
    slug_map = _get_source_slug_map()
    # Case-insensitive lookup: check as-given, all-upper, all-lower, then scan keys
    info = slug_map.get(slug) or slug_map.get(slug.upper()) or slug_map.get(slug.lower())
    if not info:
        for k in slug_map:
            if k.lower() == slug.lower():
                info = slug_map[k]
                break
    if not info:
        raise HTTPException(status_code=404, detail=f"Unknown manual slug: {slug}")

    # Try direct path first, then DnD-Manuals/ subdir
    pdf_path = MANUALS_BASE / info["path"]
    if not pdf_path.exists():
        pdf_path = MANUALS_BASE / "DnD-Manuals" / info["path"]
    if not pdf_path.exists():
        import glob
        candidates = glob.glob(str(MANUALS_BASE / f"**/{info['path']}"), recursive=True)
        if candidates:
            pdf_path = Path(candidates[0])
        else:
            raise HTTPException(status_code=404, detail=f"PDF not found: {info['path']}")

    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=\"{slug.upper()}.pdf\"",
        },
    )


# ── Run ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    # access_log stays off: log_requests already prints method, path, status, ms and the
    # client IP for every request, so uvicorn's line was pure duplication. Set
    # DND_ACCESS_LOG=1 only when debugging uvicorn itself.
    uvicorn.run(
        "main:app", host="0.0.0.0", port=8300,
        reload=os.environ.get("DND_RELOAD", "1") == "1",
        access_log=os.environ.get("DND_ACCESS_LOG", "0") == "1",
    )



# ── PWA: installable, and honest about being offline ────────────────────────────────────
# The manifest and the worker are routes rather than plain /static files on purpose:
#   * a .webmanifest served by StaticFiles can pick up the wrong MIME type (the browser then
#     ignores the manifest entirely), and
#   * sw.js must never be cached by anything, including the CDN. Cloudflare caches static assets by
#     extension, and a pinned sw.js is a service worker that can never update: the app would serve
#     last week's shell to every device forever. no-store on a route fixes both.

_PWA_MANIFEST = {
    "name": "D&D Character Manager",
    "short_name": "D&D Manager",
    "description": "Characters, sheets, campaigns, maps and the table channel.",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "any",
    "background_color": "#1a1a2e",
    "theme_color": "#1a1a2e",
    "icons": [
        {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png",
         "purpose": "any"},
        {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "any"},
        {"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "maskable"},
    ],
}


@app.get("/manifest.webmanifest")
def pwa_manifest():
    """The install manifest, with a MIME type browsers actually accept."""
    return JSONResponse(_PWA_MANIFEST, media_type="application/manifest+json")


@app.get("/sw.js")
def pwa_service_worker():
    """The service worker. no-store so neither the browser nor the CDN can pin a stale one."""
    return FileResponse(
        STATIC / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store, must-revalidate", "Service-Worker-Allowed": "/"},
    )


@app.get("/offline", response_class=HTMLResponse)
def pwa_offline():
    """What a player sees when the wifi goes: the app, and the truth about what still works.

    Deliberately not a cached copy of any real page — a stale sheet is worse than an honest notice.
    """
    return HTMLResponse("""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Offline — D&D Character Manager</title>
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#1a1a2e">
<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon-180.png">
<style>
  :root{--bg:#1a1a2e;--card:#16213e;--text:#e8e8ee;--muted:#9a9aa6;--accent:#e94560;
        color-scheme:dark}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
       background:var(--bg);color:var(--text);
       font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:1.5rem}
  .card{max-width:26rem;background:var(--card);border:1px solid #2a2a4a;border-radius:12px;
        padding:1.4rem 1.5rem;text-align:center}
  img{width:72px;height:72px;margin-bottom:0.6rem}
  h1{margin:0 0 0.4rem;font-size:1.2rem}
  p{margin:0 0 0.8rem;color:var(--muted);font-size:0.92rem}
  button{background:var(--accent);color:#fff;border:none;border-radius:8px;padding:0.55rem 1rem;
         font-size:0.95rem;font-weight:600;cursor:pointer}
  ul{text-align:left;color:var(--muted);font-size:0.85rem;margin:0.8rem 0 0;padding-left:1.1rem}
</style></head>
<body><div class="card">
  <img src="/static/icons/icon-192.png" alt="">
  <h1>You are offline</h1>
  <p>The app opened, but the table is not reachable right now.</p>
  <button onclick="location.reload()">Try again</button>
  <ul>
    <li>Rolls, sheets and the tracker need the server — nothing is queued for later.</li>
    <li>Reconnect to wifi or mobile data and reload: the table picks up where it is.</li>
  </ul>
</div></body></html>""")
