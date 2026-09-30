"""Map layer routes — battle maps, their tokens, and per-map snapshots.

The spatial half of the DM tools. Deliberately small: a map is an image plus a grid plus
placements, and every placement points at art the app already serves (`/api/ref-image/...`
for creatures/NPCs/items, `/api/character/{id}/portrait-image` for PCs), so nothing here
duplicates the art pipeline.

Image uploads go through the same helpers the portrait pipeline uses
(`services.images.decode_data_url` + `thumbnail_bytes`) and then land on DISK under
`static/maps/` — a 4096px battle map must never become a data URL in a DB row, which is
exactly how this app's character portraits once reached 3 MB each.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from main import (get_db, require_user, get_current_user, _require_owned, _is_admin,
                  _user_where, _render, STATIC)
from services.images import decode_data_url, fit_blob

router = APIRouter()

MAP_MAX_PX = 4096          # battle maps are big; portraits cap at 1024
TOKEN_MAX = 200            # a map with more placements than this is not a fight any more
MAP_DIR = Path(STATIC) / "maps"
TOKEN_KINDS = {"creature", "npc", "character", "marker", "pin", "prop"}
EXT_BY_MEDIA = {"image/webp": ".webp", "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg"}


# ── helpers ────────────────────────────────────────────────────────────────────────────

def _own_map(db, user, map_id: int):
    """The map row if this user may touch it, else None (admins see everything)."""
    return _require_owned(db, user, "dm_maps", map_id)


def _own_child(db, user, table: str, child_id: int):
    """A token or scene row, authorised through its map."""
    row = db.execute(
        f"SELECT c.*, m.user_id AS _owner FROM {table} c JOIN dm_maps m ON m.id = c.map_id "
        "WHERE c.id = ?", (child_id,)
    ).fetchone()
    if not row:
        return None
    row = dict(row)
    if not _is_admin(user) and row.get("_owner") != user["id"]:
        return None
    return row


def _fnum(value, default=0.0, lo=-100000.0, hi=100000.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:  # NaN
        return default
    return max(lo, min(hi, out))


# 1,000,000 ft is ~189 miles: headroom over the 60-mile continent hex, and still a bounded field.
FEET_PER_CELL_MAX = 1_000_000


def _quarter_turn(value):
    """Snap a rotation to the nearest quarter turn, normalised to 0/90/180/270.

    A free angle would put the artwork at odds with the square grid drawn on it, and the point of the
    control is to straighten a sideways scan, so anything else is a mistake rather than a feature."""
    try:
        return (int(round(float(value) / 90.0)) * 90) % 360
    except (TypeError, ValueError):
        return 0


def _inum(value, default=0, lo=-9999, hi=9999) -> int:
    try:
        out = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, out))


def _token_cells(raw: dict) -> int:
    """The footprint the payload asks for: `cells`, or a 5e size word, or one square."""
    if raw.get("cells") is not None:
        return _inum(raw.get("cells"), 1, 1, 4)
    return _cells_for_size(raw.get("size"), 1)


def _clean_token(raw: dict, map_id: int) -> dict | None:
    """Normalise one placement. An unusable entry is dropped rather than stored."""
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "creature").lower()
    if kind not in TOKEN_KINDS:
        kind = "creature"
    return {
        "map_id": map_id,
        "x": _fnum(raw.get("x")),
        "y": _fnum(raw.get("y")),
        # Footprint: an explicit w/h wins (the DM may want a 2x2 "large" guard), then an explicit
        # cells count, then a size word, then one square. A token placed with no size at all is a
        # Medium creature — which is right for a PC or a goblin and wrong for anything bigger, so
        # the palette sends the creature's real size from the reference library.
        "w": _inum(raw.get("w"), _token_cells(raw), 1, 12),
        "h": _inum(raw.get("h"), _token_cells(raw), 1, 12),
        "kind": kind,
        "ref_name": str(raw.get("ref_name") or "")[:120],
        "label": str(raw.get("label") or "")[:60],
        "hp_current": _inum(raw.get("hp_current"), 0, -999, 9999),
        "hp_max": _inum(raw.get("hp_max"), 0, 0, 9999),
        "hidden": 1 if raw.get("hidden") else 0,
        "z": _inum(raw.get("z"), 0, -50, 50),
        "encounter_en_id": _inum(raw["encounter_en_id"], 0, 0, 10 ** 9) if raw.get("encounter_en_id") else None,
        "character_id": _inum(raw["character_id"], 0, 0, 10 ** 9) if raw.get("character_id") else None,
    }


def _tokens_of(db, map_id: int) -> list[dict]:
    return [dict(r) for r in db.execute(
        "SELECT * FROM dm_map_tokens WHERE map_id = ? ORDER BY z, id", (map_id,))]


def _sync_tokens(db, map_id: int, posted) -> list[dict]:
    """Upsert the posted placements and delete the ones the client dropped.

    The canvas sends its whole token list on a debounced save, so the server has to work out
    what is new, what moved and what is gone. Rows keep their ids across saves — HP and the
    encounter link (`encounter_en_id`) ride on that id, and a delete/insert cycle would break
    the link mid-fight.
    """
    if not isinstance(posted, list):
        posted = []
    existing = {t["id"] for t in _tokens_of(db, map_id)}
    kept: set[int] = set()
    for raw in posted[:TOKEN_MAX]:
        clean = _clean_token(raw, map_id)
        if not clean:
            continue
        tid = _inum(raw.get("id"), 0, 0, 10 ** 9)
        if tid and tid in existing:
            kept.add(tid)
            db.execute(
                "UPDATE dm_map_tokens SET x=?, y=?, w=?, h=?, kind=?, ref_name=?, label=?, "
                "hp_current=?, hp_max=?, hidden=?, z=? WHERE id=? AND map_id=?",
                (clean["x"], clean["y"], clean["w"], clean["h"], clean["kind"], clean["ref_name"],
                 clean["label"], clean["hp_current"], clean["hp_max"], clean["hidden"], clean["z"],
                 tid, map_id))
        else:
            cur = db.execute(
                "INSERT INTO dm_map_tokens (map_id, x, y, w, h, kind, ref_name, label, hp_current, "
                "hp_max, hidden, z, encounter_en_id, character_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (map_id, clean["x"], clean["y"], clean["w"], clean["h"], clean["kind"],
                 clean["ref_name"], clean["label"], clean["hp_current"], clean["hp_max"],
                 clean["hidden"], clean["z"], clean["encounter_en_id"], clean["character_id"]))
            kept.add(cur.lastrowid)
    for tid in existing - kept:
        db.execute("DELETE FROM dm_map_tokens WHERE id = ? AND map_id = ?", (tid, map_id))
    db.commit()
    return _tokens_of(db, map_id)


def _scenes_of(db, map_id: int) -> list[dict]:
    return [dict(r) for r in db.execute(
        "SELECT id, name, created_at FROM dm_map_scenes WHERE map_id = ? ORDER BY created_at DESC",
        (map_id,))]


# ── maps ───────────────────────────────────────────────────────────────────────────────

@router.get("/api/dm/maps", response_class=JSONResponse)
async def dm_maps_list(request: Request, campaign_id: str = ""):
    """The user's maps, newest first. `campaign_id` narrows to one campaign."""
    user = require_user(request)
    db = get_db()
    try:
        where, params = _user_where(user, "m.user_id")
        params = list(params)
        if campaign_id:
            # _user_where returns '' for an admin, so the AND cannot be appended blindly
            where = (where + " AND m.campaign_id = ?") if where else "WHERE m.campaign_id = ?"
            params.append(_inum(campaign_id, 0, 0, 10 ** 9))
        # Only what the list draws. `SELECT m.*` shipped every map's fog, draw_data, notes, camera
        # and player key: with one demo map that was free, with 689 corpus maps the list payload was
        # 323 KB of state the tab never reads. Those blobs belong to the map page, not the list.
        minted = ("id", "user_id", "campaign_id", "name", "image_path", "image_w", "image_h",
                  "grid_type", "grid_size", "grid_offset_x", "grid_offset_y", "feet_per_cell",
                  "source_manual", "source_page", "created_at", "parent_map_id")
        rows = db.execute(
            f"SELECT {', '.join('m.' + c for c in minted)}, "
            f"(SELECT COUNT(*) FROM dm_map_tokens t WHERE t.map_id = m.id) AS token_count, "
            f"(SELECT COUNT(*) FROM dm_map_scenes s WHERE s.map_id = m.id) AS scene_count "
            f"FROM dm_maps m {where} ORDER BY m.created_at DESC", params).fetchall()
        return JSONResponse({"count": len(rows), "maps": [dict(r) for r in rows]})
    finally:
        db.close()


