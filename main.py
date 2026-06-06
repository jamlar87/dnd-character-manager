"""D&D Character Manager — FastAPI webapp with multi-user character tracking."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Form, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader
from passlib.context import CryptContext

# ── Config ──────────────────────────────────────────────────────────────────

HERE = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DND_DATA_DIR", str(HERE / "data")))
DB_PATH = DATA_DIR / "characters.db"
TEMPLATES = HERE / "templates"
STATIC = HERE / "static"
SECRET_KEY = os.environ.get("SECRET_KEY", "dnd-dev-secret-change-me")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_jinja = Environment(loader=FileSystemLoader(str(TEMPLATES)))

# ── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(title="D&D Character Manager")

# ── DB ──────────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db

def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            race TEXT NOT NULL,
            subrace TEXT DEFAULT '',
            class_name TEXT NOT NULL,
            subclass TEXT DEFAULT '',
            level INTEGER DEFAULT 1,
            background TEXT DEFAULT '',
            alignment TEXT DEFAULT '',
            strength INTEGER DEFAULT 10,
            dexterity INTEGER DEFAULT 10,
            constitution INTEGER DEFAULT 10,
            intelligence INTEGER DEFAULT 10,
            wisdom INTEGER DEFAULT 10,
            charisma INTEGER DEFAULT 10,
            hp_max INTEGER DEFAULT 10,
            hp_current INTEGER DEFAULT 10,
            temp_hp INTEGER DEFAULT 0,
            ac INTEGER DEFAULT 10,
            speed INTEGER DEFAULT 30,
            proficiency_bonus INTEGER DEFAULT 2,
            hit_dice TEXT DEFAULT '1d8',
            hit_dice_used INTEGER DEFAULT 0,
            death_saves_success INTEGER DEFAULT 0,
            death_saves_fail INTEGER DEFAULT 0,
            skills TEXT DEFAULT '[]',
            tool_proficiencies TEXT DEFAULT '[]',
            weapon_proficiencies TEXT DEFAULT '[]',
            armor_proficiencies TEXT DEFAULT '[]',
            languages TEXT DEFAULT '[]',
            features TEXT DEFAULT '[]',
            inventory TEXT DEFAULT '[]',
            equipped TEXT DEFAULT '[]',
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS character_spells (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            character_id INTEGER NOT NULL,
            spell_name TEXT NOT NULL,
            spell_level INTEGER DEFAULT 0,
            prepared INTEGER DEFAULT 0,
            slots_max INTEGER DEFAULT 0,
            slots_used INTEGER DEFAULT 0,
            FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
    """)
    db.commit()
    db.close()

# ── Auth ────────────────────────────────────────────────────────────────────

def _hash(password: str) -> str:
    return pwd_context.hash(password)

def _verify(password: str, hash_: str) -> bool:
    return pwd_context.verify(password, hash_)

def _get_user(email: str) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    db.close()
    return dict(row) if row else None

def _create_session(user_id: int) -> str:
    import secrets
    token = secrets.token_hex(32)
    db = get_db()
    db.execute("INSERT INTO sessions (user_id, token) VALUES (?, ?)", (user_id, token))
    db.commit()
    db.close()
    return token

def _get_user_by_token(token: str) -> dict | None:
    db = get_db()
    row = db.execute("""
        SELECT u.* FROM users u
        JOIN sessions s ON s.user_id = u.id
        WHERE s.token = ?
    """, (token,)).fetchone()
    db.close()
    return dict(row) if row else None

def get_current_user(request: Request) -> dict | None:
    token = request.cookies.get("dnd_token")
    if not token:
        return None
    return _get_user_by_token(token)

