"""Campaign packs — one JSON file carrying a whole campaign.

A pack is self-contained: the campaign, the characters and NPCs it lists, its maps with their
tokens and fog/drawing, the encounters those maps were built from, and the map images
base64-embedded so the file can be moved between machines with nothing else.

What a pack deliberately does NOT carry: `user_id` (there is no importing as someone else),
`player_key` (a share secret must never travel in a file), `created_at`, and an encounter's
`shared` flag. The importer always owns what it imports, and ids are remapped — a pack must be
safe to import twice, and importing must never touch an existing row.

The character half reuses routes/characters/transfer.py's builder and inserter rather than
re-implementing the field whitelist: two copies would drift and a pack would then hand back a
character the sheet cannot read.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from main import get_db, require_user, _require_owned, _is_admin, STATIC, _json_list

router = APIRouter()

PACK_FORMAT = "dnd-campaign-pack"
PACK_VERSION = 1
MAX_IMAGES = 60
MAX_IMAGE_BYTES = 40 * 1024 * 1024        # a pack that embeds more than this is not a file anyone
MAX_CHARACTERS = 200                      # wants to email; the images can be moved by hand
MAX_NPCS = 500
MAX_ENCOUNTERS = 200
MAX_MAPS = 60
MAX_SCENES_PER_MAP = 100
MAP_DIR = Path(STATIC) / "maps"

# Never carried in a pack: identity, ownership, tenancy, share secrets.
_DROP = {"id", "user_id", "created_at", "player_key", "shared"}
_EXT_BY_MEDIA = {"image/webp": ".webp", "image/png": ".png", "image/jpeg": ".jpg"}


def _cols(db, table: str) -> list[str]:
    """The table's columns minus the ones a pack must never carry.

    Read from the schema rather than hardcoded so a new column travels automatically — the drop
    list is the deliberate part.
    """
    return [r[1] for r in db.execute(f"PRAGMA table_info({table})") if r[1] not in _DROP]


def _export_row(row, cols) -> dict:
    out = {k: row[k] for k in cols if k in row.keys()}
    out["_old_id"] = row["id"]          # the importer needs the old id to remap references
    return out


def _child_rows(db, table: str, where: str, params) -> list[dict]:
    cols = _cols(db, table)
    return [_export_row(r, cols) for r in db.execute(f"SELECT * FROM {table} {where}", params)]


def _blob_ids(blob) -> list[int]:
    """Ids out of a campaign's characters/npcs blob — entries are dicts (or bare ids)."""
    out: list[int] = []
    for entry in _json_list(blob):
        raw = entry.get("id") if isinstance(entry, dict) else entry
        try:
            cid = int(raw)
        except (TypeError, ValueError):
            continue
        if cid and cid not in out:
            out.append(cid)
    return out


def _ensure_sentinel_npc(db, user_id: int) -> None:
    """Make sure the creature-only placeholder exists before participants point at it.

    `dm_encounter_npcs.npc_id = -1` means "this combatant is a stat block, not an NPC row", and
    the FK is really enforced (services/db.py turns foreign_keys on) — so the placeholder has to
    exist. This mirrors what the encounter-import route already does, rather than inventing NULL
    where the rest of the app uses the sentinel.
    """
    if not db.execute("SELECT id FROM dm_npcs WHERE id = -1").fetchone():
        db.execute("INSERT INTO dm_npcs (id, user_id, name, race, class_name, level, "
                   "hp_current, hp_max, ac) VALUES (-1, ?, '__sentinel__', '', '', 0, 1, 1, 10)",
                   (user_id,))


def _insert_row(db, table: str, data: dict) -> int:
    cols = [k for k in data if not k.startswith("_")]
    marks = ", ".join("?" for _ in cols)
    cur = db.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({marks})",
                     [data[c] for c in cols])
    return cur.lastrowid