@router.post("/api/dm/map/{map_id}/align-grid", response_class=JSONResponse)
async def dm_map_align_grid(map_id: int, request: Request):
    """Put the overlay grid on the grid printed on the art.

    Cell size is half the problem: an overlay at the right pitch still looks wrong until its lines land
    on the printed ones, which is the phase a DM otherwise nudges in by hand. services/map_grid_align
    measures both from the image. It is deliberately allowed to fail — art with no printed grid folds
    flat (measured 0.08-0.21 against 0.9+ for a real grid), and the map is then left exactly as it was
    rather than given a confident-looking wrong grid.
    """
    from pathlib import Path as _P

    from starlette.concurrency import run_in_threadpool

    from services.map_grid_align import detect

    user = require_user(request)
    db = get_db()
    try:
        row = _own_map(db, user, map_id)
        if not row:
            return JSONResponse({"ok": False, "error": "map not found"}, status_code=404)
        rel = str(row["image_path"] or "")
        if not rel.startswith("/static/maps/"):
            return JSONResponse({"ok": False, "reason": "this map has no art to measure"})
        src = _P(__file__).resolve().parent.parent / rel.lstrip("/")
        if not src.is_file():
            return JSONResponse({"ok": False, "reason": "the art file is missing"})

        # CPU-bound and the route is async: never encode or measure inline
        res = await run_in_threadpool(detect, str(src))
        if not res:
            return JSONResponse({"ok": False, "reason": "the art could not be read"})
        if not res["has_grid"]:
            return JSONResponse({"ok": False, "reason": "no printed grid found on this map",
                                 "score": res["score"], "axis_agree": res["axis_agree"]})

        size = max(10, min(400, int(round(res["pitch_px"]))))
        ox = int(round(res["offset_x"])) % max(1, size)
        oy = int(round(res["offset_y"])) % max(1, size)
        # pressing the button is a decision too, so it claims the map for the DM as well
        db.execute("UPDATE dm_maps SET grid_size=?, grid_offset_x=?, grid_offset_y=?, "
                   "grid_source='user' WHERE id=?", (size, ox, oy, map_id))
        db.commit()
        return JSONResponse({"ok": True, "grid_size": size, "offset_x": ox, "offset_y": oy,
                             "score": res["score"], "axis_agree": res["axis_agree"]})
    finally:
        db.close()


@router.get("/api/dm/map/{map_id}/thumb")
async def dm_map_thumb(map_id: int, request: Request, size: int = 96):
    """A small WebP of the map's art for the list. 96px by default.

    The list is 689 maps long, so its thumbs must never be full-size art and must never be
    re-encoded twice: the first request writes `static/maps/thumbs/<stem>-<size>.webp` and every
    later one is a file read. The stem comes from the stored image path, which carries a content
    hash, so replacing a map's art changes the URL — which is what makes a year-long immutable
    cache safe here where the reference portraits deliberately keep theirs short.
    """
    from fastapi.responses import Response
    from pathlib import Path as _P

    from services.images import thumbnail_bytes

    user = require_user(request)
    db = get_db()
    try:
        row = _own_map(db, user, map_id)
        if not row:
            return Response(status_code=404)
        rel = str(row["image_path"] or "")
        if not rel.startswith("/static/maps/"):
            return Response(status_code=404, headers={"Cache-Control": "no-store"})
        src = _P(__file__).resolve().parent.parent / rel.lstrip("/")
        if not src.is_file():
            return Response(status_code=404, headers={"Cache-Control": "no-store"})
        size = _inum(size, 96, 32, 512)
        cache = src.parent / "thumbs" / f"{src.stem}-{size}.webp"
        if cache.is_file():
            return Response(cache.read_bytes(),
                            media_type="image/webp",
                            headers={"Cache-Control": "public, max-age=31536000, immutable"})
        thumb = thumbnail_bytes(src.read_bytes(), size)
        if not thumb:
            # Pillow could not help (or would not be smaller): serve the original, but do not let a
            # year-long cache pin it, in case a later request can do better.
            return Response(src.read_bytes(),
                            headers={"Cache-Control": "public, max-age=300, must-revalidate"})
        blob, media = thumb
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")
            tmp.write_bytes(blob)
            tmp.replace(cache)
        except OSError:
            pass                                    # serving beats caching
        return Response(blob, media_type=media,
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})
    finally:
        db.close()


@router.post("/api/dm/map/create", response_class=JSONResponse)
async def dm_map_create(request: Request):
    user = require_user(request)
    data = await request.json()
    name = str(data.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "A map needs a name"}, status_code=400)
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO dm_maps (user_id, campaign_id, name, grid_type, grid_size, "
            "grid_offset_x, grid_offset_y, notes, feet_per_cell, source_manual, source_page, "
            "grid_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (user["id"], _inum(data.get("campaign_id"), 0, 0, 10 ** 9) or None, name[:80],
             "hex" if str(data.get("grid_type")) == "hex" else "square",
             _inum(data.get("grid_size"), 50, 10, 400),
             _inum(data.get("grid_offset_x"), 0, -400, 400),
             _inum(data.get("grid_offset_y"), 0, -400, 400),
             str(data.get("notes") or "")[:2000],
             # Not 1-100: a battle map is 5 ft/cell, but an overland map is miles per hex — the
             # DMG's scales are 1 mile (5280), 6 miles (31,680) and 60 miles (316,800) per hex.
             _inum(data.get("feet_per_cell"), 5, 1, FEET_PER_CELL_MAX),
             str(data.get("source_manual") or "")[:40],          # a manual slug, e.g. "DMG"
             _inum(data.get("source_page"), 0, 0, 5000),
             # A size or offset given at creation is the DM saying something, so "preset". It still
             # allows the phase to be aligned later, but the cell size is not re-derived from the art —
             # only an untouched default may be replaced by what the art actually measures.
             "preset" if any(k in data for k in
                             ("grid_size", "grid_offset_x", "grid_offset_y")) else ""))
        db.commit()
        return JSONResponse({"ok": True, "id": cur.lastrowid})
    finally:
        db.close()


