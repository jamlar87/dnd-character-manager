"""D&D Character Manager — FastAPI webapp with multi-user character tracking."""
from __future__ import annotations

import json
import os
import asyncio
import random
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import bcrypt
import httpx
from fastapi import FastAPI, Request, Form, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader

# ── Config ──────────────────────────────────────────────────────────────────

HERE = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DND_DATA_DIR", str(HERE / "data")))
DB_PATH = DATA_DIR / "characters.db"
TEMPLATES = HERE / "templates"
STATIC = HERE / "static"
SECRET_KEY = os.environ.get("SECRET_KEY", "dnd-dev-secret-change-me")
SRD_CACHE = DATA_DIR / "srd_cache"

# ── SRD Class Data (from dnd5eapi.co 2014, cached locally) ──────────────────

def _load_srd_class_data() -> tuple[dict, dict]:
    """Load cached SRD class levels and metadata."""
    try:
        with open(SRD_CACHE / "class_levels.json") as f:
            levels = json.load(f)
        with open(SRD_CACHE / "class_meta.json") as f:
            meta = json.load(f)
        return levels, meta
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, {}

SRD_LEVELS, SRD_META = _load_srd_class_data()

# ── Campaign Expert imports (reuse existing engine data) ────────────────────

_CE_PATH = Path("/home/james/dnd-campaign-expert")
if str(_CE_PATH) not in sys.path:
    sys.path.insert(0, str(_CE_PATH))

try:
    from engine.spells import _load_spell_cache as _ce_load_spells
    SRD_SPELLS: list[dict] = _ce_load_spells()
except Exception:
    SRD_SPELLS = []

# ── SRD Magic Items & Features (from dnd5eapi.co 2014, cached locally) ──────

def _load_json_cache(filename: str) -> list[dict]:
    try:
        with open(SRD_CACHE / filename) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

SRD_MAGIC_ITEMS: list[dict] = _load_json_cache("magic-items.json")
SRD_FEATURES: list[dict] = _load_json_cache("features.json")

# Build feature lookup by class+name for enrichment
FEATURE_DESCRIPTIONS: dict[str, str] = {}
for f in SRD_FEATURES:
    key = f.get("name", "").lower()
    desc = " ".join(f.get("desc", []))[:200]
    if desc:
        FEATURE_DESCRIPTIONS[key] = desc

# Build magic item index by rarity
from collections import defaultdict
ITEMS_BY_RARITY: dict[str, list[dict]] = defaultdict(list)
ITEM_WEAPONS: list[dict] = []
ITEM_ARMOR: list[dict] = []
ITEM_WONDROUS: list[dict] = []
ITEM_RODS_STAVES_WANDS: list[dict] = []

for item in SRD_MAGIC_ITEMS:
    rarity = item.get("rarity", {}).get("name", "").lower()
    ITEMS_BY_RARITY[rarity].append(item)
    cat = item.get("equipment_category", {}).get("name", "").lower()
    if cat == "weapon":
        ITEM_WEAPONS.append(item)
    elif cat == "armor":
        ITEM_ARMOR.append(item)
    elif cat in ("rod", "staff", "wand"):
        ITEM_RODS_STAVES_WANDS.append(item)
    elif cat == "wondrous item":
        ITEM_WONDROUS.append(item)

# Rarity tier by level bracket (PHB DMG p.135 magic item distribution)
def _item_rarity_for_level(level: int) -> list[str]:
    if level <= 4:   return []           # No magic items
    if level <= 10:  return ["uncommon"]
    if level <= 16:  return ["rare", "uncommon"]
    return ["very rare", "rare", "uncommon"]

# bcrypt 4.x native — avoid passlib (broken on Python 3.13)
BCRYPT_MAX = 72  # bcrypt's byte limit; truncate to be safe

def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:BCRYPT_MAX], bcrypt.gensalt()).decode()