# ── export ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/dm/campaign/{camp_id}/export", response_class=Response)
async def campaign_export(camp_id: int, request: Request, images: str = "embed"):
    """Download the campaign as a pack. `?images=skip` leaves the map images out."""
    from routes.characters.transfer import build_character_payload   # function-local: see Pitfall 16

    user = require_user(request)
    db = get_db()
    try:
        camp_row = _require_owned(db, user, "dm_campaigns", camp_id)
        if not camp_row:
            raise HTTPException(status_code=404, detail="Campaign not found")
        camp = dict(camp_row)
        warnings: list[str] = []

        characters = []
        for cid in _blob_ids(camp.get("characters"))[:MAX_CHARACTERS]:
            row = db.execute("SELECT * FROM characters WHERE id = ? AND user_id = ?",
                             (cid, user["id"])).fetchone()
            if not row:
                warnings.append(f"character {cid} is listed by the campaign but no longer exists")
                continue
            payload = build_character_payload(db, dict(row), user["id"])
            payload["_old_id"] = cid
            characters.append(payload)

        npcs = []
        for nid in _blob_ids(camp.get("npcs"))[:MAX_NPCS]:
            row = db.execute("SELECT * FROM dm_npcs WHERE id = ? AND user_id = ?",
                             (nid, user["id"])).fetchone()
            if not row:
                warnings.append(f"NPC {nid} is listed by the campaign but no longer exists")
                continue
            npcs.append(_export_row(row, _cols(db, "dm_npcs")))

        maps = []
        images_out: dict[str, str] = {}
        image_bytes = 0
        for m in db.execute("SELECT * FROM dm_maps WHERE campaign_id = ? AND user_id = ?",
                            (camp_id, user["id"])).fetchall()[:MAX_MAPS]:
            entry = _export_row(m, _cols(db, "dm_maps"))
            entry["tokens"] = _child_rows(db, "dm_map_tokens", "WHERE map_id = ?", (m["id"],))
            entry["scenes"] = _child_rows(db, "dm_map_scenes", "WHERE map_id = ?", (m["id"],))
            maps.append(entry)

            path = str(m["image_path"] or "")
            if images == "embed" and path.startswith("/static/maps/"):
                src = MAP_DIR / Path(path).name
                try:
                    blob = src.read_bytes()
                except OSError:
                    warnings.append(f"map '{m['name']}' has no image file on disk")
                    continue
                if image_bytes + len(blob) > MAX_IMAGE_BYTES:
                    warnings.append(
                        f"'{Path(path).name}' was left out: the pack already holds "
                        f"{image_bytes // (1024 * 1024)} MB of images (limit "
                        f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB)")
                    continue
                images_out[Path(path).name] = base64.b64encode(blob).decode()
                image_bytes += len(blob)
        if images == "skip" and any(m.get("image_path") for m in maps):
            warnings.append("map images were not embedded (?images=skip)")

        # The encounters these maps were built from, with their combatants.
        enc_ids: list[int] = []
        for m in maps:
            for t in m["tokens"]:
                eid = t.get("encounter_en_id")
                if not eid:
                    continue
                row = db.execute("SELECT encounter_id FROM dm_encounter_npcs WHERE id = ?",
                                 (eid,)).fetchone()
                if row and row["encounter_id"] not in enc_ids:
                    enc_ids.append(row["encounter_id"])
        encounters = []
        for eid in enc_ids[:MAX_ENCOUNTERS]:
            erow = db.execute("SELECT * FROM dm_encounters WHERE id = ? AND user_id = ?",
                              (eid, user["id"])).fetchone()
            if not erow:
                warnings.append(f"encounter {eid} belongs to a map but is gone")
                continue
            entry = _export_row(erow, _cols(db, "dm_encounters"))
            entry["participants"] = _child_rows(db, "dm_encounter_npcs", "WHERE encounter_id = ?",
                                                (eid,))
            encounters.append(entry)

        pack = {
            "format": PACK_FORMAT,
            "version": PACK_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "campaign": {k: v for k, v in camp.items() if k not in _DROP},
            "characters": characters,
            "npcs": npcs,
            "encounters": encounters,
            "maps": maps,
            "images": images_out,
        }
        if warnings:
            pack["warnings"] = warnings
    finally:
        db.close()

    fname = "".join(c for c in (camp.get("name") or "campaign") if c.isalnum() or c in " -_").strip()
    return Response(
        content=json.dumps(pack, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{fname or "campaign"}-pack.json"'},
    )


# ── import ─────────────────────────────────────────────────────────────────────────────

@router.post("/api/dm/campaign/import", response_class=JSONResponse)
async def campaign_pack_import(request: Request):
    """Create a NEW campaign from a pack, owned by the importer.

    Never merges into an existing campaign and never updates an existing row: every id in the
    pack is remapped, so importing the same pack twice gives two independent campaigns.
    """
    from routes.characters.transfer import insert_character        # function-local: see Pitfall 16

    user = require_user(request)
    try:
        pack = await request.json()
    except Exception:
        return JSONResponse({"error": "That file is not valid JSON"}, status_code=400)
    if not isinstance(pack, dict) or pack.get("format") != PACK_FORMAT:
        return JSONResponse({"error": "That is not a campaign pack"}, status_code=400)
    try:
        version = int(pack.get("version") or 0)
    except (TypeError, ValueError):
        version = 0
    if version != PACK_VERSION:
        return JSONResponse(
            {"error": f"pack version {pack.get('version')!r} is not supported "
                      f"(this app reads version {PACK_VERSION})"}, status_code=400)

    campaign = pack.get("campaign")
    if not isinstance(campaign, dict) or not str(campaign.get("name") or "").strip():
        return JSONResponse({"error": "The pack has no campaign name"}, status_code=400)

    db = get_db()
    warnings: list[str] = []
    counts = {"characters": 0, "npcs": 0, "encounters": 0, "maps": 0, "tokens": 0, "setups": 0,
              "images": 0}
    try:
        # ── the campaign itself ──
        camp_cols = [c for c in _cols(db, "dm_campaigns") if c not in ("characters", "npcs")]
        new_camp = {k: campaign.get(k) for k in camp_cols if k in campaign}
        new_camp["name"] = str(campaign["name"])[:120]
        new_camp["user_id"] = user["id"]
        new_camp["characters"] = "[]"       # filled in once the characters exist
        new_camp["npcs"] = "[]"
        new_camp_id = _insert_row(db, "dm_campaigns", new_camp)

        # ── characters (their own import path, so a pack character is a real character) ──
        char_map: dict[int, int] = {}
        new_char_entries: list[dict] = []
        for entry in (pack.get("characters") or [])[:MAX_CHARACTERS]:
            if not isinstance(entry, dict):
                continue
            try:
                new_id, name = insert_character(db, user["id"], entry)
            except ValueError as exc:
                warnings.append(f"skipped a character: {exc}")
                continue
            old = entry.get("_old_id")
            if old is not None:
                try:
                    char_map[int(old)] = new_id
                except (TypeError, ValueError):
                    pass
            char = entry.get("character") or {}
            new_char_entries.append({
                "id": new_id, "name": name,
                "class_name": str(char.get("class_name") or ""),
                "level": int(char.get("level") or 1),
                "race": str(char.get("race") or ""),
                "status": "active",
            })
            counts["characters"] += 1

        # ── NPCs ──
        npc_map: dict[int, int] = {}
        new_npc_entries: list[dict] = []
        for entry in (pack.get("npcs") or [])[:MAX_NPCS]:
            if not isinstance(entry, dict):
                continue
            data = {k: v for k, v in entry.items() if not k.startswith("_")}
            if not str(data.get("name") or "").strip():
                continue
            data["user_id"] = user["id"]
            new_id = _insert_row(db, "dm_npcs", data)
            old = entry.get("_old_id")
            if old is not None:
                try:
                    npc_map[int(old)] = new_id
                except (TypeError, ValueError):
                    pass
            new_npc_entries.append({"id": new_id, "name": data.get("name"),
                                    "role": data.get("role") or "", "status": "alive"})
            counts["npcs"] += 1

        # ── encounters and their combatants ──
        if any((e.get("participants") for e in (pack.get("encounters") or []) if isinstance(e, dict))):
            _ensure_sentinel_npc(db, user["id"])
        enc_map: dict[int, int] = {}
        part_map: dict[int, int] = {}
        for entry in (pack.get("encounters") or [])[:MAX_ENCOUNTERS]:
            if not isinstance(entry, dict):
                continue
            data = {k: v for k, v in entry.items()
                    if not k.startswith("_") and k != "participants"}
            data["user_id"] = user["id"]
            data["shared"] = 0              # never import a share flag
            new_enc_id = _insert_row(db, "dm_encounters", data)
            old = entry.get("_old_id")
            if old is not None:
                try:
                    enc_map[int(old)] = new_enc_id
                except (TypeError, ValueError):
                    pass
            for part in (entry.get("participants") or []):
                if not isinstance(part, dict):
                    continue
                pdata = {k: v for k, v in part.items() if not k.startswith("_")}
                pdata["encounter_id"] = new_enc_id
                old_npc = pdata.get("npc_id")
                try:
                    pdata["npc_id"] = npc_map.get(int(old_npc), -1) if old_npc else -1
                except (TypeError, ValueError):
                    pdata["npc_id"] = -1
                new_part_id = _insert_row(db, "dm_encounter_npcs", pdata)
                if part.get("_old_id") is not None:
                    try:
                        part_map[int(part["_old_id"])] = new_part_id
                    except (TypeError, ValueError):
                        pass
            counts["encounters"] += 1

        # ── maps, their tokens, their setups, and their images ──
        for entry in (pack.get("maps") or [])[:MAX_MAPS]:
            if not isinstance(entry, dict):
                continue
            data = {k: v for k, v in entry.items()
                    if not k.startswith("_") and k not in ("tokens", "scenes")}
            data["user_id"] = user["id"]
            data["campaign_id"] = new_camp_id
            data["player_key"] = ""          # a share secret must not travel in a pack
            old_image = str(data.get("image_path") or "")
            data["image_path"] = ""
            new_map_id = _insert_row(db, "dm_maps", data)

            if old_image.startswith("/static/maps/"):
                b64 = (pack.get("images") or {}).get(Path(old_image).name)
                if b64:
                    try:
                        blob = base64.b64decode(b64, validate=False)
                    except Exception:
                        blob = b""
                    if blob:
                        digest = hashlib.sha1(blob).hexdigest()[:12]
                        ext = Path(old_image).suffix or ".png"
                        fname = f"map-{new_map_id}-{digest}{ext}"
                        MAP_DIR.mkdir(parents=True, exist_ok=True)
                        (MAP_DIR / fname).write_bytes(blob)
                        db.execute("UPDATE dm_maps SET image_path = ? WHERE id = ?",
                                   (f"/static/maps/{fname}", new_map_id))
                        counts["images"] += 1
                else:
                    warnings.append(f"map '{data.get('name')}' came without its image")

            for t in (entry.get("tokens") or []):
                if not isinstance(t, dict):
                    continue
                tdata = {k: v for k, v in t.items() if not k.startswith("_")}
                tdata["map_id"] = new_map_id
                for fk, mapping in (("encounter_en_id", part_map), ("character_id", char_map)):
                    raw = tdata.get(fk)
                    if raw:
                        try:
                            tdata[fk] = mapping.get(int(raw))
                        except (TypeError, ValueError):
                            tdata[fk] = None
                    if not tdata.get(fk):
                        tdata[fk] = None
                _insert_row(db, "dm_map_tokens", tdata)
                counts["tokens"] += 1

            for s in (entry.get("scenes") or [])[:MAX_SCENES_PER_MAP]:
                if not isinstance(s, dict):
                    continue
                sdata = {k: v for k, v in s.items() if not k.startswith("_")}
                sdata["map_id"] = new_map_id
                _insert_row(db, "dm_map_scenes", sdata)
                counts["setups"] += 1

            counts["maps"] += 1

        # the campaign's own rosters, now pointing at the NEW ids
        db.execute("UPDATE dm_campaigns SET characters = ?, npcs = ? WHERE id = ?",
                   (json.dumps(new_char_entries, ensure_ascii=False),
                    json.dumps(new_npc_entries, ensure_ascii=False), new_camp_id))
        db.commit()
    finally:
        db.close()

    return JSONResponse({"ok": True, "campaign_id": new_camp_id, "name": new_camp.get("name"),
                         "counts": counts, "warnings": warnings})