@router.get("/api/dm/map/{map_id}", response_class=JSONResponse)
async def dm_map_detail(map_id: int, request: Request):
    """One map with everything needed to draw it.

    The two overlay layers come back parsed (`fog` = revealed cell keys, `draw` = strokes)
    because only the canvas cares about them and a JSON string in the payload means every
    caller has to remember to parse it.
    """
    user = require_user(request)
    db = get_db()
    try:
        row = _own_map(db, user, map_id)
        if not row:
            return JSONResponse({"error": "Not found"}, status_code=404)
        row = dict(row)
        row["fog"] = _clean_fog(_safe_json(row.get("fog"), []))
        row["draw"] = _clean_draw(_safe_json(row.get("draw_data"), []))
        return JSONResponse({"map": row, "tokens": _tokens_of(db, map_id), "scenes": _scenes_of(db, map_id)})
    finally:
        db.close()


@router.post("/api/dm/map/{map_id}/update", response_class=JSONResponse)
async def dm_map_update(map_id: int, request: Request):
    """Metadata, grid and camera. Absent keys are left alone."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    try:
        row = _own_map(db, user, map_id)
        if not row:
            return JSONResponse({"error": "Not found"}, status_code=404)
        sets, params = [], []
        if "name" in data:
            name = str(data.get("name") or "").strip()
            if not name:
                return JSONResponse({"error": "A map needs a name"}, status_code=400)
            sets.append("name = ?"); params.append(name[:80])
        if "notes" in data:
            sets.append("notes = ?"); params.append(str(data.get("notes") or "")[:2000])
        if "grid_type" in data:
            sets.append("grid_type = ?"); params.append("hex" if str(data.get("grid_type")) == "hex" else "square")
        if "grid_size" in data:
            sets.append("grid_size = ?"); params.append(_inum(data.get("grid_size"), 50, 10, 400))
        if "grid_offset_x" in data:
            sets.append("grid_offset_x = ?"); params.append(_inum(data.get("grid_offset_x"), 0, -400, 400))
        if "grid_offset_y" in data:
            sets.append("grid_offset_y = ?"); params.append(_inum(data.get("grid_offset_y"), 0, -400, 400))
        if "rotation" in data:
            # Snap to a quarter turn: the point is to straighten a sideways scan, and an arbitrary angle
            # would put the art at odds with the square grid it is drawn on.
            sets.append("rotation = ?"); params.append(_quarter_turn(data.get("rotation")))

        # Record that a human placed this grid. The canvas autosaves the values it just loaded, so
        # "the grid fields were posted" is not an edit — only a post that actually differs from the
        # stored row is. Without this the automatic paths cannot tell a DM's correction from a default,
        # and the image route would re-measure over a grid someone had aligned by hand.
        grid_keys = ("grid_size", "grid_offset_x", "grid_offset_y")
        if any(k in data for k in grid_keys + ("grid_type",)):
            stored_row = db.execute("SELECT grid_size, grid_offset_x, grid_offset_y, grid_type "
                                    "FROM dm_maps WHERE id = ?", (map_id,)).fetchone()
            stored = dict(zip(("grid_size", "grid_offset_x", "grid_offset_y", "grid_type"),
                              tuple(stored_row))) if stored_row else {}
            changed = False
            for k in grid_keys:
                if k in data and k in stored:
                    if _inum(data.get(k), stored[k], -100000, 100000) != int(stored[k] or 0):
                        changed = True
            if "grid_type" in data and str(data.get("grid_type") or "") != str(stored.get("grid_type") or ""):
                changed = True
            if changed:
                sets.append("grid_source = ?"); params.append("user")
        if "camera" in data:
            cam = data.get("camera")
            sets.append("camera = ?")
            params.append(json.dumps(cam)[:400] if isinstance(cam, dict) else str(cam or "")[:400])
        if "feet_per_cell" in data:
            sets.append("feet_per_cell = ?")
            params.append(_inum(data.get("feet_per_cell"), 5, 1, FEET_PER_CELL_MAX))
        if "source_manual" in data:
            sets.append("source_manual = ?"); params.append(str(data.get("source_manual") or "")[:40])
        if "source_page" in data:
            sets.append("source_page = ?"); params.append(_inum(data.get("source_page"), 0, 0, 5000))
        if "campaign_id" in data:
            sets.append("campaign_id = ?"); params.append(_inum(data.get("campaign_id"), 0, 0, 10 ** 9) or None)
        if "parent_map_id" in data:
            sets.append("parent_map_id = ?"); params.append(_inum(data.get("parent_map_id"), 0, 0, 10 ** 9) or None)
        if not sets:
            return JSONResponse({"ok": True, "unchanged": True})
        params.append(map_id)
        db.execute(f"UPDATE dm_maps SET {', '.join(sets)} WHERE id = ?", params)
        db.commit()
        return JSONResponse({"ok": True, "map": _own_map(db, user, map_id)})
    finally:
        db.close()


@router.post("/api/dm/map/{map_id}/delete", response_class=JSONResponse)
async def dm_map_delete(map_id: int, request: Request):
    """Delete a map and everything placed on it."""
    user = require_user(request)
    db = get_db()
    try:
        row = _own_map(db, user, map_id)
        if not row:
            return JSONResponse({"error": "Not found"}, status_code=404)
        db.execute("DELETE FROM dm_map_tokens WHERE map_id = ?", (map_id,))
        db.execute("DELETE FROM dm_map_scenes WHERE map_id = ?", (map_id,))
        db.execute("DELETE FROM dm_maps WHERE id = ?", (map_id,))
        db.commit()
        _drop_map_image(db, row.get("image_path") or "")
        return JSONResponse({"ok": True})
    finally:
        db.close()


def _drop_map_image(db, image_path: str) -> None:
    """Remove a file this app wrote, unless another map still points at it."""
    if not image_path.startswith("/static/maps/"):
        return
    still_used = db.execute("SELECT COUNT(*) FROM dm_maps WHERE image_path = ?", (image_path,)).fetchone()[0]
    if still_used:
        return
    target = Path(STATIC) / "maps" / Path(image_path).name
    try:
        if target.is_file() and target.parent == MAP_DIR:
            target.unlink()
    except OSError:
        pass  # a leftover file is not worth failing a delete for


# ── map image ──────────────────────────────────────────────────────────────────────────

@router.post("/api/dm/map/{map_id}/image", response_class=JSONResponse)
async def dm_map_image(map_id: int, request: Request):
    """Attach (or clear) the map's background image.

    Downscaled to MAP_MAX_PX and written to `static/maps/` — never stored as a data URL in the
    row. The filename carries a content hash, so re-uploading the same file is a no-op and a
    replaced image does not leave the old one serving from a URL in a browser cache.
    """
    user = require_user(request)
    data = await request.json()
    db = get_db()
    try:
        row = _own_map(db, user, map_id)
        if not row:
            return JSONResponse({"error": "Not found"}, status_code=404)
        value = str(data.get("image") or "").strip()
        if not value:
            db.execute("UPDATE dm_maps SET image_path = '' WHERE id = ?", (map_id,))
            db.commit()
            _drop_map_image(db, row.get("image_path") or "")
            return JSONResponse({"ok": True, "image_path": ""})
        if value.startswith("http://") or value.startswith("https://"):
            db.execute("UPDATE dm_maps SET image_path = ? WHERE id = ?", (value[:400], map_id))
            db.commit()
            return JSONResponse({"ok": True, "image_path": value[:400], "external": True})
        if not value.startswith("data:"):
            return JSONResponse({"error": "image must be a data URL or an http(s) URL"}, status_code=400)
        decoded = decode_data_url(value)
        if not decoded:
            return JSONResponse({"error": "image data URL is not decodable"}, status_code=400)
        blob, media = decoded
        fitted = fit_blob(blob, MAP_MAX_PX)
        if fitted:
            blob, media, img_w, img_h, src_w, src_h = fitted
        else:
            media, img_w, img_h, src_w, src_h = "image/png", 0, 0, 0, 0
        ext = EXT_BY_MEDIA.get(media, ".png")
        digest = hashlib.sha1(blob).hexdigest()[:12]
        filename = f"map-{map_id}-{digest}{ext}"
        MAP_DIR.mkdir(parents=True, exist_ok=True)
        (MAP_DIR / filename).write_bytes(blob)

        # If the upload HAD to be shrunk, the art's squares are now closer together: scale the
        # grid with it, so the DM's 5-ft alignment survives the resize instead of silently
        # covering twice as many squares as it did on the original file.
        grid_size = _inum(row.get("grid_size"), 50, 10, 400)
        ox = _inum(row.get("grid_offset_x"), 0, -400, 400)
        oy = _inum(row.get("grid_offset_y"), 0, -400, 400)
        scaled = False
        if src_w and img_w and img_w != src_w:
            factor = img_w / float(src_w)
            grid_size = max(10, min(400, int(round(grid_size * factor))))
            ox = max(-400, min(400, int(round(ox * factor))))
            oy = max(-400, min(400, int(round(oy * factor))))
            scaled = True

        # Align by default, where the art offers something to align to. A map whose grid was never
        # placed (offsets still 0,0) is put onto the art's own lines as its art lands, rather than
        # waiting for someone to notice and press a button. The pitch stays exactly as it is — the map
        # already carries a cell size and re-deriving it is how a lattice lands on a multiple of the
        # real one — so this only solves for the phase. Best-effort: a failure must never cost an upload.
        try:
            _placed_by = str(row["grid_source"] or "")
        except Exception:
            _placed_by = ""
        # The cell size is NEVER re-derived here. It was tempting — a brand-new map carries the default
        # 50, so replacing it with what the art measures looks like free accuracy — but it re-introduces
        # the failure this feature already paid for: texture and harmonics measure as plausible pitches
        # (on plain test art the detector confidently reports 35, and on real maps it reported 178 where
        # the truth was 104). Only a map's own size or an explicit measurement from the DM sets it.
        auto_aligned = False
        if ox == 0 and oy == 0 and _placed_by != "user":
            try:
                from starlette.concurrency import run_in_threadpool as _pool

                from services.map_grid_align import detect as _detect
                got = await _pool(_detect, str(MAP_DIR / filename), grid_size)
                if got and got.get("has_grid"):
                    ox = max(-400, min(400, int(round(got["offset_x"])) % max(1, grid_size)))
                    oy = max(-400, min(400, int(round(got["offset_y"])) % max(1, grid_size)))
                    auto_aligned = True
            except Exception:
                auto_aligned = False

        image_path = f"/static/maps/{filename}"
        db.execute("UPDATE dm_maps SET image_path = ?, image_w = ?, image_h = ?, grid_size = ?, "
                   "grid_offset_x = ?, grid_offset_y = ? WHERE id = ?",
                   (image_path, img_w, img_h, grid_size, ox, oy, map_id))
        db.commit()
        _drop_map_image(db, row.get("image_path") or "")
        return JSONResponse({"ok": True, "image_path": image_path, "bytes": len(blob),
                             "image_w": img_w, "image_h": img_h,
                             "source_w": src_w, "source_h": src_h,
                             "grid_size": grid_size, "grid_offset_x": ox, "grid_offset_y": oy,
                             "grid_scaled": scaled, "auto_aligned": auto_aligned})
    finally:
        db.close()


# ── tokens ─────────────────────────────────────────────────────────────────────────────

@router.post("/api/dm/map/{map_id}/tokens", response_class=JSONResponse)
async def dm_map_tokens_save(map_id: int, request: Request):
    """Bulk save the canvas's placements (the debounced drag save)."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    try:
        if not _own_map(db, user, map_id):
            return JSONResponse({"error": "Not found"}, status_code=404)
        tokens = _sync_tokens(db, map_id, data.get("tokens"))
        return JSONResponse({"ok": True, "tokens": tokens, "count": len(tokens)})
    finally:
        db.close()