def require_user(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user

# ── Render helper ───────────────────────────────────────────────────────────

def _render(template: str, request: Request | None = None, **ctx) -> HTMLResponse:
    user = get_current_user(request) if request else None
    content = _jinja.get_template(template).render(
        user=user,
        request=request,
        **ctx
    )
    return HTMLResponse(content)

# ── D&D Data — race/class/skill/etc (embedded constants) ────────────────────

RACES = {
    "Dwarf": {"subraces": ["Hill Dwarf", "Mountain Dwarf"], "asi": {"constitution": 2}, "speed": 25, "darkvision": 60, "languages": ["Common", "Dwarvish"], "traits": ["Dwarven Resilience", "Stonecunning"]},
    "Elf": {"subraces": ["High Elf", "Wood Elf", "Dark Elf (Drow)"], "asi": {"dexterity": 2}, "speed": 30, "darkvision": 60, "languages": ["Common", "Elvish"], "traits": ["Keen Senses", "Fey Ancestry", "Trance"]},
    "Halfling": {"subraces": ["Lightfoot Halfling", "Stout Halfling"], "asi": {"dexterity": 2}, "speed": 25, "darkvision": 0, "languages": ["Common", "Halfling"], "traits": ["Lucky", "Brave", "Halfling Nimbleness"]},
    "Human": {"subraces": [], "asi": {"strength": 1, "dexterity": 1, "constitution": 1, "intelligence": 1, "wisdom": 1, "charisma": 1}, "speed": 30, "darkvision": 0, "languages": ["Common"], "traits": []},
    "Dragonborn": {"subraces": [], "asi": {"strength": 2, "charisma": 1}, "speed": 30, "darkvision": 0, "languages": ["Common", "Draconic"], "traits": ["Draconic Ancestry", "Breath Weapon", "Damage Resistance"]},
    "Gnome": {"subraces": ["Forest Gnome", "Rock Gnome"], "asi": {"intelligence": 2}, "speed": 25, "darkvision": 60, "languages": ["Common", "Gnomish"], "traits": ["Gnome Cunning"]},
    "Half-Elf": {"subraces": [], "asi": {"charisma": 2}, "speed": 30, "darkvision": 60, "languages": ["Common", "Elvish"], "traits": ["Fey Ancestry", "Skill Versatility"]},
    "Half-Orc": {"subraces": [], "asi": {"strength": 2, "constitution": 1}, "speed": 30, "darkvision": 60, "languages": ["Common", "Orc"], "traits": ["Relentless Endurance", "Savage Attacks"]},
    "Tiefling": {"subraces": [], "asi": {"charisma": 2, "intelligence": 1}, "speed": 30, "darkvision": 60, "languages": ["Common", "Infernal"], "traits": ["Hellish Resistance", "Infernal Legacy"]},
}

SUBASIS = {
    "Hill Dwarf": {"wisdom": 1},
    "Mountain Dwarf": {"strength": 2},
    "High Elf": {"intelligence": 1},
    "Wood Elf": {"wisdom": 1},
    "Dark Elf (Drow)": {"charisma": 1},
    "Lightfoot Halfling": {"charisma": 1},
    "Stout Halfling": {"constitution": 1},
    "Forest Gnome": {"dexterity": 1},
    "Rock Gnome": {"constitution": 1},
}

CLASSES = {
    "Barbarian": {"hd": 12, "skills": ["Animal Handling","Athletics","Intimidation","Nature","Perception","Survival"], "skill_count": 2, "saves": ["strength","constitution"], "subclasses": ["Path of the Berserker","Path of the Totem Warrior"]},
    "Bard": {"hd": 8, "skills": "all", "skill_count": 3, "saves": ["dexterity","charisma"], "subclasses": ["College of Lore","College of Valor"]},
    "Cleric": {"hd": 8, "skills": ["History","Insight","Medicine","Persuasion","Religion"], "skill_count": 2, "saves": ["wisdom","charisma"], "subclasses": ["Knowledge Domain","Life Domain","Light Domain","Nature Domain","Tempest Domain","Trickery Domain","War Domain"]},
    "Druid": {"hd": 8, "skills": ["Arcana","Animal Handling","Insight","Medicine","Nature","Perception","Religion","Survival"], "skill_count": 2, "saves": ["intelligence","wisdom"], "subclasses": ["Circle of the Land","Circle of the Moon"]},
    "Fighter": {"hd": 10, "skills": ["Acrobatics","Animal Handling","Athletics","History","Insight","Intimidation","Perception","Survival"], "skill_count": 2, "saves": ["strength","constitution"], "subclasses": ["Champion","Battle Master","Eldritch Knight"]},
    "Monk": {"hd": 8, "skills": ["Acrobatics","Athletics","History","Insight","Religion","Stealth"], "skill_count": 2, "saves": ["strength","dexterity"], "subclasses": ["Way of the Open Hand","Way of Shadow","Way of the Four Elements"]},
    "Paladin": {"hd": 10, "skills": ["Athletics","Insight","Intimidation","Medicine","Persuasion","Religion"], "skill_count": 2, "saves": ["wisdom","charisma"], "subclasses": ["Oath of Devotion","Oath of the Ancients","Oath of Vengeance"]},
    "Ranger": {"hd": 10, "skills": ["Animal Handling","Athletics","Insight","Investigation","Nature","Perception","Stealth","Survival"], "skill_count": 3, "saves": ["strength","dexterity"], "subclasses": ["Hunter","Beast Master"]},
    "Rogue": {"hd": 8, "skills": ["Acrobatics","Athletics","Deception","Insight","Intimidation","Investigation","Perception","Performance","Persuasion","Sleight of Hand","Stealth"], "skill_count": 4, "saves": ["dexterity","intelligence"], "subclasses": ["Thief","Assassin","Arcane Trickster"]},
    "Sorcerer": {"hd": 6, "skills": ["Arcana","Deception","Insight","Intimidation","Persuasion","Religion"], "skill_count": 2, "saves": ["constitution","charisma"], "subclasses": ["Draconic Bloodline","Wild Magic"]},
    "Warlock": {"hd": 8, "skills": ["Arcana","Deception","History","Intimidation","Investigation","Nature","Religion"], "skill_count": 2, "saves": ["wisdom","charisma"], "subclasses": ["The Archfey","The Fiend","The Great Old One"]},
    "Wizard": {"hd": 6, "skills": ["Arcana","History","Insight","Investigation","Medicine","Religion"], "skill_count": 2, "saves": ["intelligence","wisdom"], "subclasses": ["Abjuration","Conjuration","Divination","Enchantment","Evocation","Illusion","Necromancy","Transmutation"]},
}

SKILL_ABILITIES = {
    "Acrobatics":"dexterity","Animal Handling":"wisdom","Arcana":"intelligence",
    "Athletics":"strength","Deception":"charisma","History":"intelligence",
    "Insight":"wisdom","Intimidation":"charisma","Investigation":"intelligence",
    "Medicine":"wisdom","Nature":"intelligence","Perception":"wisdom",
    "Performance":"charisma","Persuasion":"charisma","Religion":"intelligence",
    "Sleight of Hand":"dexterity","Stealth":"dexterity","Survival":"wisdom",
}

ALL_SKILLS = sorted(SKILL_ABILITIES.keys())

BACKGROUNDS = ["Acolyte","Charlatan","Criminal","Entertainer","Folk Hero","Guild Artisan","Hermit","Noble","Outlander","Sage","Sailor","Soldier","Urchin"]
ALIGNMENTS = ["Lawful Good","Neutral Good","Chaotic Good","Lawful Neutral","True Neutral","Chaotic Neutral","Lawful Evil","Neutral Evil","Chaotic Evil"]

# ── Routes: Auth ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/dashboard", 303)
    return _render("landing.html", request=request)

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return _render("register.html", request=request)

@app.post("/register")
async def register(request: Request, email: str = Form(...), password: str = Form(...)):
    if len(password) < 6:
        return _render("register.html", request=request, error="Password must be at least 6 characters")
    db = get_db()
    try:
        db.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)",
                   (email.lower().strip(), _hash(password)))
        db.commit()
        user = _get_user(email.lower().strip())
        token = _create_session(user["id"])
        resp = RedirectResponse("/dashboard", 303)
        resp.set_cookie("dnd_token", token, httponly=True, max_age=60*60*24*30, samesite="lax")
        return resp
    except sqlite3.IntegrityError:
        return _render("register.html", request=request, error="Email already registered")
    finally:
        db.close()

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return _render("login.html", request=request)

