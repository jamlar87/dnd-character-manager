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
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from main import get_db, require_user, _require_owned, _is_admin, _user_where, STATIC
from services.images import decode_data_url, thumbnail_bytes

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


def _inum(value, default=0, lo=-9999, hi=9999) -> int:
    try:
        out = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, out))


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
        "w": _inum(raw.get("w"), 1, 1, 12),
        "h": _inum(raw.get("h"), 1, 1, 12),
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
        rows = db.execute(
            f"SELECT m.*, (SELECT COUNT(*) FROM dm_map_tokens t WHERE t.map_id = m.id) AS token_count, "
            f"(SELECT COUNT(*) FROM dm_map_scenes s WHERE s.map_id = m.id) AS scene_count "
            f"FROM dm_maps m {where} ORDER BY m.created_at DESC", params).fetchall()
        return JSONResponse({"count": len(rows), "maps": [dict(r) for r in rows]})
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
            "grid_offset_x, grid_offset_y, notes) VALUES (?,?,?,?,?,?,?,?)",
            (user["id"], _inum(data.get("campaign_id"), 0, 0, 10 ** 9) or None, name[:80],
             "hex" if str(data.get("grid_type")) == "hex" else "square",
             _inum(data.get("grid_size"), 50, 10, 400),
             _inum(data.get("grid_offset_x"), 0, -400, 400),
             _inum(data.get("grid_offset_y"), 0, -400, 400),
             str(data.get("notes") or "")[:2000]))
        db.commit()
        return JSONResponse({"ok": True, "id": cur.lastrowid})
    finally:
        db.close()


@router.get("/api/dm/map/{map_id}", response_class=JSONResponse)
async def dm_map_detail(map_id: int, request: Request):
    """One map with everything needed to draw it."""
    user = require_user(request)
    db = get_db()
    try:
        row = _own_map(db, user, map_id)
        if not row:
            return JSONResponse({"error": "Not found"}, status_code=404)
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
        if "camera" in data:
            cam = data.get("camera")
            sets.append("camera = ?")
            params.append(json.dumps(cam)[:400] if isinstance(cam, dict) else str(cam or "")[:400])
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
        thumb = thumbnail_bytes(blob, MAP_MAX_PX)
        if thumb:
            blob, media = thumb
        ext = EXT_BY_MEDIA.get(media, ".png")
        digest = hashlib.sha1(blob).hexdigest()[:12]
        filename = f"map-{map_id}-{digest}{ext}"
        MAP_DIR.mkdir(parents=True, exist_ok=True)
        (MAP_DIR / filename).write_bytes(blob)
        image_path = f"/static/maps/{filename}"
        db.execute("UPDATE dm_maps SET image_path = ? WHERE id = ?", (image_path, map_id))
        db.commit()
        _drop_map_image(db, row.get("image_path") or "")
        return JSONResponse({"ok": True, "image_path": image_path, "bytes": len(blob)})
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
        if not _own_map(db, user, map_id):
            return JSONResponse({"error": "Not found"}, status_code=404)
        count = db.execute("SELECT COUNT(*) FROM dm_map_tokens WHERE map_id = ?", (map_id,)).fetchone()[0]
        if count >= TOKEN_MAX:
            return JSONResponse({"error": f"a map holds at most {TOKEN_MAX} tokens"}, status_code=400)
        clean = _clean_token(data, map_id)
        if not clean:
            return JSONResponse({"error": "bad token payload"}, status_code=400)
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
        snapshot = json.dumps({"tokens": _tokens_of(db, map_id), "camera": row.get("camera") or ""})
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
        return JSONResponse({"ok": True, "tokens": tokens, "camera": camera or ""})
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