@router.post("/api/dm/map/{map_id}/token/add", response_class=JSONResponse)
async def dm_map_token_add(map_id: int, request: Request):
    """Place one creature/NPC/character/marker on the map."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    try:
        map_row = _own_map(db, user, map_id)
        if not map_row:
            return JSONResponse({"error": "Not found"}, status_code=404)
        count = db.execute("SELECT COUNT(*) FROM dm_map_tokens WHERE map_id = ?", (map_id,)).fetchone()[0]
        if count >= TOKEN_MAX:
            return JSONResponse({"error": f"a map holds at most {TOKEN_MAX} tokens"}, status_code=400)
        clean = _clean_token(data, map_id)
        if not clean:
            return JSONResponse({"error": "bad token payload"}, status_code=400)
        if data.get("x") is None or data.get("y") is None:
            # no coordinates: land in a cell rather than at (0,0), which is the map's corner and
            # would hang half the token off the edge
            clean["x"], clean["y"] = _snap_to_cell(clean["x"], clean["y"], map_row)
        cur = db.execute(
            "INSERT INTO dm_map_tokens (map_id, x, y, w, h, kind, ref_name, label, hp_current, hp_max, "
            "hidden, z, encounter_en_id, character_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (map_id, clean["x"], clean["y"], clean["w"], clean["h"], clean["kind"], clean["ref_name"],
             clean["label"], clean["hp_current"], clean["hp_max"], clean["hidden"], clean["z"],
             clean["encounter_en_id"], clean["character_id"]))
        db.commit()
        row = db.execute("SELECT * FROM dm_map_tokens WHERE id = ?", (cur.lastrowid,)).fetchone()
        return JSONResponse({"ok": True, "token": dict(row)})
    finally:
        db.close()


@router.post("/api/dm/map/token/{token_id}/update", response_class=JSONResponse)
async def dm_map_token_update(token_id: int, request: Request):
    """HP, label, visibility, size — the things that change during a fight."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    try:
        row = _own_child(db, user, "dm_map_tokens", token_id)
        if not row:
            return JSONResponse({"error": "Not found"}, status_code=404)
        sets, params = [], []
        for key, caster in (("hp_current", lambda v: _inum(v, 0, -999, 9999)),
                            ("hp_max", lambda v: _inum(v, 0, 0, 9999)),
                            ("z", lambda v: _inum(v, 0, -50, 50)),
                            ("w", lambda v: _inum(v, 1, 1, 12)),
                            ("h", lambda v: _inum(v, 1, 1, 12)),
                            ("x", lambda v: _fnum(v)),
                            ("y", lambda v: _fnum(v))):
            if key in data:
                sets.append(f"{key} = ?"); params.append(caster(data.get(key)))
        if "hidden" in data:
            sets.append("hidden = ?"); params.append(1 if data.get("hidden") else 0)
        if "label" in data:
            sets.append("label = ?"); params.append(str(data.get("label") or "")[:60])
        if "ref_name" in data:
            sets.append("ref_name = ?"); params.append(str(data.get("ref_name") or "")[:120])
        if not sets:
            return JSONResponse({"ok": True, "unchanged": True})
        params.append(token_id)
        db.execute(f"UPDATE dm_map_tokens SET {', '.join(sets)} WHERE id = ?", params)
        # HP changed on a token that came from the tracker: keep the tracker's row in step, so
        # the DM does not have to wound the same goblin twice.
        if "hp_current" in data or "hp_max" in data:
            row = db.execute("SELECT encounter_en_id, hp_current, hp_max FROM dm_map_tokens WHERE id = ?",
                             (token_id,)).fetchone()
            if row and row["encounter_en_id"]:
                db.execute("UPDATE dm_encounter_npcs SET hp_current = ?, hp_max = ?, "
                           "defeated = CASE WHEN ? <= 0 THEN 1 ELSE defeated END WHERE id = ?",
                           (row["hp_current"], row["hp_max"], row["hp_current"], row["encounter_en_id"]))
        db.commit()
        out = db.execute("SELECT * FROM dm_map_tokens WHERE id = ?", (token_id,)).fetchone()
        return JSONResponse({"ok": True, "token": dict(out)})
    finally:
        db.close()