def _verify(password: str, hash_: str) -> bool:
    return bcrypt.checkpw(password.encode()[:BCRYPT_MAX], hash_.encode())

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
            personality TEXT DEFAULT '',
            backstory TEXT DEFAULT '',
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
    # Migration: add personality/backstory columns if missing
    try:
        db.execute("ALTER TABLE characters ADD COLUMN personality TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        db.execute("ALTER TABLE characters ADD COLUMN backstory TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # Migration: build data columns
    for col, coltype in [("feature_data","TEXT DEFAULT '[]'"),
                          ("attacks_data","TEXT DEFAULT '[]'"),
                          ("spell_slot_data","TEXT DEFAULT '{}'"),
                          ("passive_perception","INTEGER DEFAULT 10"),
                          ("inspiration","INTEGER DEFAULT 0"),
                          ("exhaustion","INTEGER DEFAULT 0"),
                          ("portrait_url","TEXT DEFAULT ''"),
                          ("portrait_prompt","TEXT DEFAULT ''"),
                          ("save_proficiencies","TEXT DEFAULT '[]'"),
                          ("damage_resistances","TEXT DEFAULT '[]'"),
                          ("damage_immunities","TEXT DEFAULT '[]'"),
                          ("damage_vulnerabilities","TEXT DEFAULT '[]'"),
                          ("condition_immunities","TEXT DEFAULT '[]'")]:
        try:
            db.execute(f"ALTER TABLE characters ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass
    db.commit()
    db.close()

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
# All data from Player's Handbook (2014), verified against DnD-Manuals PDFs.
# PHB page references inline.

# PHB Ch.2 p.17-43 — Races
RACES = {
    "Dwarf": {"subraces": ["Hill Dwarf", "Mountain Dwarf"], "asi": {"constitution": 2}, "speed": 25, "darkvision": 60, "languages": ["Common", "Dwarvish"], "traits": ["Dwarven Resilience", "Stonecunning"], "desc": "Stout and hardy, standing 4–5 feet tall. Dwarves are known for their skill in battle, resistance to poison, and expertise with stonework. They live in great mountain kingdoms and value tradition and craftsmanship.", "subrace_descs": {"Hill Dwarf": "+1 Wisdom. Dwarven Toughness grants +1 HP per level. The hardier, wiser dwarf subrace.", "Mountain Dwarf": "+2 Strength. Dwarven Armor Training grants light and medium armor proficiency. The stronger, more martial dwarf."}},
    "Elf": {"subraces": ["High Elf", "Wood Elf", "Dark Elf (Drow)"], "asi": {"dexterity": 2}, "speed": 30, "darkvision": 60, "languages": ["Common", "Elvish"], "traits": ["Keen Senses", "Fey Ancestry", "Trance"], "desc": "Graceful and long-lived, elves stand 5–6 feet tall with pointed ears. They are known for their keen senses, immunity to magical sleep, and trance-like meditation instead of sleep.", "subrace_descs": {"High Elf": "+1 Intelligence. Gain a wizard cantrip and an extra language. The most magical of the elves.", "Wood Elf": "+1 Wisdom. Fleet of Foot (+5ft speed) and Mask of the Wild (hide in light natural obscurement). The swift and stealthy elf.", "Dark Elf (Drow)": "+1 Charisma. Superior Darkvision (120ft), Sunlight Sensitivity, and Drow Magic (dancing lights, faerie fire, darkness)."}},
    "Halfling": {"subraces": ["Lightfoot Halfling", "Stout Halfling"], "asi": {"dexterity": 2}, "speed": 25, "darkvision": 0, "languages": ["Common", "Halfling"], "traits": ["Lucky", "Brave", "Halfling Nimbleness"], "desc": "Small and nimble, standing about 3 feet tall. Halflings are famously lucky, brave despite their size, and able to move through the spaces of larger creatures.", "subrace_descs": {"Lightfoot Halfling": "+1 Charisma. Naturally Stealthy lets you hide behind larger creatures. The charming, sneaky halfling.", "Stout Halfling": "+1 Constitution. Stout Resilience grants advantage on poison saves and poison resistance. The durable halfling."}},
    "Human": {"subraces": [], "asi": {"strength": 1, "dexterity": 1, "constitution": 1, "intelligence": 1, "wisdom": 1, "charisma": 1}, "speed": 30, "darkvision": 0, "languages": ["Common"], "traits": [], "desc": "The most adaptable and ambitious of the common races. Humans gain +1 to all six ability scores, learn quickly, and are found in every corner of the world."},
    "Dragonborn": {"subraces": [], "asi": {"strength": 2, "charisma": 1}, "speed": 30, "darkvision": 0, "languages": ["Common", "Draconic"], "traits": ["Draconic Ancestry", "Breath Weapon", "Damage Resistance"], "desc": "Tall, muscular humanoids with draconic features — scales, a breath weapon, and damage resistance tied to their draconic ancestry. Proud and honorable warriors."},
    "Gnome": {"subraces": ["Forest Gnome", "Rock Gnome"], "asi": {"intelligence": 2}, "speed": 25, "darkvision": 60, "languages": ["Common", "Gnomish"], "traits": ["Gnome Cunning"], "desc": "Small and clever, gnomes stand 3–4 feet tall. Known for their intellect, cunning against magic, and natural gift for invention or illusion.", "subrace_descs": {"Forest Gnome": "+1 Dexterity. Natural Illusionist grants a minor illusion cantrip. Speak with Small Beasts. The woodland trickster.", "Rock Gnome": "+1 Constitution. Artificer's Lore doubles proficiency on History checks for magical/tech items. Tinker lets you build tiny clockwork devices."}},
    "Half-Elf": {"subraces": [], "asi": {"charisma": 2}, "speed": 30, "darkvision": 60, "languages": ["Common", "Elvish"], "traits": ["Fey Ancestry", "Skill Versatility"], "desc": "Blending human ambition with elven grace. Half-elves gain +2 Charisma, two bonus skill proficiencies, Fey Ancestry, and Darkvision — highly versatile and natural diplomats."},
    "Half-Orc": {"subraces": [], "asi": {"strength": 2, "constitution": 1}, "speed": 30, "darkvision": 60, "languages": ["Common", "Orc"], "traits": ["Relentless Endurance", "Savage Attacks"], "desc": "Powerful and enduring, with gray-green skin and prominent tusks. Half-orcs are relentless in battle (dropping to 1 HP instead of 0) and deal extra damage on critical hits."},
    "Tiefling": {"subraces": [], "asi": {"charisma": 2, "intelligence": 1}, "speed": 30, "darkvision": 60, "languages": ["Common", "Infernal"], "traits": ["Hellish Resistance", "Infernal Legacy"], "desc": "Descended from infernal bloodlines, tieflings have horns, tails, and skin in shades of red or purple. They have innate resistance to fire damage and can cast thaumaturgy, hellish rebuke, and darkness."},
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
    "Barbarian": {"hd": 12, "skills": ["Animal Handling","Athletics","Intimidation","Nature","Perception","Survival"], "skill_count": 2, "saves": ["strength","constitution"], "subclasses": ["Path of the Berserker","Path of the Totem Warrior"], "desc": "A fierce warrior of primitive background who can enter a battle rage. Barbarians are durable melee combatants who shrug off damage and hit hard.", "subclass_descs": {"Path of the Berserker": "The rage becomes frenzy — a bonus action attack each turn while raging, at the cost of exhaustion afterward. Pure offensive power.", "Path of the Totem Warrior": "Spirit totems (Bear, Eagle, Wolf) grant damage resistances, mobility, and ally buffs. Highly customizable natural warrior."}},
    "Bard": {"hd": 8, "skills": "all", "skill_count": 3, "saves": ["dexterity","charisma"], "subclasses": ["College of Lore","College of Valor"], "desc": "A master of words, music, and magic. Bards inspire allies, manipulate minds, and have access to a versatile spell list — they can fill nearly any role in a party.", "subclass_descs": {"College of Lore": "Bonus skill proficiencies and Cutting Words to undermine enemy attacks. Steals spells from any class via Additional Magical Secrets.", "College of Valor": "Combat Inspiration lets allies add Bardic Inspiration to damage or AC. Gains Extra Attack and medium armor — the martial bard."}},
    "Cleric": {"hd": 8, "skills": ["History","Insight","Medicine","Persuasion","Religion"], "skill_count": 2, "saves": ["wisdom","charisma"], "subclasses": ["Knowledge Domain","Life Domain","Light Domain","Nature Domain","Tempest Domain","Trickery Domain","War Domain"], "desc": "A divine agent wielding the power of a god. Clerics are versatile spellcasters who can heal, protect, and smite — their domain choice dramatically shapes their role.", "subclass_descs": {"Knowledge Domain": "Mind-reading and skill expertise in two knowledge skills. The scholar-cleric who uncovers secrets.", "Life Domain": "Bonus healing on every spell, heavy armor proficiency. The quintessential healer.", "Light Domain": "Warding Flare imposes disadvantage on attackers. Gains scorching ray, fireball, and other blasting spells.", "Nature Domain": "A druid cantrip, charm animals/plants, and elemental damage resistance. Protector of the wilds.", "Tempest Domain": "Destructive Wrath maximizes lightning/thunder damage. Heavy armor, martial weapons, and storm rebuke.", "Trickery Domain": "Invoke Duplicity creates an illusory double. Domain spells full of illusion and deception magic.", "War Domain": "War Priest grants bonus action attacks. Channel Divinity: Guided Strike adds +10 to an attack roll."}},
    "Druid": {"hd": 8, "skills": ["Arcana","Animal Handling","Insight","Medicine","Nature","Perception","Religion","Survival"], "skill_count": 2, "saves": ["intelligence","wisdom"], "subclasses": ["Circle of the Land","Circle of the Moon"], "desc": "A nature priest who draws power from the natural world. Druids are full spellcasters who can turn into animals (Wild Shape) and wield primal magic tied to the elements.", "subclass_descs": {"Circle of the Land": "Bonus cantrip, Natural Recovery (regain spell slots on short rest), and terrain-based circle spells. The caster druid.", "Circle of the Moon": "Combat Wild Shape as a bonus action with higher-CR beasts. Can expend spell slots to self-heal while transformed. The shapeshifter."}},
    "Fighter": {"hd": 10, "skills": ["Acrobatics","Animal Handling","Athletics","History","Insight","Intimidation","Perception","Survival"], "skill_count": 2, "saves": ["strength","constitution"], "subclasses": ["Champion","Battle Master","Eldritch Knight"], "desc": "A master of martial combat, skilled with a variety of weapons and armor. Fighters are versatile warriors who can specialize in any fighting style and get more attacks than any other class.", "subclass_descs": {"Champion": "Improved Critical (crits on 19–20, later 18–20). Remarkable Athlete adds half-proficiency to physical checks. Simple and deadly.", "Battle Master": "Combat Superiority grants maneuvers (Trip, Riposte, Precision Attack). The tactical fighter who controls the battlefield.", "Eldritch Knight": "Wizard spellcasting (abjuration/evocation) and Weapon Bond (can't be disarmed). Summon weapons across planes."}},
    "Monk": {"hd": 8, "skills": ["Acrobatics","Athletics","History","Insight","Religion","Stealth"], "skill_count": 2, "saves": ["strength","dexterity"], "subclasses": ["Way of the Open Hand","Way of Shadow","Way of the Four Elements"], "desc": "A disciplined martial artist who channels inner energy (ki). Monks fight unarmed, move with supernatural speed, and can stun, deflect, and outmaneuver foes.", "subclass_descs": {"Way of the Open Hand": "Open Hand Technique knocks foes prone or pushes them back on Flurry of Blows. Gains self-healing and a sanctuary effect.", "Way of Shadow": "Shadow Arts casts darkness, darkvision, pass without trace, and silence using ki. Shadow Step teleports between shadows for advantage.", "Way of the Four Elements": "Elemental Disciplines channel ki into spell-like effects — water whip, fireball, flight. The elemental bender."}},
    "Paladin": {"hd": 10, "skills": ["Athletics","Insight","Intimidation","Medicine","Persuasion","Religion"], "skill_count": 2, "saves": ["wisdom","charisma"], "subclasses": ["Oath of Devotion","Oath of the Ancients","Oath of Vengeance"], "desc": "A holy warrior bound by a sacred oath. Paladins combine martial prowess with divine magic — they smite enemies, heal allies, and project protective auras.", "subclass_descs": {"Oath of Devotion": "Sacred Weapon adds CHA to attack rolls. Aura of Devotion prevents charm. The classic holy knight.", "Oath of the Ancients": "Nature's Wrath restrains foes with vines. Aura of Warding grants resistance to all spell damage. The green knight.", "Oath of Vengeance": "Vow of Enmity gives advantage against one foe. Relentless Avenger lets you move after opportunity attacks. The relentless pursuer."}},
    "Ranger": {"hd": 10, "skills": ["Animal Handling","Athletics","Insight","Investigation","Nature","Perception","Stealth","Survival"], "skill_count": 3, "saves": ["strength","dexterity"], "subclasses": ["Hunter","Beast Master"], "desc": "A wilderness scout and skilled tracker. Rangers blend martial ability with nature magic — they excel at exploration, favored enemy tactics, and ranged combat.", "subclass_descs": {"Hunter": "Choose from Colossus Slayer (extra damage to wounded foes), Horde Breaker (extra attack), or Giant Killer. Versatile combat specialist.", "Beast Master": "An animal companion fights alongside you, acting on your turn. Share spells and coordinate attacks with your bonded beast."}},
    "Rogue": {"hd": 8, "skills": ["Acrobatics","Athletics","Deception","Insight","Intimidation","Investigation","Perception","Performance","Persuasion","Sleight of Hand","Stealth"], "skill_count": 4, "saves": ["dexterity","intelligence"], "subclasses": ["Thief","Assassin","Arcane Trickster"], "desc": "A stealthy trickster who exploits enemy weaknesses. Rogues deal massive Sneak Attack damage, have more skill proficiencies than any class, and excel at avoiding danger.", "subclass_descs": {"Thief": "Fast Hands lets you use items, pick pockets, and disarm traps as a bonus action. Second-Story Work adds climbing speed and jump distance.", "Assassin": "Assassinate auto-crits surprised creatures. Infiltration Expertise lets you create false identities. The lethal first-strike rogue.", "Arcane Trickster": "Wizard spellcasting (illusion/enchantment) plus invisible Mage Hand Legerdemain. Can steal spells and impose disadvantage on saves from stealth."}},
    "Sorcerer": {"hd": 6, "skills": ["Arcana","Deception","Insight","Intimidation","Persuasion","Religion"], "skill_count": 2, "saves": ["constitution","charisma"], "subclasses": ["Draconic Bloodline","Wild Magic"], "desc": "A spellcaster born with innate magic in their blood. Sorcerers use Metamagic to bend spells in ways no other class can — twin spells, quicken them, or make them subtle.", "subclass_descs": {"Draconic Bloodline": "Draconic Resilience boosts HP and grants natural AC 13. Elemental Affinity adds CHA to damage of your chosen element. The durable blaster.", "Wild Magic": "Tides of Chaos grants advantage, but may trigger Wild Magic Surges — random effects from a d100 table. Unpredictable and explosive."}},
    "Warlock": {"hd": 8, "skills": ["Arcana","Deception","History","Intimidation","Investigation","Nature","Religion"], "skill_count": 2, "saves": ["wisdom","charisma"], "subclasses": ["The Archfey","The Fiend","The Great Old One"], "desc": "A seeker of forbidden knowledge who made a pact with an otherworldly patron. Warlocks use Pact Magic — a few spell slots that recharge on short rests — plus Eldritch Invocations for unique abilities.", "subclass_descs": {"The Archfey": "Fey Presence charms or frightens nearby foes. Misty Escape lets you teleport and turn invisible when hit. The trickster patron.", "The Fiend": "Dark One's Blessing grants temp HP when you kill. Hurl Through Hell sends a target on a short, devastating trip to the lower planes.", "The Great Old One": "Awakened Mind grants telepathy. Entropic Ward imposes disadvantage on attackers. Create Thrall makes a permanent charmed servant."}},
    "Wizard": {"hd": 6, "skills": ["Arcana","History","Insight","Investigation","Medicine","Religion"], "skill_count": 2, "saves": ["intelligence","wisdom"], "subclasses": ["Abjuration","Conjuration","Divination","Enchantment","Evocation","Illusion","Necromancy","Transmutation"], "desc": "A scholarly spellcaster who learns magic through study. Wizards have the largest spell list and can learn new spells from scrolls — they prepare from a spellbook and can ritual cast without preparation.", "subclass_descs": {"Abjuration": "Arcane Ward absorbs damage as a magical HP buffer. Spell Resistance grants advantage on saves against spells. The defensive wizard.", "Conjuration": "Minor Conjuration creates nonmagical objects. Benign Transposition teleports you and swaps places with an ally. The summoner.", "Divination": "Portent lets you replace any d20 roll with one of two pre-rolled results. The fate manipulator.", "Enchantment": "Hypnotic Gaze incapacitates a creature. Instinctive Charm redirects attacks to other targets. The mind controller.", "Evocation": "Sculpt Spells protects allies from your area effects. Potent Cantrip deals half damage even on saves. The blaster.", "Illusion": "Improved Minor Illusion creates sound and image simultaneously. Malleable Illusions lets you reshape ongoing illusions.", "Necromancy": "Grim Harvest heals you when you kill with spells. Undead Thralls creates stronger undead and lets you raise more of them.", "Transmutation": "Minor Alchemy temporarily changes materials. Transmuter's Stone grants a buff (darkvision, speed, resistance, or CON saves)."}},
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

BACKGROUNDS = ["Acolyte","Charlatan","Criminal","Entertainer","Folk Hero","Guild Artisan","Hermit","Noble","Outlander","Sage","Sailor","Soldier","Urchin","Custom"]
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

    # Generate build data (features, attacks, spell slots)
    build_features = get_class_features(class_name, level, subclass)
    enriched = enrich_features(build_features)
    build_attacks = _calculate_attacks(class_name, level,
        {a: (stats[a] - 10) // 2 for a in stats}, prof_bonus,
        data.get("equipment", []))

    # Spell slots from SRD data
    spell_slots = get_spell_slots(class_name, level)

    # Passive perception: 10 + WIS mod + proficiency if Perception proficient
    skills_list = data.get("skills", [])
    wis_mod = (stats["wisdom"] - 10) // 2
    passive = 10 + wis_mod + (prof_bonus if "Perception" in skills_list else 0)

    db = get_db()
    cur = db.execute("""
        INSERT INTO characters (user_id, name, race, subrace, class_name, subclass,
        level, background, alignment, personality, backstory, strength, dexterity, constitution, intelligence,
        wisdom, charisma, hp_max, hp_current, ac, speed,
        proficiency_bonus, hit_dice, skills, features, languages, tool_proficiencies,
        weapon_proficiencies, armor_proficiencies, inventory, equipped,
        feature_data, attacks_data, spell_slot_data, passive_perception, portrait_url, portrait_prompt)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        user["id"], name, race_name, subrace, class_name, subclass, level,
        data.get("background",""), data.get("alignment",""), data.get("personality",""), data.get("backstory",""),
        stats["strength"], stats["dexterity"], stats["constitution"],
        stats["intelligence"], stats["wisdom"], stats["charisma"],
        hp_max, hp_max, ac_base, race_data.get("speed", 30),
        prof_bonus, f"1d{hd}",
        json.dumps(skills_list), json.dumps(build_features), json.dumps(race_data.get("languages",["Common"])),
        json.dumps([]), json.dumps([]), json.dumps([]),
        json.dumps(data.get("equipment", [])), json.dumps([]),
        json.dumps(enriched), json.dumps(build_attacks), json.dumps(spell_slots), passive,
        data.get("portrait_url", ""), data.get("portrait_prompt", "")
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
    for f in ("skills","features","inventory","equipped","languages","tool_proficiencies","weapon_proficiencies","armor_proficiencies",
              "save_proficiencies","damage_resistances","damage_immunities","damage_vulnerabilities","condition_immunities"):
        try:
            char[f] = json.loads(char[f])
        except (json.JSONDecodeError, TypeError):
            char[f] = []
    # Load enriched build data
    for f in ("feature_data", "attacks_data", "spell_slot_data"):
        try:
            char[f] = json.loads(char[f] or "[]")
        except (json.JSONDecodeError, TypeError):
            char[f] = [] if f != "spell_slot_data" else {}

    spells = [dict(r) for r in db.execute(
        "SELECT * FROM character_spells WHERE character_id = ? ORDER BY spell_level, spell_name",
        (char_id,)
    ).fetchall()]
    db.close()

    # Ability modifiers
    for stat in ["strength","dexterity","constitution","intelligence","wisdom","charisma"]:
        char[f"{stat}_mod"] = (char[stat] - 10) // 2

    # Merged save proficiencies (class-derived + user-toggled)
    class_saves = CLASSES.get(char.get("class_name",""), {}).get("saves", [])
    user_saves = char.get("save_proficiencies", [])
    saves_class = list(set(class_saves) | set(user_saves))

    return _render("sheet.html", request=request, character=char, spells=spells,
                   skill_abilities=SKILL_ABILITIES, classes=CLASSES, races=RACES, saves_class=saves_class)

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

    allowed = {
        # Core stats
        "hp_current","hp_max","temp_hp","ac","notes","death_saves_success","death_saves_fail",
        "hit_dice_used","strength","dexterity","constitution","intelligence","wisdom","charisma",
        "level","proficiency_bonus","speed","hit_dice","inspiration","exhaustion","passive_perception",
        # Identity
        "name","race","subrace","class_name","subclass","background","alignment",
        "personality","backstory",
        # JSON arrays (serialized as JSON strings)
        "skills","save_proficiencies","tool_proficiencies","weapon_proficiencies","armor_proficiencies",
        "languages","features","inventory","equipped","feature_data","attacks_data",
        "damage_resistances","damage_immunities","damage_vulnerabilities","condition_immunities",
    }
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

# ── Name Generators ─────────────────────────────────────────────────────────

RACE_NAMES = {
    "Dwarf": {"male": ["Thorin","Durin","Balin","Dwalin","Oin","Gloin","Bofur","Bombur","Nori","Dori"], "female": ["Dis","Hilda","Brunhild","Gerta","Helga","Inga","Sigrid","Thyra","Yrsa","Kara"], "clan": ["Ironforge","Battlehammer","Bronzebeard","Darkiron","Stoutmantle","Deepdelve","Anvilmar","Stonebrow","Hammersmith","Forgefire"]},
    "Elf": {"male": ["Elrond","Thranduil","Legolas","Finrod","Celeborn","Haldir","Fingolfin","Glorfindel","Cirdan","Eol"], "female": ["Galadriel","Arwen","Luthien","Idril","Nimrodel","Earwen","Aredhel","Elwing","Miriel","Finduilas"], "clan": ["Silverleaf","Moonshadow","Starbreeze","Dawnwhisper","Nightbreeze","Goldenoak","Swiftarrow","Brightsong","Dewdrop","Windwalker"]},
    "Halfling": {"male": ["Bilbo","Frodo","Samwise","Meriadoc","Peregrin","Fredegar","Tobold","Hamfast","Drogo","Odo"], "female": ["Rosie","Belladonna","Primula","Lobelia","Daisy","Marigold","Pearl","Esmeralda","Pansy","Ruby"], "clan": ["Baggins","Took","Brandybuck","Gamgee","Bolger","Proudfoot","Greenhand","Brockhouse","Goodbody","Chubb"]},
    "Human": {"male": ["Aldric","Cedric","Edmund","Garret","Harold","Lothar","Merek","Oswald","Roland","Theron"], "female": ["Alys","Brynn","Catelyn","Elara","Gwendolyn","Isolde","Liana","Morwen","Rowena","Seraphine"], "clan": ["Hawke","Blackwood","Stormwind","Ashford","Ravencroft","Thornfield","Westbrook","Northrend","Hightower","Greymane"]},
    "Dragonborn": {"male": ["Kriv","Medrash","Nadarr","Pandjed","Patrin","Rhogar","Sora","Torrin","Ushar","Vrakas"], "female": ["Akra","Biri","Daar","Farideh","Harann","Jheri","Kava","Korinn","Nala","Sora"], "clan": ["Clethtinthiallor","Daardendrian","Delmirev","Drachedandion","Fenkenkabradon","Kepeshkmolik","Kerrhylon","Nemmonis","Verthisathurgiesh","Yarjerit"]},
    "Gnome": {"male": ["Fizzwick","Gimble","Nackle","Orryn","Pock","Quill","Sprocket","Tinker","Wizzle","Zook"], "female": ["Bimpnottin","Caramip","Duvamil","Ellywick","Lilli","Loopmottin","Mardnab","Roywyn","Shamil","Zanna"], "clan": ["Beren","Daergel","Folkor","Garrick","Nackle","Raulnor","Scheppen","Turen","Warrick","Wiggens"]},
    "Half-Elf": {"male": ["Aelar","Caelum","Doran","Eryndor","Fenris","Kael","Lorien","Myles","Theron","Varek"], "female": ["Aeris","Caelia","Elowen","Illyria","Kyra","Lyra","Maeris","Nyx","Seren","Vaela"], "clan": ["Amakiir","Ilphelkiir","Moonflower","Wintermere","Summerwind","Autumnvale","Springbrook","Truehart","Goodfellow","Whispermoon"]},
    "Half-Orc": {"male": ["Durgash","Goruk","Hrogath","Krusk","Lurtz","Morg","Ogruk","Thokk","Ulfgrim","Zugor"], "female": ["Borga","Druga","Grenka","Hagra","Kella","Murook","Rogga","Sutha","Urzul","Vorga"], "clan": ["Bonecrusher","Doomhammer","Ironhide","Skullsplitter","Warsong","Bloodfist","Gorehowl","Dreadmaw","Stormrage","Blackrock"]},
    "Tiefling": {"male": ["Akmenos","Damakos","Ekemon","Iados","Kairon","Leucis","Melech","Morthos","Phelan","Skamos"], "female": ["Akta","Anakis","Bryseis","Criella","Damaia","Ea","Kallista","Lerissa","Makaria","Nemeia"], "clan": ["Art","Carrion","Chant","Creed","Despair","Fear","Glory","Hope","Ideal","Music","Reverie","Sorrow","Torment","Weary"]},
}

STARTING_EQUIPMENT = {
    "Barbarian": ["Greataxe", "2 Handaxes", "Explorer's Pack", "4 Javelins"],
    "Bard": ["Rapier", "Entertainer's Pack", "Lute", "Leather Armor", "Dagger"],
    "Cleric": ["Mace", "Scale Mail", "Light Crossbow + 20 Bolts", "Priest's Pack", "Shield", "Holy Symbol"],
    "Druid": ["Wooden Shield", "Scimitar", "Leather Armor", "Explorer's Pack", "Druidic Focus"],
    "Fighter": ["Chain Mail", "Longsword", "Shield", "Light Crossbow + 20 Bolts", "Dungeoneer's Pack"],
    "Monk": ["Shortsword", "Dungeoneer's Pack", "10 Darts"],
    "Paladin": ["Longsword", "Shield", "5 Javelins", "Priest's Pack", "Chain Mail", "Holy Symbol"],
    "Ranger": ["Longbow + 20 Arrows", "Shortsword", "Scale Mail", "Explorer's Pack"],
    "Rogue": ["Rapier", "Shortbow + 20 Arrows", "Burglar's Pack", "Leather Armor", "2 Daggers", "Thieves' Tools"],
    "Sorcerer": ["Light Crossbow + 20 Bolts", "Arcane Focus", "Dungeoneer's Pack", "2 Daggers"],
    "Warlock": ["Light Crossbow + 20 Bolts", "Arcane Focus", "Scholar's Pack", "Leather Armor", "Dagger"],
    "Wizard": ["Quarterstaff", "Arcane Focus", "Scholar's Pack", "Spellbook"],
}

def random_name(race: str, gender: str = "any") -> dict:
    """Generate a random name for the given race."""
    data = RACE_NAMES.get(race, RACE_NAMES["Human"])
    if gender == "any":
        gender = random.choice(["male", "female"])
    first = random.choice(data[gender])
    clan = random.choice(data["clan"])
    return {"name": f"{first} {clan}", "first": first, "clan": clan}

def random_equipment(class_name: str) -> list[str]:
    return STARTING_EQUIPMENT.get(class_name, ["Explorer's Pack", "Dagger"])

# ── PHB-Grounded AI Generation ──────────────────────────────────────────────
# All mechanical data (races, classes, spells, equipment) is from the PHB 2014
# hardcoded above. AI only handles creative flavor: name, personality, backstory.
# Backgrounds and alignments are validated against PHB-approved lists.
# Model chain: Gemini → OpenRouter (free) → Ollama (local) → deterministic

PHB_BACKGROUNDS = BACKGROUNDS  # PHB p.125-141
PHB_ALIGNMENTS = ALIGNMENTS    # PHB p.122

# ── Character Build Optimization (PHB 2014) ─────────────────────────────────

# Ability Score Priority (PHB Quick Build sections + optimal play)
ABILITY_PRIORITY = {
    "Barbarian":  ["strength","constitution","dexterity","wisdom","charisma","intelligence"],
    "Bard":       ["charisma","dexterity","constitution","intelligence","wisdom","strength"],
    "Cleric":     ["wisdom","strength","constitution","dexterity","charisma","intelligence"],
    "Druid":      ["wisdom","constitution","dexterity","intelligence","charisma","strength"],
    "Fighter":    ["strength","constitution","dexterity","wisdom","intelligence","charisma"],
    "Monk":       ["dexterity","wisdom","constitution","strength","intelligence","charisma"],
    "Paladin":    ["strength","charisma","constitution","wisdom","dexterity","intelligence"],
    "Ranger":     ["dexterity","wisdom","constitution","strength","intelligence","charisma"],
    "Rogue":      ["dexterity","constitution","intelligence","wisdom","charisma","strength"],
    "Sorcerer":   ["charisma","constitution","dexterity","wisdom","intelligence","strength"],
    "Warlock":    ["charisma","constitution","dexterity","wisdom","intelligence","strength"],
    "Wizard":     ["intelligence","constitution","dexterity","wisdom","charisma","strength"],
}

# Standard array: 15,14,13,12,10,8 (PHB p.13)
STANDARD_ARRAY = [15, 14, 13, 12, 10, 8]

# Proficiency bonus by level (PHB p.15)
PROFICIENCY_BONUS = {1:2,2:2,3:2,4:2,5:3,6:3,7:3,8:3,9:4,10:4,11:4,12:4,13:5,14:5,15:5,16:5,17:6,18:6,19:6,20:6}

# ── SRD-Backed Functions (replace hand-coded PHB tables) ─────────────────────

def get_spell_slots(class_name: str, level: int) -> dict:
    """Return spell slots from SRD API cache. Falls back to empty if class not found."""
    key = class_name.lower()
    levels = SRD_LEVELS.get(key, [])
    entry = None
    for l in levels:
        if l.get("level") == level:
            entry = l
            break
    if not entry:
        return {"slots": 0, "slot_level": None, "by_level": {}}

    sc = entry.get("spellcasting", {})
    # Warlock uses Pact Magic — different slot structure
    if key == "warlock":
        slot_count = 0
        slot_level = 0
        # Find max non-zero slot
        for i in range(1, 10):
            slots = sc.get(f"spell_slots_level_{i}", 0)
            if slots > 0:
                slot_count = slots
                slot_level = i
        note = f"{slot_count} Pact Magic slots, all {_ordinal(slot_level)} level" if slot_count else "No Pact Magic yet"
        return {"slots": slot_count, "slot_level": slot_level, "note": note, "by_level": {}}

    # Standard spell slots
    by_level = {}
    for i in range(1, 10):
        by_level[i] = sc.get(f"spell_slots_level_{i}", 0)
    return {"slots": sum(by_level.values()), "slot_level": None, "by_level": by_level}


def get_class_features(class_name: str, level: int, subclass: str = "") -> list[str]:
    """Return class features gained by this level from SRD API cache."""
    key = class_name.lower()
    levels = SRD_LEVELS.get(key, [])
    gained = []
    for l in levels:
        lvl = l.get("level", 0)
        if lvl <= level:
            for feat in l.get("features", []):
                name = feat.get("name", "")
                if name:
                    gained.append(f"L{lvl}: {name}")
    if subclass and level >= 3:
        gained.insert(0, f"L3: {subclass}")
    return gained


def get_srd_spells_for_class(class_name: str, max_level: int) -> dict:
    """Get SRD spells (cantrips + leveled) for a class, filtered by max spell level available."""
    if not SRD_SPELLS:
        return {"cantrips": [], "spells": {}}
    cls_lower = class_name.lower()
    # Determine max spell level this class can cast at the given level
    spell_slots = get_spell_slots(class_name, max_level)
    if class_name == "Warlock":
        max_spell_level = spell_slots.get("slot_level", 0)
    else:
        by_level = spell_slots.get("by_level", {})
        max_spell_level = max((lvl for lvl, slots in by_level.items() if slots > 0), default=0)

    cantrips = []
    leveled = {i: [] for i in range(1, 10)}
    for spell in SRD_SPELLS:
        spell_classes = [c.get("name", "").lower() for c in spell.get("classes", [])]
        if cls_lower not in spell_classes:
            continue
        spell_level = spell.get("level", 0)
        spell_name = spell.get("name", "")
        if spell_level == 0:
            cantrips.append(spell_name)
        elif spell_level <= max_spell_level:
            leveled[spell_level].append(spell_name)

    # Limit to top spells (first 6 per level to keep output manageable)
    for lvl in leveled:
        leveled[lvl] = leveled[lvl][:6]

    return {"cantrips": cantrips[:8], "spells": leveled}


# ── Spells also available as tiered recommendations (from SRD cache) ──────────

def get_spells_for_level(class_name: str, level: int) -> dict:
    """Get recommended spells for this class from SRD data."""
    return get_srd_spells_for_class(class_name, level)

# Feats by class tier (PHB-optimal picks at ASI levels 4,8,12,16,19)
RECOMMENDED_FEATS = {
    "Barbarian":    ["Great Weapon Master","Polearm Master","Sentinel","Resilient (Wisdom)","Tough"],
    "Bard":         ["Inspiring Leader","War Caster","Resilient (Constitution)","Lucky","Alert","Fey Touched"],
    "Cleric":       ["War Caster","Resilient (Constitution)","Telekinetic","Lucky","Alert","Fey Touched"],
    "Druid":        ["War Caster","Resilient (Constitution)","Observant","Lucky","Alert","Fey Touched"],
    "Fighter":      ["Great Weapon Master","Polearm Master","Sentinel","Sharpshooter","Crossbow Expert","Tough"],
    "Monk":         ["Mobile","Crusher","Sentinel","Alert","Tough"],
    "Paladin":      ["Great Weapon Master","Polearm Master","Sentinel","Inspiring Leader","Resilient (Constitution)","Fey Touched"],
    "Ranger":       ["Sharpshooter","Crossbow Expert","Fey Touched","Resilient (Constitution)","Alert","Lucky"],
    "Rogue":        ["Sharpshooter","Crossbow Expert","Skulker","Lucky","Alert","Mobile"],
    "Sorcerer":     ["War Caster","Metamagic Adept","Fey Touched","Lucky","Alert","Elemental Adept"],
    "Warlock":      ["War Caster","Resilient (Constitution)","Fey Touched","Spell Sniper","Lucky","Alert"],
    "Wizard":       ["War Caster","Resilient (Constitution)","Telekinetic","Lucky","Alert","Fey Touched"],
}

# Scaled equipment by class and level tier (PHB starting equipment + reasonable progression)
# Levels: 1, 5, 10, 15, 20
SCALED_EQUIPMENT = {
    "Barbarian": {
        1:  ["Greataxe","2 Handaxes","Explorer's Pack","4 Javelins"],
        5:  ["+1 Greataxe","2 Handaxes","Explorer's Pack","4 Javelins","Breastplate"],
        10: ["+2 Greataxe","Explorer's Pack","Half Plate","Javelin of Lightning","Cloak of Protection","Potion of Giant Strength (Hill)"],
        15: ["+2 Greatsword","Half Plate +1","Cloak of Displacement","Belt of Giant Strength (Fire)","Ring of Protection","Boots of Speed"],
        20: ["+3 Greataxe","Half Plate +2","Belt of Storm Giant Strength","Cloak of Displacement","Ring of Protection","Boots of Speed","Mantle of Spell Resistance"],
    },
    "Bard": {
        1:  ["Rapier","Entertainer's Pack","Lute","Leather Armor","Dagger"],
        5:  ["+1 Rapier","Studded Leather","Entertainer's Pack","Lute","Wand of Magic Missiles"],
        10: ["+2 Rapier","Studded Leather +1","Instrument of the Bards (Cli Lyre)","Cloak of Protection","Wand of Web","Hat of Disguise"],
        15: ["+2 Rapier","Studded Leather +2","Instrument of the Bards (Bandore)","Ring of Protection","Cloak of Displacement","Mantle of Spell Resistance"],
        20: ["+3 Rapier","Studded Leather +3","Instrument of the Bards (Ollamh Harp)","Ring of Protection","Cloak of Displacement","Robe of the Archmagi"],
    },
    "Cleric": {
        1:  ["Mace","Scale Mail","Light Crossbow + 20 Bolts","Priest's Pack","Shield","Holy Symbol"],
        5:  ["+1 Mace","Splint Mail","Shield","Holy Symbol","Priest's Pack","Necklace of Prayer Beads"],
        10: ["+2 Mace","Plate Armor","Shield +1","Necklace of Prayer Beads","Cloak of Protection","Periapt of Wound Closure"],
        15: ["+2 Mace","Plate Armor +1","Shield +2","Amulet of the Devout +2","Cloak of Displacement","Ring of Spell Storing"],
        20: ["+3 Mace","Plate Armor +2","Shield +3","Amulet of the Devout +3","Cloak of Displacement","Ring of Spell Storing","Rod of Resurrection"],
    },
    "Druid": {
        1:  ["Wooden Shield","Scimitar","Leather Armor","Explorer's Pack","Druidic Focus"],
        5:  ["+1 Scimitar","Hide Armor","Wooden Shield","Explorer's Pack","Moon Sickle +1","Cloak of Elvenkind"],
        10: ["+2 Scimitar","Studded Leather","Wooden Shield +1","Moon Sickle +2","Staff of the Woodlands","Cloak of Protection"],
        15: ["+2 Scimitar","Studded Leather +1","Wooden Shield +2","Moon Sickle +2","Staff of the Woodlands","Ring of Protection","Dragonhide Breastplate"],
        20: ["+3 Scimitar","Dragonhide Half Plate","Wooden Shield +3","Moon Sickle +3","Staff of the Woodlands","Ring of Protection","Cloak of Displacement"],
    },
    "Fighter": {
        1:  ["Chain Mail","Longsword","Shield","Light Crossbow + 20 Bolts","Dungeoneer's Pack"],
        5:  ["Plate Armor","+1 Longsword","Shield","Longbow + 20 Arrows","Dungeoneer's Pack","Cloak of Protection"],
        10: ["Plate Armor +1","+2 Longsword","Shield +1","Longbow +1","Cloak of Displacement","Ring of Protection","Gauntlets of Ogre Power"],
        15: ["Plate Armor +2","+2 Greatsword","Longbow +2","Belt of Giant Strength (Hill)","Cloak of Displacement","Ring of Protection","Winged Boots"],
        20: ["Plate Armor +3","+3 Greatsword","Longbow +3","Belt of Giant Strength (Storm)","Cloak of Displacement","Ring of Protection","Winged Boots","Mantle of Spell Resistance"],
    },
    "Monk": {
        1:  ["Shortsword","Dungeoneer's Pack","10 Darts"],
        5:  ["+1 Shortsword","Dungeoneer's Pack","10 Darts","Bracers of Defense","Cloak of Protection"],
        10: ["+2 Shortsword","Bracers of Defense","Cloak of Displacement","Ring of Protection","Insignia of Claws","Boots of Speed"],
        15: ["+2 Shortsword","Bracers of Defense +1","Cloak of Displacement","Ring of Protection","Insignia of Claws","Boots of Speed","Dragonhide Belt +2"],
        20: ["+3 Shortsword","Bracers of Defense +2","Cloak of Displacement","Ring of Protection","Boots of Speed","Dragonhide Belt +3","Tome of Understanding"],
    },
    "Paladin": {
        1:  ["Longsword","Shield","5 Javelins","Priest's Pack","Chain Mail","Holy Symbol"],
        5:  ["+1 Longsword","Shield","Plate Armor","5 Javelins","Priest's Pack","Holy Symbol"],
        10: ["+2 Longsword","Shield +1","Plate Armor +1","Cloak of Protection","Holy Avenger (base)","Necklace of Prayer Beads"],
        15: ["+2 Greatsword","Plate Armor +2","Cloak of Displacement","Holy Avenger","Ring of Protection","Belt of Giant Strength (Hill)","Necklace of Prayer Beads"],
        20: ["+3 Greatsword","Plate Armor +3","Holy Avenger","Belt of Giant Strength (Storm)","Cloak of Displacement","Ring of Protection","Mantle of Spell Resistance"],
    },
    "Ranger": {
        1:  ["Longbow + 20 Arrows","Shortsword","Scale Mail","Explorer's Pack"],
        5:  ["+1 Longbow","Breastplate","Shortsword","Explorer's Pack","Cloak of Elvenkind","Bracers of Archery"],
        10: ["+2 Longbow","Studded Leather +1","Shortsword +1","Cloak of Elvenkind","Bracers of Archery","Ring of Protection","Boots of Elvenkind"],
        15: ["+2 Longbow","Studded Leather +2","Cloak of Displacement","Bracers of Archery","Ring of Protection","Oathbow","Boots of Speed"],
        20: ["+3 Longbow","Studded Leather +3","Cloak of Displacement","Bracers of Archery","Ring of Protection","Oathbow","Boots of Speed","Mantle of Spell Resistance"],
    },
    "Rogue": {
        1:  ["Rapier","Shortbow + 20 Arrows","Burglar's Pack","Leather Armor","2 Daggers","Thieves' Tools"],
        5:  ["+1 Rapier","Studded Leather","Shortbow","Burglar's Pack","Thieves' Tools","Cloak of Elvenkind","Gloves of Thievery"],
        10: ["+2 Rapier","Studded Leather +1","Shortbow +1","Cloak of Elvenkind","Gloves of Thievery","Ring of Protection","Boots of Elvenkind"],
        15: ["+2 Rapier","Studded Leather +2","Cloak of Displacement","Ring of Protection","Boots of Speed","Dagger of Venom","Hat of Disguise"],
        20: ["+3 Rapier","Studded Leather +3","Cloak of Displacement","Ring of Protection","Boots of Speed","Dagger of Venom","Hat of Disguise","Mantle of Spell Resistance"],
    },
    "Sorcerer": {
        1:  ["Light Crossbow + 20 Bolts","Arcane Focus","Dungeoneer's Pack","2 Daggers"],
        5:  ["Arcane Focus","Dungeoneer's Pack","Cloak of Protection","Wand of Magic Missiles","Elven Chain"],
        10: ["Bloodwell Vial +2","Elven Chain","Cloak of Protection","Wand of Fireballs","Ring of Spell Storing","Broom of Flying"],
        15: ["Bloodwell Vial +2","Elven Chain +1","Cloak of Displacement","Ring of Spell Storing","Wand of Fireballs","Robe of Stars","Staff of Power"],
        20: ["Bloodwell Vial +3","Robe of the Archmagi","Cloak of Displacement","Ring of Spell Storing","Robe of Stars","Staff of Power","Tome of Leadership and Influence"],
    },
    "Warlock": {
        1:  ["Light Crossbow + 20 Bolts","Arcane Focus","Scholar's Pack","Leather Armor","Dagger"],
        5:  ["+1 Rod of the Pact Keeper","Studded Leather","Scholar's Pack","Cloak of Protection","Wand of Web"],
        10: ["+2 Rod of the Pact Keeper","Studded Leather +1","Cloak of Protection","Wand of Fireballs","Ring of Spell Storing","Broom of Flying"],
        15: ["+2 Rod of the Pact Keeper","Studded Leather +2","Cloak of Displacement","Ring of Spell Storing","Robe of Stars","Staff of Power","Illusionist's Bracers"],
        20: ["+3 Rod of the Pact Keeper","Studded Leather +3","Cloak of Displacement","Ring of Spell Storing","Robe of the Archmagi","Staff of Power","Illusionist's Bracers"],
    },
    "Wizard": {
        1:  ["Quarterstaff","Arcane Focus","Scholar's Pack","Spellbook"],
        5:  ["Arcane Focus","Scholar's Pack","Spellbook","Cloak of Protection","Wand of Magic Missiles","Elven Chain"],
        10: ["Arcane Grimoire +2","Elven Chain","Cloak of Protection","Wand of Fireballs","Ring of Spell Storing","Broom of Flying"],
        15: ["Arcane Grimoire +2","Elven Chain +1","Cloak of Displacement","Ring of Spell Storing","Wand of Fireballs","Robe of Stars","Staff of Power"],
        20: ["Arcane Grimoire +3","Robe of the Archmagi","Cloak of Displacement","Ring of Spell Storing","Robe of Stars","Staff of Power","Tome of Clear Thought"],
    },
}

# HP calculation: max at level 1, average (rounded up) thereafter
def calc_hp(class_name: str, level: int, con_mod: int) -> int:
    hd = CLASSES.get(class_name, {}).get("hd", 8)
    avg_roll = (hd // 2) + 1  # PHB: half+1, effectively ceiling(average)
    return hd + con_mod + (level - 1) * (avg_roll + con_mod)

def allocate_ability_scores(class_name: str, race_name: str, subrace: str = "") -> dict:
    """Allocate standard array optimally for class, then apply racial ASIs."""
    priority = ABILITY_PRIORITY.get(class_name, ABILITY_PRIORITY["Fighter"])
    scores = {ability: val for ability, val in zip(priority, sorted(STANDARD_ARRAY, reverse=True))}
    # Apply racial ASIs (PHB p.12-40)
    race_data = RACES.get(race_name, RACES["Human"])
    for ability, bonus in race_data["asi"].items():
        scores[ability] = scores.get(ability, 10) + bonus
    if subrace and subrace in SUBASIS:
        for ability, bonus in SUBASIS[subrace].items():
            scores[ability] = scores.get(ability, 10) + bonus
    # Fill any missing abilities
    for ability in ["strength","dexterity","constitution","intelligence","wisdom","charisma"]:
        scores.setdefault(ability, 10)
    return scores

def modifier(score: int) -> int:
    return (score - 10) // 2

def get_asi_levels(level: int, class_name: str = "") -> list[int]:
    """List of ASI levels the character has passed."""
    asis = {4,8,12,16,19}
    if class_name == "Fighter": asis.update({6,14})  # Fighter gets extra ASIs (PHB p.71)
    return sorted([a for a in asis if a <= level])

def _ordinal(n: int) -> str:
    """Return ordinal string: 1st, 2nd, 3rd, etc."""
    if 11 <= n % 100 <= 13: return f"{n}th"
    return f"{n}{['th','st','nd','rd','th','th','th','th','th','th'][n % 10]}"

def get_feats_for_level(class_name: str, level: int) -> list[str]:
    """Recommended feats based on how many ASIs the character has taken."""
    asi_count = len(get_asi_levels(level, class_name))
    all_feats = RECOMMENDED_FEATS.get(class_name, RECOMMENDED_FEATS["Fighter"])
    # Pick top N feats (or fewer if not enough levels)
    count = min(asi_count, len(all_feats))
    return all_feats[:count]

def get_equipment_for_level(class_name: str, level: int) -> list[str]:
    """Get scaled equipment for this class at the closest tier."""
    if class_name not in SCALED_EQUIPMENT:
        return ["Explorer's Pack", "Dagger"]
    tiers = sorted(SCALED_EQUIPMENT[class_name].keys())
    tier = 1
    for t in tiers:
        if t <= level:
            tier = t
    return SCALED_EQUIPMENT[class_name][tier]


def pick_magic_items(class_name: str, level: int) -> list[dict]:
    """Pick level-appropriate SRD magic items for this class."""
    if not SRD_MAGIC_ITEMS:
        return []
    rarities = _item_rarity_for_level(level)
    if not rarities:
        return []
    
    items = []
    # Determine what item types this class wants
    is_martial = class_name in ("Barbarian", "Fighter", "Paladin", "Ranger")
    is_caster = class_name in ("Wizard", "Sorcerer", "Warlock", "Bard", "Cleric", "Druid")
    
    for rarity in rarities:
        pool = ITEMS_BY_RARITY.get(rarity, [])
        if not pool:
            continue
        
        # Pick 1 weapon for martials, 1 focus for casters, 1 armor, 1 wondrous
        if is_martial:
            weapons = [i for i in pool if i in ITEM_WEAPONS]
            if weapons:
                items.append(random.choice(weapons))
        if is_caster:
            foci = [i for i in pool if i in ITEM_RODS_STAVES_WANDS]
            if foci:
                items.append(random.choice(foci))
        
        armor = [i for i in pool if i in ITEM_ARMOR]
        if armor and random.random() < 0.4:
            items.append(random.choice(armor))
        
        wondrous = [i for i in pool if i in ITEM_WONDROUS]
        if wondrous:
            items.append(random.choice(wondrous))
        
        if len(items) >= 3:
            break
    
    # Format as name + rarity + short description
    result = []
    for item in items[:5]:
        name = item["name"]
        rarity = item.get("rarity", {}).get("name", "")
        desc = " ".join(item.get("desc", []))[:120]
        result.append({"name": name, "rarity": rarity, "description": desc})
    return result


def enrich_features(feature_list: list[str]) -> list[dict]:
    """Add SRD descriptions to feature names."""
    enriched = []
    for feat_str in feature_list:
        # Parse "L3: Feature Name" format
        if ": " in feat_str:
            level_part, name = feat_str.split(": ", 1)
        else:
            level_part, name = feat_str, feat_str
        key = name.lower()
        desc = FEATURE_DESCRIPTIONS.get(key, "")
        enriched.append({"name": name, "level": level_part, "description": desc})
    return enriched


async def _call_gemini(prompt: str) -> str | None:
    """Tier 1: Google Gemini. Requires GOOGLE_API_KEY."""
    key = os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}",
                json={"contents": [{"parts": [{"text": prompt}]}]},
            )
            result = resp.json()
            return result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return None

async def _call_openrouter(prompt: str) -> str | None:
    """Tier 2: OpenRouter free router (never charges). Requires OPENROUTER_API_KEY."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://characters.jamlarnet.stream",
                    "X-OpenRouter-Title": "D&D Character Manager",
                },
                json={
                    "model": "openrouter/free",  # auto-load-balances free models, can't charge
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 300,
                },
            )
            result = resp.json()
            return result["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[OR] error: {e}")
        return None

async def _call_ollama(prompt: str) -> str | None:
    """Tier 3: Local Ollama hermes3:8b. No API key needed."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "http://192.168.1.31:11434/api/generate",
                json={"model": "hermes3:8b-llama3.1-q8_0", "prompt": prompt, "stream": False},
            )
            result = resp.json()
            return result.get("response", "")
    except Exception:
        return None

async def _fetch_stable_horde_image(prompt: str, max_wait: int = 120) -> str | None:
    """Generate image via Stable Horde (free, no API key, crowdsourced).
    Returns base64 data URL or None.
    Uses urllib in a thread to avoid httpx IPv6 issues."""
    import base64, asyncio, json, urllib.request, urllib.error
    payload = json.dumps({
        "prompt": prompt[:1000],
        "params": {
            "width": 384, "height": 512,
            "steps": 15, "n": 1,
            "sampler_name": "k_euler_a",
        }
    }).encode()

    def _sync_request(url, data=None, headers=None, timeout=30):
        req = urllib.request.Request(url, data=data, headers=headers or {})
        return urllib.request.urlopen(req, timeout=timeout)

    try:
        # Submit job
        headers = {"apikey": "0000000000", "Content-Type": "application/json"}
        resp = await asyncio.to_thread(_sync_request,
            "https://stablehorde.net/api/v2/generate/async", payload, headers, 30)
        if resp.status != 202:
            print(f"[IMG] Stable Horde submit failed: {resp.status}")
            return None
        req_id = json.loads(resp.read()).get("id", "")
        if not req_id:
            return None
        print(f"[IMG] Stable Horde job {req_id} submitted")

        # Poll for result
        for i in range(max_wait // 2):
            await asyncio.sleep(2)
            try:
                sr = await asyncio.to_thread(_sync_request,
                    f"https://stablehorde.net/api/v2/generate/status/{req_id}",
                    None, {}, 15)
                status = json.loads(sr.read())
                if status.get("done"):
                    gen = status.get("generations", [{}])[0]
                    img_url = gen.get("img", "")
                    if img_url:
                        ir = await asyncio.to_thread(_sync_request, img_url, None, {}, 30)
                        ct = ir.headers.get("Content-Type", "image/webp")
                        b64 = base64.b64encode(ir.read()).decode()
                        print(f"[IMG] Stable Horde done after {(i+1)*2}s")
                        return f"data:{ct};base64,{b64}"
                    break
                if i % 5 == 0:
                    print(f"[IMG] waiting... {i*2}s")
            except Exception:
                pass
        print(f"[IMG] Stable Horde timed out after {max_wait}s")
    except Exception as e:
        print(f"[IMG] Stable Horde error: {e}")
    return None

def _extract_json(text: str) -> dict | None:
    """Extract JSON from LLM response, stripping markdown wrappers."""
    if not text:
        return None
    if "```" in text:
        block = text.split("```")[1]
        if block.startswith("json"):
            block = block[4:]
        text = block
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return None

def _validate_and_fix(ai: dict, race: str = "", class_name: str = "", name: str = "") -> dict:
    """Ground AI output in PHB data. Fix hallucinations silently."""
    # Validate background against PHB list (p.125-141)
    if ai.get("background") not in PHB_BACKGROUNDS:
        ai["background"] = random.choice(PHB_BACKGROUNDS)
    # Validate alignment against PHB list (p.122)
    if ai.get("alignment") not in PHB_ALIGNMENTS:
        ai["alignment"] = random.choice(PHB_ALIGNMENTS)
    # Ensure name exists
    if not ai.get("name"):
        ai["name"] = name or random_name(race)["name"]
    # Ensure personality + backstory exist
    if not ai.get("personality"):
        ai["personality"] = f"Brave but reckless. Loyal to friends. Distrusts authority."
    if not ai.get("backstory"):
        bg = ai.get("background", "adventurer").lower()
        ai["backstory"] = f"A {race} {class_name} who grew up as a {bg}. They seek adventure and glory."
    return ai

@app.post("/api/ai/generate", response_class=JSONResponse)
async def ai_generate(request: Request):
    """Generate character flavor (name/bg/alignment/personality/backstory).
    All mechanical data is hardcoded from PHB. Only creative text is generated.
    Model chain: Gemini → OpenRouter → Ollama → deterministic fallback."""
    user = require_user(request)
    data = await request.json()
    race = data.get("race", "Human")
    subrace = data.get("subrace", "")
    class_name = data.get("class_name", "Fighter")
    subclass = data.get("subclass", "")
    name = data.get("name", "")

    # Prompt is constrained to PHB-approved options only
    bg_list = ", ".join(PHB_BACKGROUNDS)
    al_list = ", ".join(PHB_ALIGNMENTS)
    prompt = f"""Generate a D&D 5e character concept using ONLY these official Player's Handbook options.

Race: {race}{' (' + subrace + ')' if subrace else ''}
Class: {class_name}{' — ' + subclass if subclass else ''}
PHB Backgrounds: {bg_list}
PHB Alignments: {al_list}
{'Player name suggestion: ' + name if name else 'Generate a race-appropriate name.'}

Return ONLY valid JSON (no markdown, no explanation):
{{"name": "Firstname Lastname", "background": "one from PHB list above", "alignment": "one from PHB list above", "personality": "2-3 personality traits", "backstory": "2-3 sentence backstory connecting race, class, and background"}}"""

    # Tiered model chain
    TIER_NAMES = ["gemini", "openrouter", "ollama"]
    text = None
    used_tier = None
    for tier, caller in enumerate([_call_gemini, _call_openrouter, _call_ollama]):
        text = await caller(prompt)
        if text:
            used_tier = TIER_NAMES[tier]
            break
    print(f"[AI] tier={used_tier or 'fallback'} race={race} class={class_name}")

    ai = _extract_json(text) if text else None
    if ai:
        ai = _validate_and_fix(ai, race, class_name, name)
    else:
        ai = _fallback_generate(race, class_name, subclass, name)
    return JSONResponse(ai)

def _fallback_generate(race: str, class_name: str, subclass: str, name: str) -> dict:
    """Deterministic fallback when AI is unavailable."""
    if not name:
        name = random_name(race)["name"]
    bg = random.choice(BACKGROUNDS)
    al = random.choice(ALIGNMENTS)
    return {
        "name": name,
        "background": bg,
        "alignment": al,
        "personality": "Brave but reckless. Loyal to friends. Distrusts authority.",
        "backstory": f"A {race} {class_name}{' of the ' + subclass if subclass else ''} who grew up as a {bg.lower()}. They seek adventure and glory, driven by a desire to prove themselves to the world."
    }

# ── Custom Background Generation ──────────────────────────────────────────

# Background-appropriate items (common magic + mundane equipment from SRD/PHB)
# Format: "Name (SRD reference)" for magic items, plain name for mundane
BACKGROUND_ITEM_POOL = [
    # Potions (common)
    "Potion of Healing (SRD: Potion of Healing)",
    "Potion of Climbing (SRD: Potion of Climbing)",
    # Scrolls (common)
    "Spell Scroll — Cantrip (SRD: Spell Scroll Cantrip)",
    "Spell Scroll — 1st level (SRD: Spell Scroll 1st)",
    # Minor magic items (uncommon, not overpowered)
    "Hat of Disguise (SRD: Hat of Disguise)",
    "Goggles of Night (SRD: Goggles of Night)",
    "Rope of Climbing (SRD: Rope of Climbing)",
    "Helm of Comprehending Languages (SRD: Helm of Comprehending Languages)",
    "Lantern of Revealing (SRD: Lantern of Revealing)",
    "Potion of Animal Friendship (SRD: Potion of Animal Friendship)",
    "Potion of Water Breathing (SRD: Potion of Water Breathing)",
    "Potion of Growth (SRD: Potion of Growth)",
    "Oil of Slipperiness (SRD: Oil of Slipperiness)",
    "Dust of Disappearance (SRD: Dust of Disappearance)",
    "Restorative Ointment (SRD: Restorative Ointment)",
    # Mundane equipment / tools (PHB)
    "Healer's Kit",
    "Climber's Kit",
    "Disguise Kit",
    "Herbalism Kit",
    "Navigator's Tools",
    "Thieves' Tools",
    "Hunting Trap",
    "Magnifying Glass",
    "Spyglass",
    "Signal Whistle",
    "Grappling Hook",
    "Crowbar",
    "Block and Tackle",
    "Abacus",
    "Sealing Wax",
    "Merchant's Scale",
    "Fishing Tackle",
    "Hourglass",
    "Steel Mirror",
    "Vial of Perfume",
    "Vial of Acid",
    "Vial of Alchemist's Fire",
    "Vial of Antitoxin",
    "Flask of Holy Water",
    "Blanket",
    "Tinderbox",
    "Waterskin",
    "Rations (2 days)",
    "Chalk (5 pieces)",
    "String (10 feet)",
    "Bell",
    "Candle",
    "Soap",
    "Iron Pot",
    "Shovel",
    "Whetstone",
    "Two-Person Tent",
    # Trinkets and flavor
    "Silver locket with a portrait inside",
    "Small mechanical bird that chirps",
    "Bag of polished river stones",
    "Bone dice set with a worn leather cup",
    "Lucky rabbit's foot",
    "Old brass compass that doesn't point north",
    "Glass orb filled with swirling smoke",
    "Carved wooden whistle shaped like a dragon",
    "Sealed letter with a wax sigil",
    "Worn journal with cryptic entries",
]

@app.post("/api/ai/generate-background", response_class=JSONResponse)
async def ai_generate_background(request: Request):
    """Generate a unique custom background based on character choices.
    Model chain: Gemini → OpenRouter → Ollama → deterministic fallback."""
    user = require_user(request)
    data = await request.json()
    race = data.get("race", "Human")
    subrace = data.get("subrace", "")
    class_name = data.get("class_name", "Fighter")
    subclass = data.get("subclass", "")
    abilities = data.get("abilities", {})
    skills = data.get("skills", [])
    alignment = data.get("alignment", "")

    prompt = f"""Create a UNIQUE D&D 5e custom background for this character. Do NOT use any of the 13 standard PHB backgrounds (Acolyte, Charlatan, Criminal, Entertainer, Folk Hero, Guild Artisan, Hermit, Noble, Outlander, Sage, Sailor, Soldier, Urchin). Invent something new and specific to this character.

Race: {race}{' (' + subrace + ')' if subrace else ''}
Class: {class_name}{' — ' + subclass if subclass else ''}
Ability scores: {abilities}
Skills: {skills}
Alignment: {alignment or 'Any'}

Choose exactly 3 items from this approved list. You may add flavor/rename them (e.g., "Granny's Healer's Kit"), but ALWAYS include the SRD reference in parentheses — e.g., "Granny's Kit (SRD: Healer's Kit)". Mundane items don't need an SRD reference. Do NOT invent items not on this list. Do NOT include armor, weapons, or powerful magic items.

Available items:
{chr(10).join('- ' + it for it in BACKGROUND_ITEM_POOL)}

Return ONLY valid JSON (no markdown, no explanation):
{{"name": "Background Name (2-4 words, creative and unique)", "description": "2-3 sentence description of this character's background and how it shaped them", "items": ["Flavored Name (SRD: Reference)", "Item 2", "Item 3"], "gp": 15}}"""

    TIER_NAMES = ["gemini", "openrouter", "ollama"]
    text = None
    used_tier = None
    for tier, caller in enumerate([_call_gemini, _call_openrouter, _call_ollama]):
        text = await caller(prompt)
        if text:
            used_tier = TIER_NAMES[tier]
            break
    print(f"[AI bg] tier={used_tier or 'fallback'} race={race} class={class_name}")

    ai = _extract_json(text) if text else None
    if ai and ai.get("name") and ai.get("description"):
        # Ensure items and gp exist
        if "items" not in ai or not isinstance(ai.get("items"), list):
            ai["items"] = _random_items(class_name)
        if "gp" not in ai:
            ai["gp"] = random.randint(10, 25)
        # Validate uniqueness — reject if it matches a PHB background name
        if ai["name"] in BACKGROUNDS:
            ai = _fallback_background(race, class_name, subclass)
    else:
        ai = _fallback_background(race, class_name, subclass)
    return JSONResponse(ai)

def _random_items(class_name: str) -> list:
    """Pick 3 random background-appropriate items from the pool."""
    return random.sample(BACKGROUND_ITEM_POOL, min(3, len(BACKGROUND_ITEM_POOL)))

def _fallback_background(race: str, class_name: str, subclass: str = "") -> dict:
    """Deterministic fallback — pool of unique backgrounds by class."""
    bg_pool = {
        "Barbarian": [
            {"name":"Tribal Outcast","desc":"Exiled from their clan for refusing to follow a corrupt chieftain. They wander the wilds, honing their rage into a weapon of justice.","items":["Clan Hunter's Trap (SRD: Hunting Trap)","Bone Dice Set (SRD: Bone dice set with a worn leather cup)","Whetstone"],"gp":12},
            {"name":"Spirit-Blessed Wanderer","desc":"Touched by ancestral spirits during a near-death experience. They follow visions across the land, guided by voices only they can hear.","items":["Spirit Ward Pouch (SRD: Restorative Ointment)","Blanket","Tinderbox"],"gp":10},
            {"name":"Arena Champion","desc":"Fought for survival in underground fighting pits. They earned their freedom through blood and now seek a greater purpose.","items":["Victor's Brew (SRD: Potion of Healing)","Iron Pot","Soap"],"gp":18},
        ],
        "Bard": [
            {"name":"Traveling Minstrel","desc":"Performed in taverns and courts across the realm. Their songs carry secrets overheard from nobles and commoners alike.","items":["Disguise Kit","Vial of Perfume","Signal Whistle"],"gp":14},
            {"name":"Disgraced Courtier","desc":"Once a trusted advisor who uncovered a conspiracy. Framed for treason, they now travel under a new identity, seeking the truth.","items":["Courtier's Veil (SRD: Hat of Disguise)","Sealing Wax","Sealed letter with a wax sigil"],"gp":20},
        ],
        "Cleric": [
            {"name":"Pilgrim of the Lost","desc":"Received a divine vision during a plague that wiped out their village. They now travel, healing the sick and seeking answers.","items":["Sacred Salve (SRD: Restorative Ointment)","Healer's Kit","Candle"],"gp":11},
            {"name":"Heretic Reformer","desc":"Cast out from their temple for questioning doctrine. Their faith remains unshaken, but they now serve their god outside the clergy's walls.","items":["Forbidden Scroll (SRD: Spell Scroll — 1st level)","Flask of Holy Water","Worn journal with cryptic entries"],"gp":15},
        ],
        "Druid": [
            {"name":"Grove Warden","desc":"Sworn protector of an ancient forest grove. The trees whisper warnings to them, and animals treat them as kin.","items":["Herbalism Kit","Fishing Tackle","Carved wooden whistle shaped like a dragon"],"gp":10},
        ],
        "Fighter": [
            {"name":"Broken Legionnaire","desc":"The sole survivor of a massacre that destroyed their company. They carry the unit's standard, seeking to restore its honor.","items":["Whetstone","Crowbar","Blanket"],"gp":13},
            {"name":"Village Guardian","desc":"A small-town militia captain who stood against a monster incursion. They now seek proper training to protect others.","items":["Healer's Kit","Signal Whistle","Iron Pot"],"gp":16},
        ],
        "Monk": [
            {"name":"Mountain Acolyte","desc":"Trained in a remote monastery hidden among the peaks. They descend to the world below on a mission of enlightenment.","items":["Meditation Scroll (SRD: Spell Scroll — Cantrip)","Hourglass","Bell"],"gp":10},
        ],
        "Paladin": [
            {"name":"Oathsworn Avenger","desc":"Witnessed their mentor fall to corruption. They swore an oath over the mentor's grave to root out evil wherever it hides.","items":["Blessed Vial (SRD: Flask of Holy Water)","Steel Mirror","Candle"],"gp":14},
        ],
        "Ranger": [
            {"name":"Frontier Scout","desc":"Mapped uncharted wilderness for a frontier settlement. They know every trail, every danger, and every safe haven for 50 miles.","items":["Climber's Kit","Old brass compass that doesn't point north","Tinderbox"],"gp":12},
        ],
        "Rogue": [
            {"name":"Reformed Smuggler","desc":"Ran contraband through city sewers before a close call with the law. They now use their skills for more honest — but equally thrilling — work.","items":["Thieves' Tools","Grappling Hook","Chalk (5 pieces)"],"gp":15},
        ],
        "Sorcerer": [
            {"name":"Wild Magic Prodigy","desc":"Their powers erupted during a childhood accident, destroying their home. They've spent years learning to control the chaos within.","items":["Arcanic Spark (SRD: Spell Scroll — Cantrip)","Glass orb filled with swirling smoke","Chalk (5 pieces)"],"gp":12},
        ],
        "Warlock": [
            {"name":"Reluctant Pact-Bearer","desc":"Made a desperate bargain to save a loved one. Now bound to a mysterious patron, they seek a way to break free — or at least understand the terms.","items":["Patron's Token (SRD: Sealed letter with a wax sigil)","Candle","Silver locket with a portrait inside"],"gp":10},
        ],
        "Wizard": [
            {"name":"Arcane Archivist","desc":"Apprenticed to an eccentric librarian who guarded forbidden knowledge. They inherited the library's secrets when the old mage vanished.","items":["Scholar's Scroll (SRD: Spell Scroll — 1st level)","Magnifying Glass","Worn journal with cryptic entries"],"gp":14},
            {"name":"Failed Academy Student","desc":"Expelled from a prestigious magical academy for an experiment gone wrong. They continue their studies independently, determined to prove the academy wrong.","items":["Practitioner's Lens (SRD: Magnifying Glass)","Vial of Acid","Abacus"],"gp":11},
        ],
    }
    pool = bg_pool.get(class_name, [
        {"name":"Wandering Adventurer","desc":"Grew up hearing tales of heroes and monsters. When tragedy struck their hometown, they took up arms and never looked back.","items":["Healer's Kit","Tinderbox","Waterskin"],"gp":10}
    ])
    bg = random.choice(pool)
    return {"name": bg["name"], "description": bg["desc"], "items": list(bg["items"]), "gp": bg["gp"]}

# ── Character Portrait Generation ────────────────────────────────────────

@app.post("/api/ai/portrait", response_class=JSONResponse)
async def ai_portrait(request: Request):
    """Generate a high-fantasy character portrait. Returns image URL.
    If custom_prompt provided, uses it directly (skips AI generation).
    If character_id provided, persists the result to the DB."""
    user = require_user(request)
    data = await request.json()
    race = data.get("race", "Human")
    subrace = data.get("subrace", "")
    class_name = data.get("class_name", "Fighter")
    subclass = data.get("subclass", "")
    abilities = data.get("abilities", {})
    background = data.get("background", "")
    alignment = data.get("alignment", "")
    skills = data.get("skills", [])
    gender = data.get("gender", "")
    custom_prompt = (data.get("custom_prompt") or "").strip()
    character_id = data.get("character_id")

    if custom_prompt:
        # User-provided prompt — prepend style + framing directives
        image_prompt = f"Bust portrait, upper body only, 3:4 aspect ratio, close-up composition. High fantasy oil painting, dramatic lighting. {custom_prompt}"
        print(f"[AI portrait] custom_prompt race={race} class={class_name}")
    else:
        # Class-appropriate attire guidance
        CLASS_ATTIRE = {
            "Barbarian": "bare-chested or animal furs, NO heavy armor, tribal style, muscular and primal",
            "Bard": "flamboyant colorful performer's clothing, stylish but light, musical instrument visible",
            "Cleric": "robes with holy symbols, chainmail possible, divine motifs",
            "Druid": "natural materials, hides and furs, NO metal armor, nature motifs, wooden staff",
            "Fighter": "heavy armor, chainmail or plate, practical and battle-worn",
            "Monk": "simple monastic robes, bare hands and feet, unarmored, disciplined posture",
            "Paladin": "gleaming plate armor with holy symbols and divine motifs",
            "Ranger": "leather armor in forest tones, hooded cloak, practical and rugged",
            "Rogue": "dark leather armor, hooded, shadowy, stealthy, daggers visible",
            "Sorcerer": "flowing robes, NO armor, innate magical energy visible",
            "Warlock": "dark occult robes, eldritch symbols, NO heavy armor, arcane pact motifs",
            "Wizard": "scholarly robes, NO armor, spellbook or arcane focus, studious appearance",
        }
        attire = CLASS_ATTIRE.get(class_name, "appropriate adventuring attire")

        prompt = f"""Describe a D&D 5e character portrait in High Fantasy art style. Oil painting, dramatic lighting.

CRITICAL: This is a BUST portrait — head and upper chest only, no full body. 3:4 portrait aspect ratio, close-up composition, character fills the frame from the top of their head to mid-chest.

Character: {race}{' (' + subrace + ')' if subrace else ''} {class_name}{' — ' + subclass if subclass else ''}
Background: {background}
Alignment: {alignment or 'Unknown'}
Skills: {', '.join(skills) if skills else 'various'}
Key abilities: {', '.join(f'{k}:{v}' for k,v in sorted(abilities.items(), key=lambda x:-x[1])[:3]) if abilities else 'balanced'}
Class-appropriate attire: {attire}

Write a DETAILED image prompt (150-200 words) describing this character for an AI image generator. Include: face, build, hair, distinctive features, clothing/armor visible on upper body, weapon or focus if it fits in frame, pose, expression, lighting, background setting. Remember: BUST ONLY — head to mid-chest, 3:4 ratio, no legs, no full body. High fantasy oil painting style. Do NOT include the character name — just describe what they look like."""

        TIER_NAMES = ["gemini", "openrouter", "ollama"]
        text = None
        for tier, caller in enumerate([_call_gemini, _call_openrouter, _call_ollama]):
            text = await caller(prompt)
            if text:
                break
        print(f"[AI portrait] tier={'AI' if text else 'fallback'} race={race} class={class_name}")

        image_prompt = text.strip() if text else _fallback_portrait_prompt(race, class_name, subclass)

    # Generate image via Stable Horde (free, no API key)
    image_data = await _fetch_stable_horde_image(image_prompt)
    print(f"[AI portrait] image {'generated' if image_data else 'failed'} race={race} class={class_name}")

    # Persist to DB if character_id provided
    if character_id:
        db = get_db()
        db.execute("UPDATE characters SET portrait_url=?, portrait_prompt=? WHERE id=? AND user_id=?",
                   (image_data or "", image_prompt, character_id, user["id"]))
        db.commit()
        db.close()

    return JSONResponse({"prompt": image_prompt, "image_url": image_data or ""})

def _fallback_portrait_prompt(race: str, class_name: str, subclass: str = "") -> str:
    """Deterministic portrait prompts by class/race — all bust/upper-body framed."""
    prompts = {
        ("Dwarf","Barbarian"): "Bust portrait, 3:4 aspect ratio. A stout mountain dwarf barbarian with thick braided auburn hair and a scarred face. Bare-chested with a heavy fur mantle across broad shoulders, tribal tattoos visible on muscular upper arms. Gripping a massive greataxe, battle-ready expression, primal fury in eyes. Snow-capped peaks and storm clouds behind. Oil painting, dramatic lighting, high fantasy.",
        ("Dwarf","Cleric"): "Bust portrait, 3:4 aspect ratio. A venerable dwarf cleric with a silver-streaked beard and kind eyes. Robes of deep blue with gold embroidery across the chest, a holy symbol of Moradin hanging from a chain. One hand raised in blessing near the face, warm candlelight glow. Stone temple interior. Oil painting, high fantasy.",
        ("Dwarf","Fighter"): "Bust portrait, 3:4 aspect ratio. A battle-hardened dwarf fighter in chainmail armor visible across shoulders and chest, warhammer resting on one shoulder. Braided copper beard, stern expression, scar across one eyebrow. Mountain fortress stonework behind. Oil painting, dramatic lighting.",
        ("Elf","Wizard"): "Bust portrait, 3:4 aspect ratio. A slender high elf wizard with silver-white hair flowing past pointed ears. Deep purple robes with arcane sigils at the collar, a crystal-topped staff held diagonally across the frame. Faint magical aura, ancient library backdrop. Oil painting, ethereal lighting.",
        ("Elf","Ranger"): "Bust portrait, 3:4 aspect ratio. A wood elf ranger with amber eyes and copper hair pulled back. Leather armor in forest greens across shoulders, longbow visible over one shoulder. Hood partially up, alert expression, dappled sunlight. Ancient forest bokeh behind. Oil painting, high fantasy.",
        ("Elf","Rogue"): "Bust portrait, 3:4 aspect ratio. A sleek elven rogue with dark cropped hair and a knowing smirk. Black leather armor, dagger hilts visible at the collar, cloak half-drawn across shoulders. Moonlit shadows dancing across the face. Oil painting, chiaroscuro lighting.",
        ("Human","Paladin"): "Bust portrait, 3:4 aspect ratio. A noble human paladin with short-cropped blonde hair and a strong jaw. Gleaming plate armor with a sunburst emblem on the chestplate, longsword held near the face reflecting divine light. Radiant glow from behind. Oil painting, cinematic lighting.",
        ("Human","Fighter"): "Bust portrait, 3:4 aspect ratio. A weathered human fighter with close-cropped dark hair and a faint scar across the cheek. Well-worn scale mail across shoulders, longsword hilt visible at hip-level frame edge. Castle wall stonework behind. Oil painting, late afternoon light.",
        ("Half-Orc","Barbarian"): "Bust portrait, 3:4 aspect ratio. A towering half-orc barbarian with gray-green skin and tribal tattoos across the face and shoulders. Bald head, tusked jaw, fur mantle over bare chest. Massive axe head visible, primal snarl. Stormy sky background. Oil painting, dramatic lighting.",
        ("Tiefling","Warlock"): "Bust portrait, 3:4 aspect ratio. A tiefling warlock with deep purple skin and curved horns sweeping back from the forehead. Eyes glowing with eldritch fire, dark robes with infernal patterns at the collar. A crackling tome held near the chest. Shadowy ruins at midnight. Oil painting, occult atmosphere.",
    }
    key = (race, class_name)
    if key in prompts:
        return prompts[key]
    # Generic fallback
    race_features = {
        "Dwarf": "stout build, braided hair or beard",
        "Elf": "slender build, pointed ears, graceful",
        "Human": "determined expression, practical",
        "Half-Orc": "tusked, muscular, gray-green skin",
        "Halfling": "small stature, curly hair, cheerful",
        "Gnome": "small, bright-eyed, clever expression",
        "Tiefling": "horns, tail, otherworldly presence",
        "Dragonborn": "draconic features, scales, reptilian",
        "Half-Elf": "slightly pointed ears, mixed heritage beauty",
    }
    rf = race_features.get(race, "adventurous look")
    return f"Bust portrait, 3:4 aspect ratio. A {race.lower()} {class_name.lower()} with {rf}. Wearing appropriate {class_name.lower()} attire, upper body visible. Confident expression, high fantasy oil painting style with dramatic lighting. Rich colors, detailed background bokeh."

@app.post("/api/character/portrait", response_class=JSONResponse)
async def save_portrait_image(request: Request):
    """Save a client-generated portrait image (base64 data URL) to the DB."""
    user = require_user(request)
    data = await request.json()
    character_id = data.get("character_id")
    image_data = data.get("image_data", "")  # base64 data URL from Puter.js

    if not character_id or not image_data:
        return JSONResponse({"error": "character_id and image_data required"}, status_code=400)

    db = get_db()
    db.execute("UPDATE characters SET portrait_url=? WHERE id=? AND user_id=?",
               (image_data, character_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})

# ── Build Generation (PHB-grounded, level-aware) ────────────────────────────

@app.post("/api/ai/build", response_class=JSONResponse)
async def ai_build(request: Request):
    """Generate a complete optimized build for the given class/race/subclass/level."""
    user = require_user(request)
    data = await request.json()
    race = data.get("race", "Human")
    subrace = data.get("subrace", "")
    class_name = data.get("class_name", "Fighter")
    subclass = data.get("subclass", "")
    level = min(max(int(data.get("level", 1)), 1), 20)

    class_data = CLASSES.get(class_name, CLASSES["Fighter"])
    abilities = allocate_ability_scores(class_name, race, subrace)
    mods = {ability: modifier(score) for ability, score in abilities.items()}
    pb = PROFICIENCY_BONUS.get(level, 2)

    con_mod = mods["constitution"]
    hp = calc_hp(class_name, level, con_mod)
    ac = _calculate_ac(class_name, level, mods)

    saves = {ability: mod + (pb if ability in class_data["saves"] else 0)
             for ability, mod in mods.items()}

    skills = _pick_skills(class_name, mods)
    equipment = get_equipment_for_level(class_name, level)
    magic_items = pick_magic_items(class_name, level)
    feats = get_feats_for_level(class_name, level)

    spell_data = get_spells_for_level(class_name, level)
    spell_slots = get_spell_slots(class_name, level)

    attacks = _calculate_attacks(class_name, level, mods, pb, equipment)

    raw_features = get_class_features(class_name, level, subclass)
    enriched_features = enrich_features(raw_features)

    race_data = RACES.get(race, RACES["Human"])
    build = {
        "class": class_name,
        "subclass": subclass or None,
        "race": race,
        "subrace": subrace or None,
        "level": level,
        "proficiency_bonus": pb,
        "hit_points": hp,
        "hit_dice": f"{level}d{class_data['hd']}",
        "armor_class": ac,
        "initiative": mods["dexterity"],
        "speed": race_data["speed"],
        "ability_scores": abilities,
        "modifiers": mods,
        "saving_throws": saves,
        "skills": skills,
        "equipment": equipment,
        "magic_items": magic_items,
        "feats": feats,
        "cantrips": spell_data.get("cantrips", []),
        "spells": spell_data.get("spells", {}),
        "spell_slots": spell_slots,
        "attacks": attacks,
        "features": enriched_features,
    }
    return JSONResponse(build)


def _calculate_ac(class_name: str, level: int, mods: dict) -> int:
    dex = mods["dexterity"]
    if class_name == "Barbarian":
        con = mods["constitution"]
        base = 15 if level >= 20 else 14 if level >= 10 else 13 if level >= 5 else 12
        return base + dex + con
    if class_name == "Monk":
        wis = mods["wisdom"]
        return 10 + dex + wis
    if class_name in ("Wizard", "Sorcerer"):
        if level >= 5: return 13 + min(dex, 2)
        return 10 + dex
    if class_name == "Warlock":
        if level >= 20: return 12 + dex + 3
        if level >= 15: return 12 + min(dex, 5) + 2
        if level >= 10: return 12 + min(dex, 5) + 1
        if level >= 5: return 12 + min(dex, 5)
        return 11 + min(dex, 5)
    if class_name in ("Bard", "Rogue", "Ranger"):
        if level >= 20: return 12 + min(dex, 5) + 3
        if level >= 15: return 12 + min(dex, 5) + 2
        if level >= 10: return 12 + min(dex, 5) + 1
        if level >= 5: return 12 + min(dex, 5)
        return 11 + min(dex, 5)
    if class_name == "Druid":
        if level >= 20: return 15 + min(dex, 2) + 3
        if level >= 10: return 12 + min(dex, 5) + 1
        return 11 + min(dex, 5)
    if class_name == "Cleric":
        if level >= 20: return 18 + 3 + 2
        if level >= 10: return 18 + 1 + 2
        return 14 + min(dex, 2) + 2
    if level >= 20: return 18 + 3
    if level >= 15: return 18 + 2
    if level >= 10: return 18 + 1
    if level >= 5: return 18
    return 16 + 2


def _calculate_attacks(class_name: str, level: int, mods: dict, pb: int, equipment: list) -> list:
    str_mod = mods["strength"]
    dex_mod = mods["dexterity"]
    magic = 3 if level >= 20 else 2 if level >= 10 else 1 if level >= 5 else 0

    if class_name == "Warlock":
        beams = 4 if level >= 17 else 3 if level >= 11 else 2 if level >= 5 else 1
        cha = mods["charisma"]
        return [{"name": f"Eldritch Blast ({beams} beam{'s' if beams > 1 else ''})",
                 "attack_bonus": cha + pb, "damage": f"1d10+{cha} per beam",
                 "type": "force", "range": "120 ft"}]

    if class_name == "Ranger":
        return [{"name": f"Longbow +{magic}" if magic else "Longbow",
                 "attack_bonus": dex_mod + pb + magic, "damage": f"1d8+{dex_mod + magic}",
                 "type": "piercing", "range": "150/600"}]

    if class_name in ("Rogue", "Monk"):
        atk = dex_mod
        weapon = "Shortsword" if class_name == "Monk" else "Rapier"
        die = "1d6" if class_name == "Monk" else "1d8"
        return [{"name": f"{weapon} +{magic}" if magic else weapon,
                 "attack_bonus": atk + pb + magic, "damage": f"{die}+{atk + magic}",
                 "type": "piercing"}]

    atk = str_mod
    if class_name in ("Barbarian", "Fighter", "Paladin"):
        weapon = "Greatsword" if level >= 15 else "Longsword"
        die = "2d6" if level >= 15 else "1d8"
        if class_name == "Fighter" and dex_mod > str_mod:
            atk = dex_mod
    elif class_name == "Bard":
        atk = max(dex_mod, str_mod)
        weapon = "Rapier"
        die = "1d8"
    else:
        atk = max(str_mod, dex_mod)
        weapon = "Mace" if class_name == "Cleric" else "Quarterstaff"
        die = "1d6"

    return [{"name": f"{weapon} +{magic}" if magic else weapon,
             "attack_bonus": atk + pb + magic, "damage": f"{die}+{atk + magic}",
             "type": "slashing" if weapon in ("Greatsword", "Longsword") else "bludgeoning"}]


def _pick_skills(class_name: str, mods: dict) -> list[str]:
    class_data = CLASSES.get(class_name, CLASSES["Fighter"])
    available = class_data["skills"]
    if available == "all":
        available = ALL_SKILLS
    count = class_data["skill_count"]
    scored = [(skill, mods.get(SKILL_ABILITIES.get(skill, "intelligence"), 0))
              for skill in available]
    scored.sort(key=lambda x: -x[1])
    return [s[0] for s in scored[:count]]


# ── Startup ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    init_db()

# ── Reference Manual Lookup ─────────────────────────────────────────────────
# 18 D&D 5e PDFs on SlowDisk. Query by filename or indexed metadata.

MANUALS_BASE = Path("/media/james/SlowDisk1tb/dnd-character-manager/manuals")

@app.get("/api/reference/manuals", response_class=JSONResponse)
async def list_manuals(request: Request):
    """List available reference manuals. No auth required."""
    if not MANUALS_BASE.exists():
        return JSONResponse({"manuals": [], "warning": "Manual directory not found"})
    import glob
    pdfs = sorted(glob.glob(str(MANUALS_BASE / "*.pdf")) + glob.glob(str(MANUALS_BASE / "*/*.pdf")))
    return JSONResponse({
        "count": len(pdfs),
        "manuals": [Path(p).name for p in pdfs],
        "path": str(MANUALS_BASE),
    })

@app.post("/api/reference/query", response_class=JSONResponse)
async def query_reference(request: Request):
    """Query reference manuals for PHB-grounding. Requires auth.
    Accepts: {"query": "dwarf racial traits", "source": "phb"}"""
    user = require_user(request)
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

# ── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8300)