@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    user = _get_user(email.lower().strip())
    if not user or not _verify(password, user["password_hash"]):
        return _render("login.html", request=request, error="Invalid email or password")
    token = _create_session(user["id"])
    resp = RedirectResponse("/dashboard", 303)
    resp.set_cookie("dnd_token", token, httponly=True, max_age=60*60*24*30, samesite="lax")
    return resp

@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("dnd_token")
    if token:
        db = get_db()
        db.execute("DELETE FROM sessions WHERE token = ?", (token,))
        db.commit()
        db.close()
    resp = RedirectResponse("/", 303)
    resp.delete_cookie("dnd_token")
    return resp

# ── Routes: Dashboard ───────────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = require_user(request)
    db = get_db()
    chars = [dict(r) for r in db.execute(
        "SELECT * FROM characters WHERE user_id = ? ORDER BY created_at DESC", (user["id"],)
    ).fetchall()]
    db.close()
    for c in chars:
        for f in ("skills","features","inventory","equipped","languages"):
            try:
                c[f] = json.loads(c[f])
            except (json.JSONDecodeError, TypeError):
                c[f] = []
    return _render("dashboard.html", request=request, characters=chars)

# ── Routes: Character Creation Wizard ──────────────────────────────────────

@app.get("/create", response_class=HTMLResponse)
async def create_character_page(request: Request):
    require_user(request)
    return _render("create.html", request=request,
        races=RACES, subasis=SUBASIS, classes=CLASSES,
        all_skills=ALL_SKILLS, skill_abilities=SKILL_ABILITIES,
        backgrounds=BACKGROUNDS, alignments=ALIGNMENTS)