@router.post("/api/dm/map/token/{token_id}/delete", response_class=JSONResponse)
async def dm_map_token_delete(token_id: int, request: Request):
    user = require_user(request)
    db = get_db()
    try:
        if not _own_child(db, user, "dm_map_tokens", token_id):
            return JSONResponse({"error": "Not found"}, status_code=404)
        db.execute("DELETE FROM dm_map_tokens WHERE id = ?", (token_id,))
        db.commit()
        return JSONResponse({"ok": True})
    finally:
        db.close()


# ── overlay layers: fog of war + freehand drawing ──────────────────────────────────────

FOG_MAX = 20000          # revealed cells; a map bigger than this is not being played on
DRAW_MAX_STROKES = 400
DRAW_MAX_POINTS = 2000
_CELL_KEY = re.compile(r"^-?\d{1,6},-?\d{1,6}$")


def _clean_fog(raw) -> list[str]:
    """Revealed cells, as "col,row" (square) or "q,r" (hex). Validated, deduped, capped.

    The key format is the same for both grid types — the canvas interprets it per grid — so a
    map switched from square to hex keeps whatever it had rather than losing the layer.
    """
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for item in raw[:FOG_MAX]:
        key = str(item)
        if _CELL_KEY.match(key) and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _clean_draw(raw) -> list[dict]:
    """Freehand strokes, bounded: a runaway client must not be able to fill the row."""
    if not isinstance(raw, list):
        return []
    out = []
    for stroke in raw[:DRAW_MAX_STROKES]:
        if not isinstance(stroke, dict):
            continue
        pts = stroke.get("points")
        if not isinstance(pts, list):
            continue
        clean_pts = []
        for pt in pts[:DRAW_MAX_POINTS]:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                clean_pts.append([round(_fnum(pt[0]), 1), round(_fnum(pt[1]), 1)])
        if len(clean_pts) >= 2:
            out.append({
                "color": str(stroke.get("color") or "#e5e7eb")[:24],
                "width": _inum(stroke.get("width"), 4, 1, 40),
                "points": clean_pts,
            })
    return out


@router.post("/api/dm/map/{map_id}/layer", response_class=JSONResponse)
async def dm_map_layer_save(map_id: int, request: Request):
    """Save the fog and/or the drawing (the canvas' debounced overlay save).

    Separate from `/tokens` because the two change for different reasons and a fog sweep
    should never rewrite placements.
    """
    user = require_user(request)
    data = await request.json()
    db = get_db()
    try:
        if not _own_map(db, user, map_id):
            return JSONResponse({"error": "Not found"}, status_code=404)
        sets, params = [], []
        if "fog" in data:
            sets.append("fog = ?"); params.append(json.dumps(_clean_fog(data.get("fog"))))
        if "fog_on" in data:
            sets.append("fog_on = ?"); params.append(1 if data.get("fog_on") else 0)
        if "draw" in data:
            sets.append("draw_data = ?"); params.append(json.dumps(_clean_draw(data.get("draw"))))
        if not sets:
            return JSONResponse({"ok": True, "unchanged": True})
        params.append(map_id)
        db.execute(f"UPDATE dm_maps SET {', '.join(sets)} WHERE id = ?", params)
        db.commit()
        row = dict(_own_map(db, user, map_id) or {})
        return JSONResponse({"ok": True,
                             "fog": _clean_fog(_safe_json(row.get("fog"), [])),
                             "fog_on": row.get("fog_on") or 0,
                             "draw": _clean_draw(_safe_json(row.get("draw_data"), []))})
    finally:
        db.close()


# ── snapshots ("game saves" for one map) ───────────────────────────────────────────────

