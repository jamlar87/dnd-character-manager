"""History & Relationships API — character relationship CRUD + AI generation.

Extracted from routes/characters/all.py (2026-07-31). Imports helpers
from main and ai_routes only — never from all.py (avoids circulars).
"""

import json

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from main import get_db, require_user
from routes.characters.ai_routes import _call_ai

router = APIRouter()


@router.get("/api/character/{char_id}/relationships", response_class=JSONResponse)
async def get_relationships(char_id: int, request: Request):
    user = require_user(request)
    db = get_db()
    rows = db.execute(
        "SELECT * FROM character_relationships WHERE character_id = ? AND user_id = ? ORDER BY created_at DESC",
        (char_id, user["id"])
    ).fetchall()
    db.close()
    return JSONResponse([dict(r) for r in rows])

@router.post("/api/character/{char_id}/relationships", response_class=JSONResponse)
async def create_relationship(char_id: int, request: Request):
    user = require_user(request)
    data = await request.json()
    db = get_db()
    row = db.execute("SELECT id FROM characters WHERE id = ? AND user_id = ?", (char_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    name = data.get("name", "").strip()
    if not name:
        db.close()
        return JSONResponse({"error": "Name required"}, status_code=400)
    rel_type = data.get("relationship_type", "ally")
    description = data.get("description", "")
    npc_data = data.get("npc_data", {})
    ai_gen = 1 if data.get("ai_generated") else 0
    cursor = db.execute(
        "INSERT INTO character_relationships (character_id, user_id, name, relationship_type, description, npc_data, ai_generated) VALUES (?,?,?,?,?,?,?)",
        (char_id, user["id"], name, rel_type, description, json.dumps(npc_data), ai_gen)
    )
    rel_id = cursor.lastrowid
    db.commit()
    rel_row = dict(db.execute("SELECT * FROM character_relationships WHERE id = ?", (rel_id,)).fetchone())
    db.close()
    return JSONResponse(rel_row)

@router.post("/api/character/{char_id}/relationships/generate", response_class=JSONResponse)
async def generate_relationship(char_id: int, request: Request):
    user = require_user(request)
    data = await request.json()
    db = get_db()
    row = db.execute("SELECT * FROM characters WHERE id = ? AND user_id = ?", (char_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    char = dict(row)
    db.close()
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return JSONResponse({"error": "Prompt required"}, status_code=400)
    rel_type = data.get("relationship_type", "ally")
    npc_level = data.get("level", char.get("level", 1))
    char_name = char.get("name", "the character")
    char_race = char.get("race", "Human")
    char_class = char.get("class_name", "Fighter")
    char_level = char.get("level", 1)
    ai_prompt = (
        f'Create a D&D 5e NPC for a character\'s backstory.\n'
        f'Character: {char_name}, {char_race} {char_class} L{char_level}.\n'
        f'Relationship type: {rel_type}.\n'
        f'NPC level: {npc_level}.\n'
        f'Player\'s description: "{prompt}"\n\n'
        'Generate a vivid NPC with full combat stats. Return ONLY valid JSON (no markdown):\n'
        '{"name": "NPC Name", "race": "D&D race", "class": "class or occupation", '
        '"level": 1-20, '
        '"description": "2-3 sentence appearance and personality", '
        f'"relationship_detail": "1-2 sentences about their history with {char_name}", '
        '"stats": {"ac": 10-20, "hp": "XdY+Z", "speed": "30 ft.", '
        '"str": 8-20, "dex": 8-20, "con": 8-20, "int": 8-20, "wis": 8-20, "cha": 8-20, '
        '"skills": ["skill+mod"], "saves": ["save+mod"], '
        '"attacks": [{"name": "weapon", "bonus": "+X", "damage": "XdY+Z", "type": "piercing"}], '
        '"cr": "1/8 to 10"}}'
    )
    ai_text = await _call_ai(prompt=ai_prompt, label="npc-sheet")
    npc_data = {}
    name = prompt[:50]
    description = ""
    if ai_text:
        try:
            cleaned = ai_text.strip().removeprefix("```json").removesuffix("```").strip()
            ai_json = json.loads(cleaned)
            name = ai_json.get("name", name)
            description = (ai_json.get("description", "") + "\n\n" + ai_json.get("relationship_detail", "")).strip()
            npc_data = {"race": ai_json.get("race", ""), "class": ai_json.get("class", ""), "level": npc_level, "stats": ai_json.get("stats", {})}
        except (json.JSONDecodeError, AttributeError):
            description = ai_text[:500]
    # Return generated data only — frontend calls /relationships to save
    return JSONResponse({
        "name": name,
        "description": description,
        "npc_data": npc_data,
        "prompt": prompt,
        "relationship_type": rel_type,
        "level": npc_level,
        "ai_generated": True,
    })

@router.put("/api/character/{char_id}/relationships/{rel_id}", response_class=JSONResponse)
async def update_relationship(char_id: int, rel_id: int, request: Request):
    user = require_user(request)
    data = await request.json()
    db = get_db()
    row = db.execute("SELECT id FROM character_relationships WHERE id = ? AND character_id = ? AND user_id = ?",
                     (rel_id, char_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Relationship not found")
    updates = {}
    for field in ["name", "relationship_type", "description"]:
        if field in data:
            updates[field] = data[field]
    if "npc_data" in data:
        updates["npc_data"] = json.dumps(data["npc_data"])
    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        db.execute(f"UPDATE character_relationships SET {set_clause} WHERE id = ?", list(updates.values()) + [rel_id])
        db.commit()
    rel_row = dict(db.execute("SELECT * FROM character_relationships WHERE id = ?", (rel_id,)).fetchone())
    db.close()
    return JSONResponse(rel_row)

@router.delete("/api/character/{char_id}/relationships/{rel_id}", response_class=JSONResponse)
async def delete_relationship(char_id: int, rel_id: int, request: Request):
    user = require_user(request)
    db = get_db()
    db.execute("DELETE FROM character_relationships WHERE id = ? AND character_id = ? AND user_id = ?",
               (rel_id, char_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})