@app.post("/api/character/create", response_class=JSONResponse)
async def api_create_character(request: Request):
    user = require_user(request)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    # Compute ability scores
    def _asi(base, bonuses):
        stats = {}
        for stat in ["strength","dexterity","constitution","intelligence","wisdom","charisma"]:
            stats[stat] = int(base.get(stat, 10) or 10) + int(bonuses.get(stat, 0) or 0)
        return stats

    race_name = data.get("race", "")
    subrace = data.get("subrace", "")
    class_name = data.get("class_name", "")
    subclass = data.get("subclass", "")
    level = max(1, int(data.get("level", 1) or 1))
    name = data.get("name", "").strip()
    if not name or not race_name or not class_name:
        return JSONResponse({"error": "Name, race, and class required"}, status_code=400)

    # Race ASIs
    race_data = RACES.get(race_name, {})
    race_asi = dict(race_data.get("asi", {}))
    if subrace and subrace in SUBASIS:
        for k, v in SUBASIS[subrace].items():
            race_asi[k] = race_asi.get(k, 0) + v
    # Half-Elf: +1 to two other abilities (user picks from data.asi_picks)
    asi_picks = data.get("asi_picks", [])
    if race_name == "Half-Elf" and len(asi_picks) == 2:
        for a in asi_picks:
            if a in race_asi:
                race_asi[a] = race_asi.get(a, 0) + 1

    stats = _asi(data.get("abilities", {}), race_asi)

    class_data = CLASSES.get(class_name, {})
    hd = class_data.get("hd", 8)
    hp = hd + (stats["constitution"] - 10) // 2
    hp_max = hp + (hd // 2 + 1 + (stats["constitution"] - 10) // 2) * (level - 1)

    prof_bonus = 2 + (level - 1) // 4
    ac_base = 10 + (stats["dexterity"] - 10) // 2

    db = get_db()
    cur = db.execute("""
        INSERT INTO characters (user_id, name, race, subrace, class_name, subclass,
        level, background, alignment, strength, dexterity, constitution, intelligence,
        wisdom, charisma, hp_max, hp_current, ac, speed,
        proficiency_bonus, hit_dice, skills, features, languages, tool_proficiencies,
        weapon_proficiencies, armor_proficiencies, inventory, equipped)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        user["id"], name, race_name, subrace, class_name, subclass, level,
        data.get("background",""), data.get("alignment",""),
        stats["strength"], stats["dexterity"], stats["constitution"],
        stats["intelligence"], stats["wisdom"], stats["charisma"],
        hp_max, hp_max, ac_base, race_data.get("speed", 30),
        prof_bonus, f"1d{hd}",
        json.dumps(data.get("skills", [])), json.dumps([]), json.dumps(race_data.get("languages",["Common"])),
        json.dumps([]), json.dumps([]), json.dumps([]),
        json.dumps(data.get("equipment", [])), json.dumps([]),
    ))
    char_id = cur.lastrowid
    db.commit()
    db.close()
    return JSONResponse({"id": char_id, "name": name})

# ── Routes: Character Sheet ────────────────────────────────────────────────

@app.get("/character/{char_id}", response_class=HTMLResponse)
async def character_sheet(char_id: int, request: Request):
    user = require_user(request)
    db = get_db()
    row = db.execute("SELECT * FROM characters WHERE id = ? AND user_id = ?",
                     (char_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")

    char = dict(row)
    for f in ("skills","features","inventory","equipped","languages","tool_proficiencies","weapon_proficiencies","armor_proficiencies"):
        try:
            char[f] = json.loads(char[f])
        except (json.JSONDecodeError, TypeError):
            char[f] = []

    spells = [dict(r) for r in db.execute(
        "SELECT * FROM character_spells WHERE character_id = ? ORDER BY spell_level, spell_name",
        (char_id,)
    ).fetchall()]
    db.close()

    # Ability modifiers
    for stat in ["strength","dexterity","constitution","intelligence","wisdom","charisma"]:
        char[f"{stat}_mod"] = (char[stat] - 10) // 2

    return _render("sheet.html", request=request, character=char, spells=spells,
                   skill_abilities=SKILL_ABILITIES, classes=CLASSES)

# ── Routes: Live Session API ───────────────────────────────────────────────

@app.post("/api/character/{char_id}/update", response_class=JSONResponse)
async def update_character(char_id: int, request: Request):
    user = require_user(request)
    data = await request.json()

    db = get_db()
    row = db.execute("SELECT * FROM characters WHERE id = ? AND user_id = ?",
                     (char_id, user["id"])).fetchone()
    if not row:
        db.close()
        return JSONResponse({"error": "Not found"}, status_code=404)

    allowed = {"hp_current","hp_max","temp_hp","ac","notes","death_saves_success","death_saves_fail",
               "hit_dice_used","strength","dexterity","constitution","intelligence","wisdom","charisma",
               "level","proficiency_bonus"}
    updates = {}
    for k, v in data.items():
        if k in allowed:
            updates[k] = v

    if updates:
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [char_id, user["id"]]
        db.execute(f"UPDATE characters SET {sets} WHERE id=? AND user_id=?", vals)

    # Spell slot updates
    if "spells" in data:
        for sp in data["spells"]:
            db.execute("UPDATE character_spells SET slots_used=? WHERE id=? AND character_id=?",
                       (sp.get("slots_used", 0), sp.get("id"), char_id))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})

@app.post("/api/character/{char_id}/add-spell", response_class=JSONResponse)
async def add_spell(char_id: int, request: Request):
    user = require_user(request)
    data = await request.json()
    db = get_db()
    db.execute("INSERT INTO character_spells (character_id, spell_name, spell_level, prepared, slots_max, slots_used) VALUES (?,?,?,?,?,?)",
               (char_id, data.get("name",""), data.get("level",0), data.get("prepared",1), data.get("slots_max",0), 0))
    db.commit()
    sp_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    return JSONResponse({"id": sp_id})

@app.post("/api/character/{char_id}/delete", response_class=JSONResponse)
async def delete_character(char_id: int, request: Request):
    user = require_user(request)
    db = get_db()
    db.execute("DELETE FROM characters WHERE id = ? AND user_id = ?", (char_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})

# ── Startup ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    init_db()

# ── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8300)