@router.post("/api/dm/map/{map_id}/snapshot", response_class=JSONResponse)
async def dm_map_snapshot(map_id: int, request: Request):
    """Freeze the current placements + camera under a name."""
    user = require_user(request)
    data = await request.json()
    name = str(data.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "A snapshot needs a name"}, status_code=400)
    db = get_db()
    try:
        row = _own_map(db, user, map_id)
        if not row:
            return JSONResponse({"error": "Not found"}, status_code=404)
        snapshot = json.dumps({
            "tokens": _tokens_of(db, map_id),
            "camera": row.get("camera") or "",
            # a prepared setup is also "what the party has already seen" and what is drawn on it
            "fog": _safe_json(row.get("fog"), []),
            "fog_on": row.get("fog_on") or 0,
            "draw": _safe_json(row.get("draw_data"), []),
        })
        cur = db.execute("INSERT INTO dm_map_scenes (map_id, name, snapshot) VALUES (?,?,?)",
                         (map_id, name[:60], snapshot))
        db.commit()
        return JSONResponse({"ok": True, "id": cur.lastrowid, "name": name[:60]})
    finally:
        db.close()


@router.post("/api/dm/map/scene/{scene_id}/restore", response_class=JSONResponse)
async def dm_map_scene_restore(scene_id: int, request: Request):
    """Put a snapshot back: placements and camera."""
    user = require_user(request)
    db = get_db()
    try:
        scene = _own_child(db, user, "dm_map_scenes", scene_id)
        if not scene:
            return JSONResponse({"error": "Not found"}, status_code=404)
        try:
            blob = json.loads(scene.get("snapshot") or "{}")
        except (TypeError, ValueError):
            blob = {}
        if not isinstance(blob, dict):
            blob = {}
        tokens = _sync_tokens(db, scene["map_id"], blob.get("tokens") or [])
        camera = blob.get("camera")
        if camera:
            db.execute("UPDATE dm_maps SET camera = ? WHERE id = ?", (str(camera)[:400], scene["map_id"]))
            db.commit()
        layers = {}
        if "fog" in blob or "fog_on" in blob:
            layers["fog"] = _clean_fog(blob.get("fog"))
            layers["fog_on"] = 1 if blob.get("fog_on") else 0
        if "draw" in blob:
            layers["draw"] = _clean_draw(blob.get("draw"))
        if layers:
            sets, params = [], []
            if "fog" in layers:
                sets.append("fog = ?"); params.append(json.dumps(layers["fog"]))
                sets.append("fog_on = ?"); params.append(layers["fog_on"])
            if "draw" in layers:
                sets.append("draw_data = ?"); params.append(json.dumps(layers["draw"]))
            params.append(scene["map_id"])
            db.execute(f"UPDATE dm_maps SET {', '.join(sets)} WHERE id = ?", params)
            db.commit()
        return JSONResponse({"ok": True, "tokens": tokens, "camera": camera or "",
                             "fog": layers.get("fog", []), "fog_on": layers.get("fog_on", 0),
                             "draw": layers.get("draw", [])})
    finally:
        db.close()


@router.post("/api/dm/map/scene/{scene_id}/delete", response_class=JSONResponse)
async def dm_map_scene_delete(scene_id: int, request: Request):
    user = require_user(request)
    db = get_db()
    try:
        if not _own_child(db, user, "dm_map_scenes", scene_id):
            return JSONResponse({"error": "Not found"}, status_code=404)
        db.execute("DELETE FROM dm_map_scenes WHERE id = ?", (scene_id,))
        db.commit()
        return JSONResponse({"ok": True})
    finally:
        db.close()


# ── spawn an encounter onto a map ──────────────────────────────────────────────────────

# How many 5-ft squares a creature occupies, per the 5e rules: Tiny/Small/Medium are one square,
# Large 2x2, Huge 3x3, Gargantuan 4x4. Everything that sizes a token goes through here.
SIZE_CELLS = {"tiny": 1, "small": 1, "medium": 1, "large": 2, "huge": 3, "gargantuan": 4}
_SIZE_ALIASES = {"t": "tiny", "sm": "small", "med": "medium", "lg": "large",
                 "grg": "gargantuan", "garg": "gargantuan"}


def _round_half_up(value: float) -> int:
    """Round like JavaScript's Math.round, not like Python's round.

    Python rounds halves to even (`round(0.5) == 0`); JS rounds them up (`Math.round(0.5) == 1`).
    On a cell boundary — a token at exactly x = 40 with 40px cells — that difference puts the
    server's snap in the cell BEFORE the one the canvas names, and the player projection would
    then reveal or hide the wrong cell for that token. The two implementations have to agree
    exactly, so this is the only rounding allowed in the grid maths.
    """
    return int(math.floor(value + 0.5))


def _cells_for_size(value, fallback: int = 1) -> int:
    """Footprint of a size word ("Large", "Huge", "grg") in 5-ft squares."""
    word = str(value or "").strip().lower().split(" ")[0].strip(".,;")
    if not word:
        return fallback
    return SIZE_CELLS.get(_SIZE_ALIASES.get(word, word), fallback)


def _safe_json(value, default):
    if not value:
        return default
    try:
        out = json.loads(value)
        return out if isinstance(out, type(default)) else default
    except (TypeError, ValueError):
        return default


def _cells_for_role(role: str) -> int:
    """A creature's footprint from the role line of its stat block ("Large monstrosity").

    Thin wrapper over _cells_for_size, so the 5e size table exists in exactly one place.
    """
    return _cells_for_size(role, 1)


def _encounter_participants(db, enc_id: int) -> list[dict]:
    """The encounter's combatants, ready to become tokens.

    A participant's stats live in `dm_encounter_npcs.creature_data` (a JSON copy of the
    monster), not in `dm_npcs` — monster rows are not created for every fight — so the name,
    size and role are read from there and the npc join is only a fallback.
    """
    rows = db.execute(
        "SELECT en.id, en.npc_id, en.hp_current, en.hp_max, en.initiative, en.defeated, "
        "en.creature_data, n.name AS npc_name "
        "FROM dm_encounter_npcs en LEFT JOIN dm_npcs n ON n.id = en.npc_id "
        "WHERE en.encounter_id = ? ORDER BY en.initiative DESC, en.id", (enc_id,)).fetchall()
    out = []
    for r in rows:
        r = dict(r)
        data = _safe_json(r.get("creature_data"), {})
        name = (data.get("name") or r.get("npc_name") or "Combatant").strip()
        role = str(data.get("role") or "")
        out.append({
            "en_id": r["id"],
            "name": name[:60],
            "hp_current": _inum(r.get("hp_current"), 0, -999, 9999),
            "hp_max": _inum(r.get("hp_max"), 0, 0, 9999),
            "cells": _cells_for_role(role),
            "defeated": bool(r.get("defeated")),
        })
    return out


def _snap_to_cell(x: float, y: float, map_row: dict) -> tuple[float, float]:
    """The centre of the cell a point falls in — the server's copy of the canvas' snapPoint.

    Square and hex both, because spawning onto a hex map with square-centre maths leaves every
    token visibly off its hex.
    """
    size = _inum(map_row.get("grid_size"), 50, 10, 400)
    ox = _inum(map_row.get("grid_offset_x"), 0, -400, 400)
    oy = _inum(map_row.get("grid_offset_y"), 0, -400, 400)
    if str(map_row.get("grid_type")) == "hex":
        radius = size / 2.0
        gx, gy = x - ox, y - oy
        q = (3 ** 0.5 / 3 * gx - gy / 3) / radius
        r = (2 / 3 * gy) / radius
        rx, ry, rz = _round_half_up(q), _round_half_up(-q - r), _round_half_up(r)
        dx, dy, dz = abs(rx - q), abs(ry - (-q - r)), abs(rz - r)
        if dx > dy and dx > dz:
            rx = -ry - rz
        elif dy > dz:
            ry = -rx - rz
        else:
            rz = -rx - ry
        return (3 ** 0.5 * radius * (rx + rz / 2) + ox, 1.5 * radius * rz + oy)
    return (_round_half_up((x - ox) / size - 0.5) * size + size / 2 + ox,
            _round_half_up((y - oy) / size - 0.5) * size + size / 2 + oy)


def _spawn_layout(count: int, map_row: dict, viewport=None) -> list[tuple[float, float]]:
    """A tidy block of cell centres, starting where the DM is actually looking.

    `viewport` is the canvas size the DM's browser reported; without it the anchor is a guess
    and fresh tokens can land off the edge of what they can see.
    """
    size = _inum(map_row.get("grid_size"), 50, 10, 400)
    vw, vh = 800.0, 600.0
    if isinstance(viewport, (list, tuple)) and len(viewport) == 2:
        try:
            vw = max(200.0, min(8000.0, float(viewport[0])))
            vh = max(200.0, min(8000.0, float(viewport[1])))
        except (TypeError, ValueError):
            vw, vh = 800.0, 600.0
    centre = None
    cam = _safe_json(map_row.get("camera"), {})
    if isinstance(cam, dict) and cam:
        try:
            # the camera is an offset applied to world space; the viewport centre in world
            # coordinates is what the canvas maps to
            centre = ((vw / 2) / float(cam.get("zoom") or 1) - float(cam.get("x") or 0),
                      (vh / 2) / float(cam.get("zoom") or 1) - float(cam.get("y") or 0))
        except (TypeError, ValueError, ZeroDivisionError):
            centre = None
    if not centre:
        centre = (size * 4.0, size * 3.0)
    cols = max(1, int(count ** 0.5 + 0.999))
    out = []
    for i in range(count):
        col = i % cols
        row = i // cols
        x = centre[0] + (col - (cols - 1) / 2.0) * size * 1.5
        y = centre[1] + (row - ((count - 1) // cols) / 2.0) * size * 1.5
        # land on cell centres (of the map's own grid shape) so snapping does not shuffle them
        # on the first drag
        out.append(_snap_to_cell(x, y, map_row))
    return out


@router.post("/api/dm/map/{map_id}/spawn-encounter", response_class=JSONResponse)
async def dm_map_spawn_encounter(map_id: int, request: Request):
    """Place an encounter's combatants on the map, linking each token to its tracker row.

    With `replace` the previous tokens for this encounter are removed first; without it,
    combatants already placed are left alone (so a mid-fight re-spawn never moves anyone).
    """
    user = require_user(request)
    data = await request.json()
    db = get_db()
    try:
        map_row = _own_map(db, user, map_id)
        if not map_row:
            return JSONResponse({"error": "Not found"}, status_code=404)
        enc_id = _inum(data.get("encounter_id"), 0, 0, 10 ** 9)
        enc = _require_owned(db, user, "dm_encounters", enc_id) if enc_id else None
        if not enc:
            return JSONResponse({"error": "Encounter not found"}, status_code=404)

        participants = _encounter_participants(db, enc_id)
        if not participants:
            return JSONResponse({"error": "That encounter has no combatants yet"}, status_code=400)
        viewport = data.get("viewport")      # [w, h] from the canvas, so tokens land on screen

        existing = {r[0] for r in db.execute(
            "SELECT encounter_en_id FROM dm_map_tokens WHERE map_id = ? AND encounter_en_id IS NOT NULL",
            (map_id,))}
        todo = participants
        if data.get("replace"):
            for en_id in existing:
                db.execute("DELETE FROM dm_map_tokens WHERE map_id = ? AND encounter_en_id = ?",
                           (map_id, en_id))
        else:
            todo = [p for p in participants if p["en_id"] not in existing]
        skipped = len(participants) - len(todo)

        room = TOKEN_MAX - db.execute(
            "SELECT COUNT(*) FROM dm_map_tokens WHERE map_id = ?", (map_id,)).fetchone()[0]
        placed = todo[:max(0, room)]
        spots = _spawn_layout(len(placed), map_row, viewport)
        for p, (x, y) in zip(placed, spots):
            db.execute(
                "INSERT INTO dm_map_tokens (map_id, x, y, w, h, kind, ref_name, label, hp_current, "
                "hp_max, hidden, z, encounter_en_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (map_id, x, y, p["cells"], p["cells"], "creature", p["name"], p["name"],
                 p["hp_current"], p["hp_max"], 0, 0, p["en_id"]))

        # Link the standing party characters too, when the encounter row knows them: a PC is a
        # character token (its portrait route), not a monster from the reference library.
        linked_chars = 0
        state = _safe_json(enc.get("combat_state"), {})
        party = state.get("player_participants") if isinstance(state, dict) else None
        # one layout for the whole arrival (creatures + party), so a PC never lands on a monster
        party_spots = _spawn_layout(len(placed) + len(party or []), map_row, viewport)
        for part in (party or []):
            if not isinstance(part, dict) or len(placed) + linked_chars >= room:
                continue
            cid = _inum(part.get("character_id") or part.get("id"), 0, 0, 10 ** 9)
            if not cid:
                continue
            already = db.execute(
                "SELECT COUNT(*) FROM dm_map_tokens WHERE map_id = ? AND character_id = ?",
                (map_id, cid)).fetchone()[0]
            if already:
                continue
            total = len(placed) + linked_chars + 1
            x, y = (party_spots[total - 1] if total - 1 < len(party_spots)
                    else _snap_to_cell(party_spots[-1][0], party_spots[-1][1], map_row))
            db.execute(
                "INSERT INTO dm_map_tokens (map_id, x, y, w, h, kind, ref_name, label, hp_current, "
                "hp_max, hidden, z, character_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (map_id, x, y, 1, 1, "character", str(part.get("name") or "")[:60],
                 str(part.get("name") or "")[:60], _inum(part.get("hp_current"), 0, -999, 9999),
                 _inum(part.get("hp_max"), 0, 0, 9999), 0, 0, cid))
            linked_chars += 1
        db.commit()
        return JSONResponse({"ok": True, "added": len(placed), "skipped": skipped,
                             "party_linked": linked_chars, "tokens": _tokens_of(db, map_id)})
    finally:
        db.close()


# ── the player view (second screen) ────────────────────────────────────────────────────

def _cell_key_of(x: float, y: float, map_row: dict) -> str:
    """The fog cell a token's centre falls in — the same maths as the canvas, on the server.

    Duplicated deliberately: the projection has to decide what the party can see, and trusting
    the client to tell us which cell a token occupies would let a stale client leak one.
    """
    size = _inum(map_row.get("grid_size"), 50, 10, 400)
    ox = _inum(map_row.get("grid_offset_x"), 0, -400, 400)
    oy = _inum(map_row.get("grid_offset_y"), 0, -400, 400)
    if str(map_row.get("grid_type")) == "hex":
        radius = size / 2.0
        # the offset moves the grid over the art on a hex map too: ignoring it meant a hex
        # battle map could not be aligned at all, while a square one could
        gx, gy = x - ox, y - oy
        q = (3 ** 0.5 / 3 * gx - gy / 3) / radius
        r = (2 / 3 * gy) / radius
        cx, cz, cy = q, r, -q - r
        rx, ry, rz = _round_half_up(cx), _round_half_up(cy), _round_half_up(cz)
        dx, dy, dz = abs(rx - cx), abs(ry - cy), abs(rz - cz)
        if dx > dy and dx > dz:
            rx = -ry - rz
        elif dy > dz:
            ry = -rx - rz
        else:
            rz = -rx - ry
        return f"{rx},{rz}"
    return f"{math.floor((x - ox) / size)},{math.floor((y - oy) / size)}"


def _player_state(db, map_row: dict) -> dict:
    """What the player view may see.

    Three things are withheld, and each for its own reason: the DM's drawing (it is the DM's
    notes), hidden tokens (the DM said so), and anything the fog still covers (the party has
    not been there). HP rides along but as a bar, not a number.
    """
    fog = _clean_fog(_safe_json(map_row.get("fog"), []))
    fog_on = bool(_inum(map_row.get("fog_on"), 0, 0, 1))
    revealed = set(fog)
    tokens = []
    for t in _tokens_of(db, map_row["id"]):
        if t.get("hidden"):
            continue
        if fog_on and _cell_key_of(t["x"], t["y"], map_row) not in revealed:
            continue
        tokens.append({
            "id": t["id"], "x": t["x"], "y": t["y"], "w": t["w"], "h": t["h"],
            "kind": t["kind"], "ref_name": t["ref_name"], "label": t["label"],
            "hp_current": t["hp_current"], "hp_max": t["hp_max"], "z": t["z"],
            "character_id": t["character_id"],
        })
    return {
        "map": {
            "id": map_row["id"], "name": map_row["name"], "image_path": map_row.get("image_path") or "",
            "grid_type": map_row.get("grid_type") or "square",
            "grid_size": _inum(map_row.get("grid_size"), 50, 10, 400),
            "grid_offset_x": _inum(map_row.get("grid_offset_x"), 0, -400, 400),
            "grid_offset_y": _inum(map_row.get("grid_offset_y"), 0, -400, 400),
        },
        # 1 = the DM's screen may dim what is unseen; the party simply does not get it
        "tokens": tokens, "fog": fog, "fog_on": int(fog_on),
        "camera": map_row.get("camera") or "",
        "shared": True,
    }


@router.get("/api/dm/map/{map_id}/state", response_class=JSONResponse)
async def dm_map_state(map_id: int, request: Request, k: str = ""):
    """The projected state a second screen renders.

    Authorised either by the owner's session or by the map's player key (`?k=`), so a TV or a
    tablet needs no login — and gets no DM drawings, no hidden tokens and nothing under the fog.
    """
    db = get_db()
    try:
        row = db.execute("SELECT * FROM dm_maps WHERE id = ?", (map_id,)).fetchone()
        if not row:
            return JSONResponse({"error": "Not found"}, status_code=404)
        row = dict(row)
        key = (row.get("player_key") or "").strip()
        if k and key and secrets.compare_digest(str(k), key):
            return JSONResponse(_player_state(db, row))
        user = get_current_user(request)
        if not user:
            return JSONResponse({"error": "login required"}, status_code=403)
        if not _is_admin(user) and row.get("user_id") != user["id"]:
            return JSONResponse({"error": "Not found"}, status_code=404)
        return JSONResponse(_player_state(db, row))
    finally:
        db.close()


@router.get("/api/dm/map/{map_id}/player-key", response_class=JSONResponse)
async def dm_map_player_key(map_id: int, request: Request):
    """The DM's share key for the second screen. Creates one on first ask."""
    user = require_user(request)
    db = get_db()
    try:
        row = _own_map(db, user, map_id)
        if not row:
            return JSONResponse({"error": "Not found"}, status_code=404)
        key = (row.get("player_key") or "").strip()
        if not key:
            key = secrets.token_hex(16)
            db.execute("UPDATE dm_maps SET player_key = ? WHERE id = ?", (key, map_id))
            db.commit()
        return JSONResponse({"ok": True, "player_key": key,
                             "url": f"/dm-map/{map_id}/player?k={key}"})
    finally:
        db.close()


@router.post("/api/dm/map/{map_id}/player-key", response_class=JSONResponse)
async def dm_map_player_key_set(map_id: int, request: Request):
    """Rotate the key, or revoke it (send an empty key) to close the second screen again."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    try:
        if not _own_map(db, user, map_id):
            return JSONResponse({"error": "Not found"}, status_code=404)
        wanted = str(data.get("key") or "").strip()
        key = wanted[:64] if wanted else ""
        db.execute("UPDATE dm_maps SET player_key = ? WHERE id = ?", (key, map_id))
        db.commit()
        return JSONResponse({"ok": True, "player_key": key,
                             "url": f"/dm-map/{map_id}/player?k={key}" if key else ""})
    finally:
        db.close()


@router.get("/dm-map/{map_id}/player", response_class=HTMLResponse)
async def dm_map_player_page(map_id: int, request: Request, k: str = ""):
    """The second screen: full-bleed canvas, no toolbar, no DM notes."""
    db = get_db()
    try:
        row = db.execute("SELECT * FROM dm_maps WHERE id = ?", (map_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Map not found")
        row = dict(row)
        key = (row.get("player_key") or "").strip()
        if not (k and key and secrets.compare_digest(str(k), key)):
            # no (valid) key: fall back to the owner's session, which a signed-in DM has
            user = require_user(request)
            if not _is_admin(user) and row.get("user_id") != user["id"]:
                raise HTTPException(status_code=404, detail="Map not found")
        return _render("map_player.html", request=request, the_map=row,
                       player_key=key, map_json=json.dumps({"id": row["id"]}))
    finally:
        db.close()


# ── the map page itself ────────────────────────────────────────────────────────────────

@router.get("/dm-map/{map_id}", response_class=HTMLResponse)
async def dm_map_page(map_id: int, request: Request):
    """The canvas. The map's data is fetched by static/vtt.js from /api/dm/map/{id} so the
    page shell stays cacheable and the canvas can re-read after a snapshot restore."""
    user = require_user(request)
    db = get_db()
    try:
        row = _own_map(db, user, map_id)
        if not row:
            raise HTTPException(status_code=404, detail="Map not found")
        return _render("map.html", request=request, the_map=row, map_json=json.dumps({"id": row["id"]}))
    finally:
        db.close()
