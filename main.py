"""D&D Character Manager — FastAPI webapp with multi-user character tracking."""
from __future__ import annotations

import json
import os
import asyncio
import random
import re
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
    desc = " ".join(f.get("desc", []))
    if desc:
        FEATURE_DESCRIPTIONS[key] = desc

# ═══════════════════════════════════════════════════════════════════════════════
# ITEM INDEX — unified equipment + magic items with SRD/PHB 2014 descriptions
# ═══════════════════════════════════════════════════════════════════════════════

SRD_EQUIPMENT: list[dict] = _load_json_cache("equipment.json")

def _build_item_description(item: dict) -> str:
    """Generate a PHB 2014-accurate description for any equipment item.
    Uses SRD desc if available, otherwise derives from metadata."""
    cat = item.get("equipment_category", {}).get("name", "")
    desc_list = item.get("desc", [])
    if desc_list:
        return " ".join(desc_list)

    # ── Weapons ──
    if cat == "Weapon":
        wcat = item.get("weapon_category", "Simple")
        wrange = item.get("weapon_range", "Melee")
        dmg = item.get("damage", {})
        dmg_dice = dmg.get("damage_dice", "")
        dmg_type = (dmg.get("damage_type", {}) or {}).get("name", "")
        props = [p.get("name", "") for p in item.get("properties", [])]

        # Build range string
        range_part = wrange
        normal = item.get("range", {}).get("normal")
        if normal:
            long_range = item.get("range", {}).get("long", "")
            range_part = f"Range {normal}/{long_range} ft"

        # Build properties string
        prop_strs = []
        for p in props:
            if p == "Versatile":
                vdmg = item.get("two_handed_damage", {}).get("damage_dice", "")
                prop_strs.append(f"versatile ({vdmg})" if vdmg else "versatile")
            elif p == "Thrown":
                thr = item.get("throw_range", {})
                tn = thr.get("normal", "")
                tl = thr.get("long", "")
                prop_strs.append(f"thrown (range {tn}/{tl})" if tn else "thrown")
            elif p == "Ammunition":
                prop_strs.append("ammunition")
            elif p == "Finesse":
                prop_strs.append("finesse")
            elif p == "Heavy":
                prop_strs.append("heavy")
            elif p == "Light":
                prop_strs.append("light")
            elif p == "Loading":
                prop_strs.append("loading")
            elif p == "Reach":
                prop_strs.append("reach")
            elif p == "Two-Handed":
                prop_strs.append("two-handed")
            elif p == "Special":
                prop_strs.append("special")
            elif p == "Monk":
                prop_strs.append("monk")
            else:
                prop_strs.append(p.lower())

        cost = item.get("cost", {})
        cost_str = f"{cost.get('quantity','?')} {cost.get('unit','gp')}"
        weight = item.get("weight", "?")
        parts = [f"{wcat} {wrange.lower()} weapon"]
        if dmg_dice:
            parts.append(f"{dmg_dice} {dmg_type.lower()}" if dmg_type else dmg_dice)
        if prop_strs:
            parts.append(". ".join(prop_strs))
        parts.append(f"{weight} lb. {cost_str}.")
        return " ".join(f"{p}{'.' if i==0 and not p.endswith('.') else ''}" if i > 0 and not p.startswith('.') else p for i, p in enumerate(parts)).replace("..", ".").replace(" .", ".")

    # ── Armor ──
    if cat == "Armor":
        ac = item.get("armor_class", {}).get("base", "?")
        armor_cat = item.get("armor_category", "Armor")
        ac_parts = [str(ac)]
        if item.get("armor_class", {}).get("dex_bonus"):
            dex = item["armor_class"]["dex_bonus"]
            ac_parts.append(f"+ Dex modifier (max {item['armor_class'].get('max_bonus', dex)})")
        ac_str = " + ".join(ac_parts)
        notes = []
        if item.get("str_minimum", 0) > 0:
            notes.append(f"Requires STR {item['str_minimum']}")
        if item.get("stealth_disadvantage"):
            notes.append("disadvantage on Stealth")
        cost = item.get("cost", {})
        cost_str = f"{cost.get('quantity','?')} {cost.get('unit','gp')}"
        weight = item.get("weight", "?")
        desc = f"{armor_cat} armor. AC {ac_str}. "
        if notes:
            desc += ", ".join(notes) + ". "
        desc += f"{weight} lb. {cost_str}."
        return desc

    # ── Tools ──
    if cat == "Tools":
        tname = item.get("name", "")
        tool_descs = {
            "Alchemist's supplies": "Used for alchemical crafting. Proficiency lets you add your bonus to checks made to identify potions, poisons, and alchemical substances. 8 lb. 50 gp.",
            "Brewer's supplies": "Used for brewing beer and ale. Proficiency lets you add your bonus to checks made to identify or craft beverages. 9 lb. 20 gp.",
            "Calligrapher's supplies": "Used for calligraphy, illuminating manuscripts, and detecting forgeries. Proficiency adds your bonus to checks related to writing. 5 lb. 10 gp.",
            "Carpenter's tools": "Used for woodworking and construction. Proficiency lets you add your bonus to checks to build, repair, or identify wooden structures. 6 lb. 8 gp.",
            "Cartographer's tools": "Used for mapping and charting. Proficiency lets you add your bonus to checks to navigate, create maps, or identify landmarks. 6 lb. 15 gp.",
            "Cobbler's tools": "Used for shoemaking and leatherwork for footwear. Proficiency adds your bonus to checks to repair or identify footwear. 5 lb. 5 gp.",
            "Cook's utensils": "Used for preparing meals. Proficiency lets you add your bonus to checks to cook, identify ingredients, or detect spoiled food. 8 lb. 1 gp.",
            "Glassblower's tools": "Used for shaping molten glass. Proficiency adds your bonus to checks to create, identify, or repair glass objects. 5 lb. 30 gp.",
            "Jeweler's tools": "Used for crafting jewelry and identifying gems. Proficiency lets you add your bonus to checks to appraise jewelry or identify gemstones. 2 lb. 25 gp.",
            "Leatherworker's tools": "Used for working with leather and hides. Proficiency adds your bonus to checks to create, repair, or identify leather goods. 5 lb. 5 gp.",
            "Mason's tools": "Used for stonework and construction. Proficiency lets you add your bonus to checks to build, demolish, or identify stone structures. 8 lb. 10 gp.",
            "Painter's supplies": "Used for painting and creating artwork. Proficiency adds your bonus to checks to create, identify, or authenticate paintings. 5 lb. 10 gp.",
            "Potter's tools": "Used for shaping clay and ceramics. Proficiency adds your bonus to checks to create, identify, or repair ceramic objects. 3 lb. 10 gp.",
            "Smith's tools": "Used for metalworking, forging, and repairing metal objects. Proficiency lets you add your bonus to checks to craft, identify, or repair metal items. 8 lb. 20 gp.",
            "Tinker's tools": "Used for tinkering with small mechanical devices. Proficiency lets you add your bonus to checks to repair or create small mechanical objects. 10 lb. 50 gp.",
            "Weaver's tools": "Used for working with cloth, thread, and textiles. Proficiency adds your bonus to checks to create, identify, or repair fabric items. 5 lb. 1 gp.",
            "Woodcarver's tools": "Used for carving wood into small objects. Proficiency adds your bonus to checks to create, identify, or repair wooden crafts. 5 lb. 1 gp.",
            "Navigator's tools": "Used for navigation at sea. Proficiency lets you add your bonus to checks to determine location, avoid getting lost, and chart courses. 2 lb. 25 gp.",
            "Thieves' tools": "Used for picking locks and disarming traps. Proficiency lets you add your bonus to ability checks made to disarm traps or pick locks. 1 lb. 25 gp.",
            "Disguise kit": "Used for creating disguises and costumes. Proficiency lets you add your bonus to checks made to create a visual disguise. 3 lb. 25 gp.",
            "Forgery kit": "Used for forging documents. Proficiency lets you add your bonus to checks made to create a physical counterfeit of a document. 5 lb. 15 gp.",
            "Herbalism kit": "Used for creating herbal remedies, antitoxins, and potions of healing. Proficiency lets you add your bonus to checks made to identify or apply herbs. 3 lb. 5 gp.",
            "Poisoner's kit": "Used for creating and applying poisons. Proficiency lets you add your bonus to checks made to create, identify, or handle poisons. 2 lb. 50 gp.",
            "Dice set": "A set of dice for games of chance. Proficiency lets you add your bonus to checks made to play that game. 0 lb. 1 sp.",
            "Dragonchess set": "A complex strategy board game popular among nobles. Proficiency lets you add your bonus to checks made to play Dragonchess. 0.5 lb. 1 gp.",
            "Playing card set": "A deck of cards for gambling and games. Proficiency lets you add your bonus to checks made to play card games. 0 lb. 5 sp.",
            "Three-Dragon Ante set": "A betting card game themed around dragons. Proficiency lets you add your bonus to checks to play Three-Dragon Ante. 0 lb. 1 gp.",
            "Bagpipes": "A musical wind instrument using enclosed reeds. Proficiency lets you add your bonus to Performance checks with this instrument. 6 lb. 30 gp.",
            "Drum": "A percussion instrument. Proficiency lets you add bonus to Performance checks. 3 lb. 6 gp.",
            "Dulcimer": "A stringed instrument played with hammers. Proficiency adds bonus to Performance checks. 10 lb. 25 gp.",
            "Flute": "A woodwind instrument. Proficiency adds bonus to Performance checks. 1 lb. 2 gp.",
            "Lute": "A stringed instrument similar to a guitar. Proficiency adds bonus to Performance checks. 2 lb. 35 gp.",
            "Lyre": "A small harp-like stringed instrument. Proficiency adds bonus to Performance checks. 2 lb. 30 gp.",
            "Horn": "A brass wind instrument. Proficiency adds bonus to Performance checks. 2 lb. 3 gp.",
            "Pan flute": "A set of graduated pipes. Proficiency adds bonus to Performance checks. 2 lb. 12 gp.",
            "Shawm": "A woodwind instrument (double reed, precursor to oboe). Proficiency adds bonus to Performance checks. 1 lb. 2 gp.",
            "Viol": "A bowed string instrument. Proficiency adds bonus to Performance checks. 1 lb. 30 gp.",
        }
        if tname in tool_descs:
            return tool_descs[tname]
        cost = item.get("cost", {})
        cost_str = f"{cost.get('quantity','?')} {cost.get('unit','gp')}"
        weight = item.get("weight", "?")
        return f"A set of tools for {tname.lower()}. {weight} lb. {cost_str}."

    # ── Adventuring Gear ──
    gear_descriptions = {
        "Abacus": "A portable calculating device — a wooden frame with beads on rods. Used for arithmetic and accounting. 2 gp. 2 lb.",
        "Acid (vial)": "As an action, splash the contents of this vial onto a creature within 5 feet, or throw the vial up to 20 feet, shattering it on impact. On a hit, the target takes 2d6 acid damage. 25 gp. 1 lb.",
        "Alchemist's fire (flask)": "A sticky, adhesive fluid that ignites when exposed to air. As an action, throw this flask up to 20 feet, shattering on impact. Target takes 1d4 fire damage at the start of each of its turns until it uses an action to extinguish the flames. 50 gp. 1 lb.",
        "Antitoxin (vial)": "Drink this vial to gain advantage on saving throws against poison for 1 hour. 50 gp.",
        "Arcane focus — Crystal": "An arcane focus is a special item designed to channel the power of arcane spells. A sorcerer, warlock, or wizard can use such an item as a spellcasting focus. 10 gp. 1 lb.",
        "Arcane focus — Orb": "An arcane focus designed to channel the power of arcane spells. 20 gp. 3 lb.",
        "Arcane focus — Rod": "An arcane focus designed to channel arcane spells. 10 gp. 2 lb.",
        "Arcane focus — Staff": "An arcane focus designed to channel arcane spells (also counts as a quarterstaff). 5 gp. 4 lb.",
        "Arcane focus — Wand": "An arcane focus designed to channel arcane spells. 10 gp. 1 lb.",
        "Arrows (20)": "Ammunition for shortbows and longbows. 1 gp. 1 lb. per 20.",
        "Blowgun needles (50)": "Ammunition for blowguns. 1 gp. 1 lb. per 50.",
        "Crossbow bolts (20)": "Ammunition for crossbows. 1 gp. 1.5 lb. per 20.",
        "Sling bullets (20)": "Ammunition for slings. 4 cp. 1.5 lb. per 20.",
        "Backpack": "A sturdy leather backpack that can hold up to 30 pounds of gear. 2 gp. 5 lb.",
        "Ball bearings (bag of 1,000)": "As an action, spill these tiny metal balls to cover a 10-foot square area. Any creature moving across the area must succeed on a DC 10 Dexterity saving throw or fall prone. 1 gp. 2 lb.",
        "Barrel": "A wooden barrel that can hold 40 gallons of liquid or 4 cubic feet of solid goods. 2 gp. 70 lb.",
        "Basket": "A woven container for carrying goods. 4 sp. 2 lb.",
        "Bedroll": "A cloth bedroll and blanket for sleeping. 1 gp. 7 lb.",
        "Bell": "A small hand bell that rings clearly. 1 gp.",
        "Blanket": "A warm woolen blanket. 5 sp. 3 lb.",
        "Block and tackle": "A set of pulleys with a cable threaded through them. Lets you hoist up to four times the normal weight you could lift. 1 gp. 5 lb.",
        "Book": "A leather-bound tome containing lore, records, or stories — typically worth 25 gp depending on content. 25 gp. 5 lb.",
        "Bottle, glass": "A glass bottle that holds 1½ pints of liquid. 2 gp. 2 lb.",
        "Bucket": "A wooden or metal bucket holding 3 gallons. 5 cp. 2 lb.",
        "Caltrops (bag of 20)": "As an action, spread a bag of caltrops to cover a 5-foot square. A creature entering the area must succeed on a DC 15 Dexterity saving throw or stop moving and take 1 piercing damage. 1 gp. 2 lb.",
        "Candle": "Provides dim light in a 5-foot radius for 1 hour. 1 cp.",
        "Case, crossbow bolt": "A wooden case that holds up to 20 crossbow bolts. 1 gp. 1 lb.",
        "Case, map or scroll": "A cylindrical leather case that protects maps, scrolls, or documents from water and wear. 1 gp. 1 lb.",
        "Chain (10 feet)": "A 10-foot iron chain. Has 10 hit points and can be burst with a DC 20 Strength check. 5 gp. 10 lb.",
        "Chalk (1 piece)": "A piece of white chalk for marking surfaces. 1 cp.",
        "Chest": "A wooden chest that holds 12 cubic feet or 300 pounds of gear. Has a lock (DC 15 to pick). 5 gp. 25 lb.",
        "Climber's kit": "Includes special pitons, boot tips, gloves, and a harness. Gives you a climbing speed equal to your walking speed for 10 minutes, once per short rest. While using it, you can't fall more than 25 feet. 25 gp. 12 lb.",
        "Clothes, common": "Simple, durable work clothes — tunic, trousers, boots. 5 sp. 3 lb.",
        "Clothes, costume": "An outfit for a specific role or costume. 5 gp. 4 lb.",
        "Clothes, fine": "High-quality fabric and tailoring suitable for nobility. 15 gp. 6 lb.",
        "Clothes, traveler's": "Sturdy clothes designed for long journeys, with reinforced stitching and multiple pockets. 2 gp. 4 lb.",
        "Component pouch": "A small watertight leather belt pouch with compartments to hold all the material components and other special items you need to cast your spells (except for those with a specific cost). 25 gp. 2 lb.",
        "Crowbar": "Using a crowbar grants advantage on Strength checks where leverage can be applied. 2 gp. 5 lb.",
        "Druidic focus — Sprig of mistletoe": "A druidic focus used to channel nature magic. Druids can use it as a spellcasting focus. 1 gp.",
        "Druidic focus — Totem": "A druidic focus incorporating feathers, fur, bones, and teeth from sacred animals. 1 gp.",
        "Druidic focus — Wooden staff": "A druidic focus (also counts as a quarterstaff). 5 gp. 4 lb.",
        "Druidic focus — Yew wand": "A druidic focus carved from yew wood. 10 gp. 1 lb.",
        "Fishing tackle": "Includes a wooden rod, silken line, corkwood bobbers, steel hooks, lead sinkers, velvet lures, and narrow netting. 1 gp. 4 lb.",
        "Flask or tankard": "A metal container that holds 1 pint of liquid. 2 cp. 1 lb.",
        "Grappling hook": "A metal hook attached to a rope. Throw with a DC 10 Strength (Athletics) check to secure it. 2 gp. 4 lb.",
        "Hammer": "A one-handed metal hammer for driving pitons and nails. 1 gp. 3 lb.",
        "Hammer, sledge": "A two-handed heavy hammer for demolition. 2 gp. 10 lb.",
        "Healer's kit": "A leather pouch containing bandages, salves, and splints. Has 10 uses. As an action, expend one use to stabilize a dying creature without needing a Wisdom (Medicine) check. 5 gp. 3 lb.",
        "Holy symbol — Amulet": "A holy symbol representing a deity or pantheon. A cleric or paladin can use it as a spellcasting focus. 5 gp. 1 lb.",
        "Holy symbol — Emblem": "A holy symbol on a shield or tabard. 5 gp.",
        "Holy symbol — Reliquary": "A holy symbol containing a sacred relic. 5 gp. 2 lb.",
        "Holy water (flask)": "As an action, splash holy water onto a creature within 5 feet, or throw the flask up to 20 feet. On a hit against a fiend or undead, the target takes 2d6 radiant damage. A cleric or paladin can create holy water with a 1-hour ritual using 25 gp of powdered silver. 25 gp. 1 lb.",
        "Hourglass": "A sand-filled glass timer for measuring time in 1-hour increments. 25 gp. 1 lb.",
        "Hunting trap": "A saw-toothed steel trap. As an action, set it. A creature stepping on it must succeed on a DC 13 Dexterity saving throw or take 1d4 piercing damage and stop moving. The creature can be freed with a DC 13 Strength check. 5 gp. 25 lb.",
        "Ink (1 ounce bottle)": "Black ink for writing. 10 gp.",
        "Ink pen": "A writing implement, typically a quill. 2 cp.",
        "Jug or pitcher": "A ceramic container holding 1 gallon of liquid. 2 cp. 4 lb.",
        "Ladder (10-foot)": "A 10-foot wooden ladder. 1 sp. 25 lb.",
        "Lamp": "A hooded lantern casting bright light in a 30-foot radius and dim light for an additional 30 feet for 6 hours on a flask of oil. 5 sp. 2 lb.",
        "Lantern, bullseye": "Casts bright light in a 60-foot cone and dim light for an additional 60 feet for 6 hours on a flask of oil. 10 gp. 2 lb.",
        "Lantern, hooded": "Casts bright light in a 30-foot radius and dim light for an additional 30 feet for 6 hours on a flask of oil. Lowering the hood reduces light to dim in a 5-foot radius. 5 gp. 2 lb.",
        "Lock": "A simple lock with a key. DC 15 Dexterity check with thieves' tools to pick. 10 gp. 1 lb.",
        "Magnifying glass": "A lens for inspecting small details. Grants advantage on ability checks to appraise or inspect small or detailed items. Also useful for starting fires in sunlight. 100 gp.",
        "Manacles": "Metal restraints that bind a Small or Medium creature. Escaping requires a DC 20 Dexterity check; breaking them a DC 20 Strength check. 2 gp. 6 lb.",
        "Mess kit": "A tin box containing a cup, simple cutlery, and a plate. 2 sp. 1 lb.",
        "Mirror, steel": "A polished steel mirror for signaling or grooming. 5 gp. 0.5 lb.",
        "Oil (flask)": "A flask of lantern oil. As an action, splash oil on a creature or pour on the ground (covers a 5-foot square). If lit, burns for 2 rounds dealing 5 fire damage per round. 1 sp. 1 lb.",
        "Paper (one sheet)": "A single sheet of parchment or vellum for writing. 2 sp.",
        "Parchment (one sheet)": "A single sheet of animal-skin parchment. 1 sp.",
        "Perfume (vial)": "A vial of fragrant perfume. 5 gp.",
        "Pick, miner's": "A mining pick for breaking stone. 2 gp. 10 lb.",
        "Piton": "A metal spike driven into rock for climbing. Holds up to 500 lb. 5 cp. 0.25 lb.",
        "Poison, basic (vial)": "Apply to a weapon or up to three pieces of ammunition. The poison retains potency for 1 minute. A creature hit must succeed on a DC 10 Constitution saving throw or take 1d4 poison damage. 100 gp.",
        "Pole (10-foot)": "A 10-foot wooden pole. Useful for prodding suspicious objects from a safe distance. 5 cp. 7 lb.",
        "Pot, iron": "An iron cooking pot holding 1 gallon. 2 gp. 10 lb.",
        "Potion of healing": "Drink this potion as an action to regain 2d4+2 hit points. 50 gp. 0.5 lb.",
        "Pouch": "A small leather or cloth pouch that holds 6 pounds or ⅕ cubic foot of gear. 5 sp. 1 lb.",
        "Quiver": "A leather quiver holding up to 20 arrows or bolts. 1 gp. 1 lb.",
        "Ram, portable": "A portable battering ram. Gives you a +4 bonus on Strength checks to break open doors, and advantage if a second character helps. 4 gp. 35 lb.",
        "Rations (1 day)": "Dried and preserved food suitable for travel — jerky, dried fruit, hardtack, and nuts. 5 sp. 2 lb.",
        "Robes": "Floor-length cloth robes worn by clergy, scholars, and wizards. 1 gp. 4 lb.",
        "Rope, hempen (50 feet)": "50 feet of hempen rope. Has 2 hit points and can be burst with a DC 17 Strength check. 1 gp. 10 lb.",
        "Rope, silk (50 feet)": "50 feet of silk rope. Has 2 hit points and can be burst with a DC 17 Strength check. Lighter and stronger than hempen. 10 gp. 5 lb.",
        "Sack": "A cloth sack holding 30 pounds or 1 cubic foot of gear. 1 cp. 0.5 lb.",
        "Scale, merchant's": "A balance scale with weights for measuring goods. 5 gp. 3 lb.",
        "Sealing wax": "A stick of wax for sealing letters and documents. 5 sp.",
        "Shovel": "A digging tool. 2 gp. 5 lb.",
        "Signal whistle": "A shrill whistle audible up to 600 feet away. 5 cp.",
        "Signet ring": "A ring bearing an engraved family or guild seal for stamping wax. 5 gp.",
        "Soap": "A bar of soap for washing. 2 cp.",
        "Spellbook": "A leather-bound tome essential for wizards to record spells. Contains 100 blank pages. 50 gp. 3 lb.",
        "Spikes, iron (10)": "A set of 10 iron spikes used to wedge doors shut or anchor ropes for climbing. 1 gp. 5 lb.",
        "Spyglass": "A telescope that magnifies distant objects to twice their apparent size. 1,000 gp. 1 lb.",
        "Tent, two-person": "A simple canvas tent for two people. 2 gp. 20 lb.",
        "Tinderbox": "A small box containing flint, fire steel, and tinder. Using it to start a fire requires an action. 5 sp. 1 lb.",
        "Torch": "A torch burns for 1 hour, providing bright light in a 20-foot radius and dim light for an additional 20 feet. Make a melee attack with a lit torch to deal 1 fire damage. 1 cp. 1 lb.",
        "Vial": "A small glass vial holding up to 4 ounces of liquid. 1 gp.",
        "Waterskin": "A leather waterskin holding 4 pints of liquid. 2 sp. 5 lb. (full).",
        "Whetstone": "A stone for sharpening blades. 1 cp. 1 lb.",
    }

    name = item.get("name", "")
    if name in gear_descriptions:
        return gear_descriptions[name]

    # Fallback: use gear category + cost/weight
    subcat = (item.get("gear_category") or {}).get("name", "")
    cost = item.get("cost", {})
    cost_str = f"{cost.get('quantity','?')} {cost.get('unit','gp')}"
    weight = item.get("weight", "?")
    if subcat:
        return f"{subcat}. {weight} lb. {cost_str}."
    return f"{weight} lb. {cost_str}."


def _build_item_type(item: dict) -> str:
    """Get a display type string for an item."""
    cat = item.get("equipment_category", {}).get("name", "")
    if cat == "Weapon":
        wcat = item.get("weapon_category", "Weapon")
        wrange = item.get("weapon_range", "Melee")
        return f"{wcat} {wrange} Weapon"
    if cat == "Armor":
        return item.get("armor_category", cat)
    subcat = (item.get("gear_category") or {}).get("name", "")
    if subcat:
        return subcat
    return cat or "Equipment"


# Build unified item index (equipment + magic items)
ITEM_INDEX: dict[str, dict] = {}
for item in SRD_EQUIPMENT:
    name = item.get("name", "")
    if name:
        key = name.lower()
        cost = item.get("cost", {})
        ITEM_INDEX[key] = {
            "name": name,
            "type": _build_item_type(item),
            "description": _build_item_description(item),
            "cost": f"{cost.get('quantity', '?')} {cost.get('unit', 'gp')}",
            "weight": item.get("weight", None),
            "rarity": "",
            "source": "PHB 2014",
        }

for item in SRD_MAGIC_ITEMS:
    name = item.get("name", "")
    if name:
        key = name.lower()
        rarity = item.get("rarity", {}).get("name", "")
        desc_list = item.get("desc", [])
        desc = " ".join(desc_list) if desc_list else ""
        ITEM_INDEX[key] = {
            "name": name,
            "type": "Magic Item" if rarity else "Magic Item",
            "description": desc,
            "cost": "—",
            "weight": None,
            "rarity": rarity,
            "source": "DMG 2014",
        }

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
            class_levels TEXT DEFAULT '{}',
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
        CREATE TABLE IF NOT EXISTS dm_npcs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            race TEXT NOT NULL DEFAULT 'Human',
            class_name TEXT DEFAULT '',
            subclass TEXT DEFAULT '',
            level INTEGER DEFAULT 1,
            is_enemy INTEGER DEFAULT 0,
            is_party_npc INTEGER DEFAULT 0,
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
            skills TEXT DEFAULT '[]',
            features TEXT DEFAULT '[]',
            inventory TEXT DEFAULT '[]',
            notes TEXT DEFAULT '',
            portrait_url TEXT DEFAULT '',
            alignment TEXT DEFAULT 'True Neutral',
            role TEXT DEFAULT 'NPC',
            faction TEXT DEFAULT '',
            xp_reward INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS dm_encounters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            location TEXT DEFAULT '',
            environment TEXT DEFAULT '',
            difficulty TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'planned',
            xp_total INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS dm_encounter_npcs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            encounter_id INTEGER NOT NULL,
            npc_id INTEGER NOT NULL,
            initiative INTEGER DEFAULT 0,
            hp_current INTEGER DEFAULT 0,
            hp_max INTEGER DEFAULT 0,
            ac INTEGER DEFAULT 10,
            defeated INTEGER DEFAULT 0,
            spell_slots_used TEXT DEFAULT '{}',
            notes TEXT DEFAULT '',
            FOREIGN KEY (encounter_id) REFERENCES dm_encounters(id) ON DELETE CASCADE,
            FOREIGN KEY (npc_id) REFERENCES dm_npcs(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS dm_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            party_level INTEGER DEFAULT 1,
            party_size INTEGER DEFAULT 4,
            notes TEXT DEFAULT '',
            session_notes TEXT DEFAULT '',
            quests TEXT DEFAULT '[]',
            locations TEXT DEFAULT '[]',
            characters TEXT DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS dm_campaign_characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL,
            status TEXT DEFAULT 'active',
            notes TEXT DEFAULT '',
            FOREIGN KEY (campaign_id) REFERENCES dm_campaigns(id) ON DELETE CASCADE,
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
                          ("condition_immunities","TEXT DEFAULT '[]'"),
                          ("background_data","TEXT DEFAULT ''"),
                          ("spell_slots_used","TEXT DEFAULT '{}'")]:
        try:
            db.execute(f"ALTER TABLE characters ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass
    # Migration: dm_encounter_npcs new columns
    for col, coltype in [("defeated", "INTEGER DEFAULT 0"),
                          ("spell_slots_used", "TEXT DEFAULT '{}'")]:
        try:
            db.execute(f"ALTER TABLE dm_encounter_npcs ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass
    # Migration: dm_campaigns characters column
    try:
        db.execute("ALTER TABLE dm_campaigns ADD COLUMN characters TEXT DEFAULT '[]'")
    except sqlite3.OperationalError:
        pass
    # Migration: class_levels for multiclass support
    try:
        db.execute("ALTER TABLE characters ADD COLUMN class_levels TEXT DEFAULT '{}'")
    except sqlite3.OperationalError:
        pass
    # Backfill: populate class_levels from class_name + level for existing characters
    db.execute("UPDATE characters SET class_levels = json_object(class_name, level) WHERE class_levels = '{}' OR class_levels IS NULL OR class_levels = ''")
    # Migration: character_relationships for History & Relationships tab
    db.execute("""
        CREATE TABLE IF NOT EXISTS character_relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            character_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            relationship_type TEXT DEFAULT 'ally',
            description TEXT DEFAULT '',
            prompt TEXT DEFAULT '',
            npc_data TEXT DEFAULT '{}',
            ai_generated INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
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
    "Human": {"subraces": ["Variant Human"], "asi": {"strength": 1, "dexterity": 1, "constitution": 1, "intelligence": 1, "wisdom": 1, "charisma": 1}, "speed": 30, "darkvision": 0, "languages": ["Common"], "traits": [], "desc": "The most adaptable and ambitious of the common races. Humans gain +1 to all six ability scores, learn quickly, and are found in every corner of the world.", "subrace_descs": {"Variant Human": "+1 to two abilities, one feat, one extra skill proficiency. The customizable human — trades the all-around +1s for focused specialization."}},
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
    "Barbarian": {"hd": 12, "skills": ["Animal Handling","Athletics","Intimidation","Nature","Perception","Survival"], "skill_count": 2, "saves": ["strength","constitution"], "subclasses": ["Path of the Berserker","Path of the Totem Warrior"], "desc": "A fierce warrior of primitive background who can enter a battle rage. Barbarians are durable melee combatants who shrug off damage and hit hard.", "subclass_descs": {"Path of the Berserker": "The rage becomes frenzy — a bonus action attack each turn while raging, at the cost of exhaustion afterward. Pure offensive power.", "Path of the Totem Warrior": "Spirit totems (Bear, Eagle, Wolf) grant damage resistances, mobility, and ally buffs. Highly customizable natural warrior."}, "weapons": "Simple weapons, Martial weapons", "armor": "Light armor, Medium armor, Shields", "tools": ""},
    "Bard": {"hd": 8, "skills": "all", "skill_count": 3, "saves": ["dexterity","charisma"], "subclasses": ["College of Lore","College of Valor"], "desc": "A master of words, music, and magic. Bards inspire allies, manipulate minds, and have access to a versatile spell list — they can fill nearly any role in a party.", "subclass_descs": {"College of Lore": "Bonus skill proficiencies and Cutting Words to undermine enemy attacks. Steals spells from any class via Additional Magical Secrets.", "College of Valor": "Combat Inspiration lets allies add Bardic Inspiration to damage or AC. Gains Extra Attack and medium armor — the martial bard."}, "weapons": "Simple weapons, Hand crossbows, Longswords, Rapiers, Shortswords", "armor": "Light armor", "tools": "Three musical instruments of your choice"},
    "Cleric": {"hd": 8, "skills": ["History","Insight","Medicine","Persuasion","Religion"], "skill_count": 2, "saves": ["wisdom","charisma"], "subclasses": ["Knowledge Domain","Life Domain","Light Domain","Nature Domain","Tempest Domain","Trickery Domain","War Domain"], "desc": "A divine agent wielding the power of a god. Clerics are versatile spellcasters who can heal, protect, and smite — their domain choice dramatically shapes their role.", "subclass_descs": {"Knowledge Domain": "Mind-reading and skill expertise in two knowledge skills. The scholar-cleric who uncovers secrets.", "Life Domain": "Bonus healing on every spell, heavy armor proficiency. The quintessential healer.", "Light Domain": "Warding Flare imposes disadvantage on attackers. Gains scorching ray, fireball, and other blasting spells.", "Nature Domain": "A druid cantrip, charm animals/plants, and elemental damage resistance. Protector of the wilds.", "Tempest Domain": "Destructive Wrath maximizes lightning/thunder damage. Heavy armor, martial weapons, and storm rebuke.", "Trickery Domain": "Invoke Duplicity creates an illusory double. Domain spells full of illusion and deception magic.", "War Domain": "War Priest grants bonus action attacks. Channel Divinity: Guided Strike adds +10 to an attack roll."}, "weapons": "Simple weapons", "armor": "Light armor, Medium armor, Shields", "tools": ""},
    "Druid": {"hd": 8, "skills": ["Arcana","Animal Handling","Insight","Medicine","Nature","Perception","Religion","Survival"], "skill_count": 2, "saves": ["intelligence","wisdom"], "subclasses": ["Circle of the Land","Circle of the Moon"], "desc": "A nature priest who draws power from the natural world. Druids are full spellcasters who can turn into animals (Wild Shape) and wield primal magic tied to the elements.", "subclass_descs": {"Circle of the Land": "Bonus cantrip, Natural Recovery (regain spell slots on short rest), and terrain-based circle spells. The caster druid.", "Circle of the Moon": "Combat Wild Shape as a bonus action with higher-CR beasts. Can expend spell slots to self-heal while transformed. The shapeshifter."}, "weapons": "Clubs, Daggers, Darts, Javelins, Maces, Quarterstaffs, Scimitars, Sickles, Slings, Spears", "armor": "Light armor, Medium armor, Shields (druids will not wear metal armor or shields)", "tools": "Herbalism kit"},
    "Fighter": {"hd": 10, "skills": ["Acrobatics","Animal Handling","Athletics","History","Insight","Intimidation","Perception","Survival"], "skill_count": 2, "saves": ["strength","constitution"], "subclasses": ["Champion","Battle Master","Eldritch Knight"], "desc": "A master of martial combat, skilled with a variety of weapons and armor. Fighters are versatile warriors who can specialize in any fighting style and get more attacks than any other class.", "subclass_descs": {"Champion": "Improved Critical (crits on 19–20, later 18–20). Remarkable Athlete adds half-proficiency to physical checks. Simple and deadly.", "Battle Master": "Combat Superiority grants maneuvers (Trip, Riposte, Precision Attack). The tactical fighter who controls the battlefield.", "Eldritch Knight": "Wizard spellcasting (abjuration/evocation) and Weapon Bond (can't be disarmed). Summon weapons across planes."}, "weapons": "Simple weapons, Martial weapons", "armor": "All armor, Shields", "tools": ""},
    "Monk": {"hd": 8, "skills": ["Acrobatics","Athletics","History","Insight","Religion","Stealth"], "skill_count": 2, "saves": ["strength","dexterity"], "subclasses": ["Way of the Open Hand","Way of Shadow","Way of the Four Elements"], "desc": "A disciplined martial artist who channels inner energy (ki). Monks fight unarmed, move with supernatural speed, and can stun, deflect, and outmaneuver foes.", "subclass_descs": {"Way of the Open Hand": "Open Hand Technique knocks foes prone or pushes them back on Flurry of Blows. Gains self-healing and a sanctuary effect.", "Way of Shadow": "Shadow Arts casts darkness, darkvision, pass without trace, and silence using ki. Shadow Step teleports between shadows for advantage.", "Way of the Four Elements": "Elemental Disciplines channel ki into spell-like effects — water whip, fireball, flight. The elemental bender."}, "weapons": "Simple weapons, Shortswords", "armor": "", "tools": "One type of artisan's tools or one musical instrument"},
    "Paladin": {"hd": 10, "skills": ["Athletics","Insight","Intimidation","Medicine","Persuasion","Religion"], "skill_count": 2, "saves": ["wisdom","charisma"], "subclasses": ["Oath of Devotion","Oath of the Ancients","Oath of Vengeance"], "desc": "A holy warrior bound by a sacred oath. Paladins combine martial prowess with divine magic — they smite enemies, heal allies, and project protective auras.", "subclass_descs": {"Oath of Devotion": "Sacred Weapon adds CHA to attack rolls. Aura of Devotion prevents charm. The classic holy knight.", "Oath of the Ancients": "Nature's Wrath restrains foes with vines. Aura of Warding grants resistance to all spell damage. The green knight.", "Oath of Vengeance": "Vow of Enmity gives advantage against one foe. Relentless Avenger lets you move after opportunity attacks. The relentless pursuer."}, "weapons": "Simple weapons, Martial weapons", "armor": "All armor, Shields", "tools": ""},
    "Ranger": {"hd": 10, "skills": ["Animal Handling","Athletics","Insight","Investigation","Nature","Perception","Stealth","Survival"], "skill_count": 3, "saves": ["strength","dexterity"], "subclasses": ["Hunter","Beast Master"], "desc": "A wilderness scout and skilled tracker. Rangers blend martial ability with nature magic — they excel at exploration, favored enemy tactics, and ranged combat.", "subclass_descs": {"Hunter": "Choose from Colossus Slayer (extra damage to wounded foes), Horde Breaker (extra attack), or Giant Killer. Versatile combat specialist.", "Beast Master": "An animal companion fights alongside you, acting on your turn. Share spells and coordinate attacks with your bonded beast."}, "weapons": "Simple weapons, Martial weapons", "armor": "Light armor, Medium armor, Shields", "tools": ""},
    "Rogue": {"hd": 8, "skills": ["Acrobatics","Athletics","Deception","Insight","Intimidation","Investigation","Perception","Performance","Persuasion","Sleight of Hand","Stealth"], "skill_count": 4, "saves": ["dexterity","intelligence"], "subclasses": ["Thief","Assassin","Arcane Trickster"], "desc": "A stealthy trickster who exploits enemy weaknesses. Rogues deal massive Sneak Attack damage, have more skill proficiencies than any class, and excel at avoiding danger.", "subclass_descs": {"Thief": "Fast Hands lets you use items, pick pockets, and disarm traps as a bonus action. Second-Story Work adds climbing speed and jump distance.", "Assassin": "Assassinate auto-crits surprised creatures. Infiltration Expertise lets you create false identities. The lethal first-strike rogue.", "Arcane Trickster": "Wizard spellcasting (illusion/enchantment) plus invisible Mage Hand Legerdemain. Can steal spells and impose disadvantage on saves from stealth."}, "weapons": "Simple weapons, Hand crossbows, Longswords, Rapiers, Shortswords", "armor": "Light armor", "tools": "Thieves' tools"},
    "Sorcerer": {"hd": 6, "skills": ["Arcana","Deception","Insight","Intimidation","Persuasion","Religion"], "skill_count": 2, "saves": ["constitution","charisma"], "subclasses": ["Draconic Bloodline","Wild Magic"], "desc": "A spellcaster born with innate magic in their blood. Sorcerers use Metamagic to bend spells in ways no other class can — twin spells, quicken them, or make them subtle.", "subclass_descs": {"Draconic Bloodline": "Draconic Resilience boosts HP and grants natural AC 13. Elemental Affinity adds CHA to damage of your chosen element. The durable blaster.", "Wild Magic": "Tides of Chaos grants advantage, but may trigger Wild Magic Surges — random effects from a d100 table. Unpredictable and explosive."}, "weapons": "Daggers, Darts, Slings, Quarterstaffs, Light crossbows", "armor": "", "tools": ""},
    "Warlock": {"hd": 8, "skills": ["Arcana","Deception","History","Intimidation","Investigation","Nature","Religion"], "skill_count": 2, "saves": ["wisdom","charisma"], "subclasses": ["The Archfey","The Fiend","The Great Old One"], "desc": "A seeker of forbidden knowledge who made a pact with an otherworldly patron. Warlocks use Pact Magic — a few spell slots that recharge on short rests — plus Eldritch Invocations for unique abilities.", "subclass_descs": {"The Archfey": "Fey Presence charms or frightens nearby foes. Misty Escape lets you teleport and turn invisible when hit. The trickster patron.", "The Fiend": "Dark One's Blessing grants temp HP when you kill. Hurl Through Hell sends a target on a short, devastating trip to the lower planes.", "The Great Old One": "Awakened Mind grants telepathy. Entropic Ward imposes disadvantage on attackers. Create Thrall makes a permanent charmed servant."}, "weapons": "Simple weapons", "armor": "Light armor", "tools": ""},
    "Wizard": {"hd": 6, "skills": ["Arcana","History","Insight","Investigation","Medicine","Religion"], "skill_count": 2, "saves": ["intelligence","wisdom"], "subclasses": ["School of Abjuration","School of Conjuration","School of Divination","School of Enchantment","School of Evocation","School of Illusion","School of Necromancy","School of Transmutation"], "desc": "A scholarly spellcaster who learns magic through study. Wizards have the largest spell list and can learn new spells from scrolls — they prepare from a spellbook and can ritual cast without preparation.", "subclass_descs": {"School of Abjuration": "Arcane Ward absorbs damage as a magical HP buffer. Spell Resistance grants advantage on saves against spells. The defensive wizard.", "School of Conjuration": "Minor Conjuration creates nonmagical objects. Benign Transposition teleports you and swaps places with an ally. The summoner.", "School of Divination": "Portent lets you replace any d20 roll with one of two pre-rolled results. The fate manipulator.", "School of Enchantment": "Hypnotic Gaze incapacitates a creature. Instinctive Charm redirects attacks to other targets. The mind controller.", "School of Evocation": "Sculpt Spells protects allies from your area effects. Potent Cantrip deals half damage even on saves. The blaster.", "School of Illusion": "Improved Minor Illusion creates sound and image simultaneously. Malleable Illusions lets you reshape ongoing illusions.", "School of Necromancy": "Grim Harvest heals you when you kill with spells. Undead Thralls creates stronger undead and lets you raise more of them.", "School of Transmutation": "Minor Alchemy temporarily changes materials. Transmuter's Stone grants a buff (darkvision, speed, resistance, or CON saves)."}, "weapons": "Daggers, Darts, Slings, Quarterstaffs, Light crossbows", "armor": "", "tools": ""},
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
BACKGROUND_INFO = {
    "Acolyte":       "You served in a temple. Skill Proficiencies: Insight, Religion. Languages: Two of your choice. Equipment: Holy symbol, prayer book, 5 sticks of incense, vestments, common clothes, 15 gp. Feature: Shelter of the Faithful.",
    "Charlatan":     "You've made a living by your wits. Skill Proficiencies: Deception, Sleight of Hand. Tool Proficiencies: Disguise kit, forgery kit. Equipment: Fine clothes, disguise kit, tools of the con, 15 gp. Feature: False Identity.",
    "Criminal":      "You are an experienced criminal. Skill Proficiencies: Deception, Stealth. Tool Proficiencies: One gaming set, thieves' tools. Equipment: Crowbar, dark clothes, 15 gp. Feature: Criminal Contact.",
    "Entertainer":   "You thrive before an audience. Skill Proficiencies: Acrobatics, Performance. Tool Proficiencies: Disguise kit, one musical instrument. Equipment: Musical instrument, costume, 15 gp. Feature: By Popular Demand.",
    "Folk Hero":     "You come from a humble social rank. Skill Proficiencies: Animal Handling, Survival. Tool Proficiencies: One artisan's tools, land vehicles. Equipment: Artisan's tools, shovel, iron pot, common clothes, 10 gp. Feature: Rustic Hospitality.",
    "Guild Artisan": "You are a member of an artisan's guild. Skill Proficiencies: Insight, Persuasion. Tool Proficiencies: One artisan's tools. Languages: One of your choice. Equipment: Artisan's tools, letter of introduction, traveler's clothes, 15 gp. Feature: Guild Membership.",
    "Hermit":        "You lived in seclusion. Skill Proficiencies: Medicine, Religion. Tool Proficiencies: Herbalism kit. Equipment: Scroll case of notes, winter blanket, common clothes, herbalism kit, 5 gp. Feature: Discovery.",
    "Noble":         "You were born into wealth and power. Skill Proficiencies: History, Persuasion. Tool Proficiencies: One gaming set. Languages: One of your choice. Equipment: Fine clothes, signet ring, scroll of pedigree, 25 gp. Feature: Position of Privilege.",
    "Outlander":     "You grew up in the wilds. Skill Proficiencies: Athletics, Survival. Tool Proficiencies: One musical instrument. Languages: One of your choice. Equipment: Staff, hunting trap, trophy, traveler's clothes, 10 gp. Feature: Wanderer.",
    "Sage":          "You spent years learning lore. Skill Proficiencies: Arcana, History. Languages: Two of your choice. Equipment: Black ink, quill, small knife, letter from dead colleague, common clothes, 10 gp. Feature: Researcher.",
    "Sailor":        "You have sailed the high seas. Skill Proficiencies: Athletics, Perception. Tool Proficiencies: Navigator's tools, water vehicles. Equipment: Belaying pin, 50 ft silk rope, lucky charm, common clothes, 10 gp. Feature: Ship's Passage.",
    "Soldier":       "You served in a military force. Skill Proficiencies: Athletics, Intimidation. Tool Proficiencies: One gaming set, land vehicles. Equipment: Insignia of rank, trophy, bone dice, common clothes, 10 gp. Feature: Military Rank.",
    "Urchin":        "You grew up on the streets alone. Skill Proficiencies: Sleight of Hand, Stealth. Tool Proficiencies: Disguise kit, thieves' tools. Equipment: Small knife, city map, pet mouse, token of parents, common clothes, 10 gp. Feature: City Secrets.",
    "Custom":        "Define your own background. Equipment: 3 useful items of your choice, traveler's clothes, 10 gp. Feature: Your own unique story.",
}
ALIGNMENTS = ["Lawful Good","Neutral Good","Chaotic Good","Lawful Neutral","True Neutral","Chaotic Neutral","Lawful Evil","Neutral Evil","Chaotic Evil"]

# ── SRD Weapons (PHB p.149) ─────────────────────────────────────────────────
WEAPONS = {
    # Simple Melee
    "club":            {"damage":"1d4","type":"bludgeoning","props":["light"],"category":"simple melee"},
    "dagger":          {"damage":"1d4","type":"piercing","props":["finesse","light","thrown (20/60)"],"category":"simple melee"},
    "greatclub":       {"damage":"1d8","type":"bludgeoning","props":["two-handed"],"category":"simple melee"},
    "handaxe":         {"damage":"1d6","type":"slashing","props":["light","thrown (20/60)"],"category":"simple melee"},
    "javelin":         {"damage":"1d6","type":"piercing","props":["thrown (30/120)"],"category":"simple melee"},
    "light hammer":    {"damage":"1d4","type":"bludgeoning","props":["light","thrown (20/60)"],"category":"simple melee"},
    "mace":            {"damage":"1d6","type":"bludgeoning","props":[],"category":"simple melee"},
    "quarterstaff":    {"damage":"1d6","type":"bludgeoning","props":["versatile (1d8)"],"category":"simple melee"},
    "sickle":          {"damage":"1d4","type":"slashing","props":["light"],"category":"simple melee"},
    "spear":           {"damage":"1d6","type":"piercing","props":["thrown (20/60)","versatile (1d8)"],"category":"simple melee"},
    # Simple Ranged
    "crossbow, light": {"damage":"1d8","type":"piercing","props":["ammunition (80/320)","loading","two-handed"],"category":"simple ranged"},
    "dart":            {"damage":"1d4","type":"piercing","props":["finesse","thrown (20/60)"],"category":"simple ranged"},
    "shortbow":        {"damage":"1d6","type":"piercing","props":["ammunition (80/320)","two-handed"],"category":"simple ranged"},
    "sling":           {"damage":"1d4","type":"bludgeoning","props":["ammunition (30/120)"],"category":"simple ranged"},
    # Martial Melee
    "battleaxe":       {"damage":"1d8","type":"slashing","props":["versatile (1d10)"],"category":"martial melee"},
    "flail":           {"damage":"1d8","type":"bludgeoning","props":[],"category":"martial melee"},
    "glaive":          {"damage":"1d10","type":"slashing","props":["heavy","reach","two-handed"],"category":"martial melee"},
    "greataxe":        {"damage":"1d12","type":"slashing","props":["heavy","two-handed"],"category":"martial melee"},
    "greatsword":      {"damage":"2d6","type":"slashing","props":["heavy","two-handed"],"category":"martial melee"},
    "halberd":         {"damage":"1d10","type":"slashing","props":["heavy","reach","two-handed"],"category":"martial melee"},
    "lance":           {"damage":"1d12","type":"piercing","props":["reach","special"],"category":"martial melee"},
    "longsword":       {"damage":"1d8","type":"slashing","props":["versatile (1d10)"],"category":"martial melee"},
    "maul":            {"damage":"2d6","type":"bludgeoning","props":["heavy","two-handed"],"category":"martial melee"},
    "morningstar":     {"damage":"1d8","type":"piercing","props":[],"category":"martial melee"},
    "pike":            {"damage":"1d10","type":"piercing","props":["heavy","reach","two-handed"],"category":"martial melee"},
    "rapier":          {"damage":"1d8","type":"piercing","props":["finesse"],"category":"martial melee"},
    "scimitar":        {"damage":"1d6","type":"slashing","props":["finesse","light"],"category":"martial melee"},
    "shortsword":      {"damage":"1d6","type":"piercing","props":["finesse","light"],"category":"martial melee"},
    "trident":         {"damage":"1d6","type":"piercing","props":["thrown (20/60)","versatile (1d8)"],"category":"martial melee"},
    "war pick":        {"damage":"1d8","type":"piercing","props":[],"category":"martial melee"},
    "warhammer":       {"damage":"1d8","type":"bludgeoning","props":["versatile (1d10)"],"category":"martial melee"},
    "whip":            {"damage":"1d4","type":"slashing","props":["finesse","reach"],"category":"martial melee"},
    # Martial Ranged
    "blowgun":         {"damage":"1","type":"piercing","props":["ammunition (25/100)","loading"],"category":"martial ranged"},
    "crossbow, hand":  {"damage":"1d6","type":"piercing","props":["ammunition (30/120)","light","loading"],"category":"martial ranged"},
    "crossbow, heavy": {"damage":"1d10","type":"piercing","props":["ammunition (100/400)","heavy","loading","two-handed"],"category":"martial ranged"},
    "longbow":         {"damage":"1d8","type":"piercing","props":["ammunition (150/600)","heavy","two-handed"],"category":"martial ranged"},
    "net":             {"damage":"—","type":"special","props":["thrown (5/15)","special"],"category":"martial ranged"},
}

def _find_weapon(item_name: str) -> dict | None:
    """Match an inventory item name to a known SRD weapon. Fuzzy match."""
    name = item_name.lower().strip()
    # Strip leading quantity (e.g., "2 Handaxes" → "Handaxes")
    import re
    name = re.sub(r'^\d+\s+', '', name)
    # Strip trailing 's' for plural matching (Handaxes → Handaxe)
    name_singular = name.rstrip('s') if name.endswith('s') and len(name) > 3 else name
    # Direct match
    if name in WEAPONS:
        return WEAPONS[name]
    if name_singular in WEAPONS:
        return WEAPONS[name_singular]
    # Substring match — check if any known weapon name appears in the item
    for wpn_name, wpn_data in WEAPONS.items():
        if wpn_name in name or name in wpn_name or wpn_name in name_singular or name_singular in wpn_name:
            return wpn_data
    # Keyword fallback
    keywords = ["sword","axe","hammer","bow","dagger","mace","spear","flail",
                "rapier","scimitar","glaive","halberd","pike","lance","whip",
                "javelin","crossbow","club","staff","sling","dart","trident"]
    for kw in keywords:
        if kw in name or kw in name_singular:
            generic = {
                "sword": WEAPONS["longsword"], "axe": WEAPONS["battleaxe"],
                "hammer": WEAPONS["warhammer"], "bow": WEAPONS["shortbow"],
                "dagger": WEAPONS["dagger"], "mace": WEAPONS["mace"],
                "spear": WEAPONS["spear"], "flail": WEAPONS["flail"],
                "rapier": WEAPONS["rapier"], "scimitar": WEAPONS["scimitar"],
                "glaive": WEAPONS["glaive"], "halberd": WEAPONS["halberd"],
                "pike": WEAPONS["pike"], "lance": WEAPONS["lance"],
                "whip": WEAPONS["whip"], "javelin": WEAPONS["javelin"],
                "crossbow": WEAPONS["crossbow, light"], "club": WEAPONS["club"],
                "staff": WEAPONS["quarterstaff"], "sling": WEAPONS["sling"],
                "dart": WEAPONS["dart"], "trident": WEAPONS["trident"],
            }
            if kw in generic:
                return generic[kw]
    return None

def _build_attack_for_weapon(item_name: str, weapon_data: dict, abilities: dict, prof_bonus: int) -> dict:
    """Build an attack entry from weapon data and character stats."""
    damage = weapon_data["damage"]
    dmg_type = weapon_data["type"]
    props = weapon_data.get("props", [])

    # Determine attack ability
    is_ranged = "ranged" in weapon_data.get("category", "")
    is_thrown = any("thrown" in p for p in props)
    is_finesse = "finesse" in props

    if is_ranged and not is_thrown:
        ability = "dexterity"
    elif is_finesse:
        # Finesse: use better of STR or DEX
        ability = "dexterity" if abilities.get("dexterity",10) > abilities.get("strength",10) else "strength"
    else:
        ability = "strength"

    ab_mod = (abilities.get(ability, 10) - 10) // 2
    attack_bonus = ab_mod + prof_bonus

    # Damage string
    if damage == "—":
        dmg_str = "Special"
    else:
        dmg_str = f"{damage} + {ab_mod} {dmg_type}"

    # Range
    range_str = None
    for p in props:
        if "thrown" in p:
            range_str = p.replace("thrown ","Thrown ").replace("(","").replace(")","")
        elif "ammunition" in p:
            range_str = p.replace("ammunition ","Range ").replace("(","").replace(")","")

    return {
        "name": item_name,
        "attack_bonus": attack_bonus,
        "damage": dmg_str,
        "range": range_str,
        "properties": [p for p in props if not ("thrown" in p or "ammunition" in p)],
    }

def _build_inventory_attacks(character: dict) -> list:
    """Scan inventory for weapons and build attack entries."""
    abilities = {a: character.get(a, 10) for a in ["strength","dexterity","constitution","intelligence","wisdom","charisma"]}
    prof_bonus = character.get("proficiency_bonus", 2)

    attacks = []
    seen = set()
    inventory = character.get("inventory", []) or []
    for item in inventory:
        name = item.get("name", item) if isinstance(item, dict) else str(item)
        key = name.lower()
        if key in seen:
            continue
        wpn = _find_weapon(name)
        if wpn:
            attacks.append(_build_attack_for_weapon(name, wpn, abilities, prof_bonus))
            seen.add(key)
    return attacks

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
    enriched = enrich_features(build_features, class_name=class_name, level=level, mods={a: (stats[a] - 10) // 2 for a in stats})
    build_attacks = _calculate_attacks(class_name, level,
        {a: (stats[a] - 10) // 2 for a in stats}, prof_bonus,
        data.get("equipment", []))

    # Spell slots from SRD data
    spell_slots = get_spell_slots(class_name, level)

    # Passive perception: 10 + WIS mod + proficiency if Perception proficient
    skills_list = data.get("skills", [])
    wis_mod = (stats["wisdom"] - 10) // 2
    passive = 10 + wis_mod + (prof_bonus if "Perception" in skills_list else 0)

    # Starting proficiencies from class (PHB 2014)
    def _parse_prof_list(raw):
        """Split comma-separated proficiency string into list, filtering empties."""
        if not raw:
            return []
        return [p.strip() for p in raw.split(",") if p.strip()]

    weapon_profs = _parse_prof_list(class_data.get("weapons", ""))
    armor_profs = _parse_prof_list(class_data.get("armor", ""))
    tool_profs = _parse_prof_list(class_data.get("tools", ""))
    save_profs = class_data.get("saves", [])

    # Starting defenses from race (PHB 2014)
    # Damage resistance: Dwarf→Poison, Tiefling→Fire
    # Condition immunity: Elf/Half-Elf→Sleep (magical)
    damage_resist = []
    condition_immune = []
    damage_immune = []
    damage_vuln = []
    race_lower = race_name.lower()
    if 'dwarf' in race_lower:
        damage_resist = ['Poison']
    elif 'tiefling' in race_lower:
        damage_resist = ['Fire']
    elif 'elf' in race_lower or 'half-elf' in race_lower:
        condition_immune = ['Sleep']

    # Merge background items into inventory (normalize to {name, qty} dicts)
    def _parse_item(item):
        """Parse item string: '4 Javelins' → {name: 'Javelin', qty: 4}.
        Strips leading quantity, trailing plural 's', and measurement units."""
        if isinstance(item, str):
            import re
            s = item.strip()
            m = re.match(r'^(\d+)\s+(.+)', s)
            if m:
                qty = int(m.group(1))
                rest = m.group(2)
                if not re.match(r'^(ft|lb|oz|gp|sp|cp|mi|yd|in|gal|feet|inch|mile|yard|pound|ounce)', rest, re.IGNORECASE):
                    # Strip trailing 's' for plurals (but not 'ss' endings like 'cross')
                    if rest.endswith('s') and not rest.endswith('ss') and len(rest) > 3:
                        rest = rest[:-1]
                    return {"name": rest, "qty": qty}
            # No quantity prefix — still singularize
            name = s
            if name.endswith('s') and not name.endswith('ss') and len(name) > 3:
                name = name[:-1]
            return {"name": name, "qty": 1}
        return item

    inventory = [_parse_item(item) for item in data.get("equipment", [])]
    bg_data_raw = data.get("background_data", "")
    if bg_data_raw and isinstance(bg_data_raw, dict):
        for item in bg_data_raw.get("items", []):
            inventory.append(_parse_item(item))

    db = get_db()
    cur = db.execute("""
        INSERT INTO characters (user_id, name, race, subrace, class_name, subclass,
        level, background, background_data, alignment, personality, backstory, strength, dexterity, constitution, intelligence,
        wisdom, charisma, hp_max, hp_current, ac, speed,
        proficiency_bonus, hit_dice, skills, features, languages, tool_proficiencies,
        weapon_proficiencies, armor_proficiencies, save_proficiencies, inventory, equipped,
        damage_resistances, damage_immunities, damage_vulnerabilities, condition_immunities,
        feature_data, attacks_data, spell_slot_data, passive_perception, portrait_url, portrait_prompt)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        user["id"], name, race_name, subrace, class_name, subclass, level,
        data.get("background",""), json.dumps(data.get("background_data","")), data.get("alignment",""), data.get("personality",""), data.get("backstory",""),
        stats["strength"], stats["dexterity"], stats["constitution"],
        stats["intelligence"], stats["wisdom"], stats["charisma"],
        hp_max, hp_max, ac_base, race_data.get("speed", 30),
        prof_bonus, f"1d{hd}",
        json.dumps(skills_list), json.dumps(build_features), json.dumps(race_data.get("languages",["Common"])),
        json.dumps(tool_profs), json.dumps(weapon_profs), json.dumps(armor_profs), json.dumps(save_profs),
        json.dumps(inventory), json.dumps([]),
        json.dumps(damage_resist), json.dumps(damage_immune), json.dumps(damage_vuln), json.dumps(condition_immune),
        json.dumps(enriched), json.dumps(build_attacks), json.dumps(spell_slots), passive,
        data.get("portrait_url", ""), data.get("portrait_prompt", "")
    ))
    char_id = cur.lastrowid
    db.commit()
    db.close()
    return JSONResponse({"id": char_id, "name": name})

# ── DM Tools: Monster helpers ──────────────────────────────────────────────

def _load_monster_cache() -> list[dict]:
    return _load_json_cache("monsters.json")

def _monster_cr_sort_key(m: dict) -> float:
    cr = m.get("challenge_rating", 0)
    if isinstance(cr, dict):
        cr = cr.get("challenge_rating", cr)
    try:
        return float(cr)
    except (TypeError, ValueError):
        return 99.0

def _xp_for_cr(cr) -> int:
    """Return XP reward for a challenge rating (PHB p.274 / DMG p.275)."""
    table = {0: 10, 0.125: 25, 0.25: 50, 0.5: 100, 1: 200, 2: 450, 3: 700,
             4: 1100, 5: 1800, 6: 2300, 7: 2900, 8: 3900, 9: 5000, 10: 5900,
             11: 7200, 12: 8400, 13: 10000, 14: 11500, 15: 13000, 16: 15000,
             17: 18000, 18: 20000, 19: 22000, 20: 25000, 21: 33000, 22: 41000,
             23: 50000, 24: 62000, 25: 75000, 26: 90000, 27: 105000, 28: 120000,
             29: 135000, 30: 155000}
    try:
        return table.get(float(cr), 0)
    except (TypeError, ValueError):
        return 0

def _format_monster_action(action: dict) -> dict:
    """Flatten a monster action for template display."""
    return {
        "name": action.get("name", ""),
        "desc": action.get("desc", ""),
        "attack_bonus": action.get("attack_bonus"),
        "damage": ", ".join(
            f"{d.get('damage_dice','')} {d.get('damage_type',{}).get('name','').lower()}"
            for d in action.get("damage", [])
        ) if action.get("damage") else "",
        "dc": action.get("dc", {}).get("dc_value") if action.get("dc") else None,
    }

@app.get("/dm-tools", response_class=HTMLResponse)
async def dm_tools(request: Request):
    """DM Tools main page — encounter builder, NPC manager, monster lookup."""
    user = require_user(request)
    db = get_db()

    # Load monsters from SRD cache
    all_monsters = _load_monster_cache()
    monsters_by_env = {}
    for m in all_monsters:
        m_type = m.get("type", "other")
        monsters_by_env.setdefault(m_type, []).append(m)

    # Load DM's NPCs
    npcs = [dict(r) for r in db.execute(
        "SELECT * FROM dm_npcs WHERE user_id = ? ORDER BY is_enemy DESC, name",
        (user["id"],)
    ).fetchall()]

    # Load encounters
    encounters = [dict(r) for r in db.execute(
        "SELECT * FROM dm_encounters WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],)
    ).fetchall()]

    # Load campaigns
    campaigns = [dict(r) for r in db.execute(
        "SELECT * FROM dm_campaigns WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],)
    ).fetchall()]

    # Parse JSON fields
    for npc in npcs:
        for f in ("skills", "features", "inventory"):
            try: npc[f] = json.loads(npc[f])
            except (json.JSONDecodeError, TypeError): npc[f] = []
    for c in campaigns:
        for f in ("quests", "locations"):
            try: c[f] = json.loads(c[f])
            except (json.JSONDecodeError, TypeError): c[f] = []

    # Monster types for filtering
    monster_types = sorted(monsters_by_env.keys())
    # Challenge rating options
    cr_ranges = [(0, 0.25), (0.5, 2), (3, 5), (6, 10), (11, 16), (17, 30)]

    db.close()
    return _render("dm_tools.html", request=request,
                   monsters=all_monsters, monster_types=monster_types,
                   cr_ranges=cr_ranges, npcs=npcs,
                   encounters=encounters, campaigns=campaigns)


@app.get("/api/dm/monster/{index}", response_class=JSONResponse)
async def dm_monster_detail(index: str, request: Request):
    """Full monster detail from SRD cache."""
    user = require_user(request)
    all_monsters = _load_monster_cache()
    for m in all_monsters:
        if m.get("index", "").lower() == index.lower():
            return JSONResponse(m)
    raise HTTPException(status_code=404, detail="Monster not found")


@app.get("/api/dm/monsters", response_class=JSONResponse)
async def dm_monster_list(request: Request):
    """List monsters with optional filters."""
    user = require_user(request)
    all_monsters = _load_monster_cache()
    return JSONResponse({"count": len(all_monsters), "monsters": all_monsters})


@app.get("/api/dm/monsters/search", response_class=JSONResponse)
async def dm_monster_search(request: Request, q: str = "", type: str = "", cr_min: float = 0, cr_max: float = 30, cr: str = ""):
    """Search/filter monsters by name, type, and CR range."""
    user = require_user(request)
    all_monsters = _load_monster_cache()
    results = []

    # Parse CR from string param as alternative
    if cr:
        try:
            cr_f = float(cr)
            cr_min = cr_f
            cr_max = cr_f
        except (TypeError, ValueError):
            pass

    for m in all_monsters:
        name = m.get("name", "").lower()
        m_type = m.get("type", "").lower()
        try:
            m_cr = float(m.get("challenge_rating", 0))
        except (TypeError, ValueError):
            m_cr = 0

        if q and q.lower() not in name:
            continue
        if type and type.lower() != m_type:
            continue
        if m_cr < cr_min or m_cr > cr_max:
            continue
        # Flatten for display
        results.append({
            "index": m["index"],
            "name": m["name"],
            "type": m.get("type", ""),
            "size": m.get("size", ""),
            "armor_class": m["armor_class"][0]["value"] if m.get("armor_class") else 10,
            "hit_points": m.get("hit_points", 0),
            "challenge_rating": m.get("challenge_rating", 0),
            "xp": m.get("xp", 0),
        })

    return JSONResponse({"count": len(results), "monsters": results, "total": len(all_monsters)})


@app.get("/api/dm/monsters/by-cr", response_class=JSONResponse)
async def dm_monsters_by_cr(request: Request):
    """Grouped monsters by CR tier for encounter building."""
    user = require_user(request)
    all_monsters = _load_monster_cache()
    tiers = {
        "trivial": [m for m in all_monsters if _monster_cr_sort_key(m) <= 0.25],
        "low": [m for m in all_monsters if 0.5 <= _monster_cr_sort_key(m) <= 2],
        "medium": [m for m in all_monsters if 3 <= _monster_cr_sort_key(m) <= 5],
        "high": [m for m in all_monsters if 6 <= _monster_cr_sort_key(m) <= 10],
        "deadly": [m for m in all_monsters if 11 <= _monster_cr_sort_key(m) <= 16],
        "legendary": [m for m in all_monsters if _monster_cr_sort_key(m) >= 17],
    }
    result = {}
    for tier, monsters in tiers.items():
        result[tier] = [{
            "index": m["index"], "name": m["name"], "cr": m.get("challenge_rating", 0),
            "type": m.get("type", ""), "size": m.get("size", ""),
            "hp": m.get("hit_points", 0), "ac": m["armor_class"][0]["value"] if m.get("armor_class") else 10,
        } for m in sorted(monsters, key=_monster_cr_sort_key)]
    return JSONResponse(result)


# ── DM Tools: NPC Management ─────────────────────────────────────────────

@app.post("/api/dm/npc/create", response_class=JSONResponse)
async def dm_npc_create(request: Request):
    """Create a new NPC (or enemy)."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    cur = db.execute("""
        INSERT INTO dm_npcs (user_id, name, race, class_name, subclass, level, is_enemy, is_party_npc,
            strength, dexterity, constitution, intelligence, wisdom, charisma,
            hp_max, hp_current, ac, speed, proficiency_bonus, hit_dice,
            skills, features, inventory, notes, alignment, role, faction, xp_reward)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        user["id"],
        data.get("name", "New NPC"),
        data.get("race", "Human"),
        data.get("class_name", ""),
        data.get("subclass", ""),
        int(data.get("level", 1)),
        1 if data.get("is_enemy") else 0,
        1 if data.get("is_party_npc") else 0,
        int(data.get("strength", 10)),
        int(data.get("dexterity", 10)),
        int(data.get("constitution", 10)),
        int(data.get("intelligence", 10)),
        int(data.get("wisdom", 10)),
        int(data.get("charisma", 10)),
        int(data.get("hp_max", 10)),
        int(data.get("hp_current", int(data.get("hp_max", 10)))),
        int(data.get("ac", 10)),
        int(data.get("speed", 30)),
        int(data.get("proficiency_bonus", 2)),
        data.get("hit_dice", "1d8"),
        json.dumps(data.get("skills", [])),
        json.dumps(data.get("features", [])),
        json.dumps(data.get("inventory", [])),
        data.get("notes", ""),
        data.get("alignment", "True Neutral"),
        data.get("role", "NPC"),
        data.get("faction", ""),
        int(data.get("xp_reward", 0)),
    ))
    db.commit()
    npc_id = cur.lastrowid
    db.close()
    return JSONResponse({"id": npc_id, "ok": True})


@app.get("/api/dm/npcs", response_class=JSONResponse)
async def dm_npcs_list(request: Request):
    """List all DM's NPCs."""
    user = require_user(request)
    db = get_db()
    rows = [dict(r) for r in db.execute(
        "SELECT * FROM dm_npcs WHERE user_id = ? ORDER BY is_enemy DESC, name",
        (user["id"],)
    ).fetchall()]
    db.close()
    for r in rows:
        for f in ("skills", "features", "inventory"):
            try: r[f] = json.loads(r[f])
            except: r[f] = []
    return JSONResponse({"npcs": rows})


@app.get("/api/dm/npc/{npc_id}", response_class=JSONResponse)
async def dm_npc_detail(npc_id: int, request: Request):
    """Full NPC detail."""
    user = require_user(request)
    db = get_db()
    row = db.execute("SELECT * FROM dm_npcs WHERE id = ? AND user_id = ?",
                     (npc_id, user["id"])).fetchone()
    db.close()
    if not row:
        raise HTTPException(status_code=404, detail="NPC not found")
    npc = dict(row)
    for f in ("skills", "features", "inventory"):
        try: npc[f] = json.loads(npc[f])
        except: npc[f] = []
    return JSONResponse(npc)


@app.post("/api/dm/npc/{npc_id}/update", response_class=JSONResponse)
async def dm_npc_update(npc_id: int, request: Request):
    """Update NPC fields."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    row = db.execute("SELECT id FROM dm_npcs WHERE id = ? AND user_id = ?",
                     (npc_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="NPC not found")

    allowed = {"name","race","class_name","subclass","level","strength","dexterity","constitution",
               "intelligence","wisdom","charisma","hp_max","hp_current","temp_hp","ac","speed",
               "proficiency_bonus","hit_dice","hit_dice_used","skills","features","inventory",
               "notes","alignment","role","faction","xp_reward","portrait_url",
               "is_enemy","is_party_npc"}
    updates = {}
    for k, v in data.items():
        if k in allowed:
            # Serialize list fields
            if isinstance(v, (list, dict)):
                v = json.dumps(v)
            updates[k] = v

    if updates:
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [npc_id, user["id"]]
        db.execute(f"UPDATE dm_npcs SET {sets} WHERE id=? AND user_id=?", vals)
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@app.post("/api/dm/npc/{npc_id}/delete", response_class=JSONResponse)
async def dm_npc_delete(npc_id: int, request: Request):
    """Delete an NPC."""
    user = require_user(request)
    db = get_db()
    db.execute("DELETE FROM dm_npcs WHERE id = ? AND user_id = ?", (npc_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@app.post("/api/dm/ai/build-encounter", response_class=JSONResponse)
async def dm_ai_build_encounter(request: Request):
    """AI-suggested encounter composition based on party size/level, environment, and difficulty."""
    user = require_user(request)
    data = await request.json()
    party_level = int(data.get("party_level", 5))
    party_size = int(data.get("party_size", 4))
    environment = data.get("environment", "dungeon")
    difficulty = data.get("difficulty", "medium")
    theme = data.get("theme", "")
    tone = data.get("tone", "")

    # XP budgets (DMG p.82 — Adjusted XP thresholds for party of N)
    # Per-character thresholds for medium encounters at each level
    MEDIUM_XP = {1: 50, 2: 100, 3: 150, 4: 250, 5: 500, 6: 600, 7: 750, 8: 900,
                 9: 1100, 10: 1200, 11: 1600, 12: 2000, 13: 2200, 14: 2500,
                 15: 2800, 16: 3200, 17: 3900, 18: 4200, 19: 4900, 20: 5700}
    HARD_XP = {1: 75, 2: 150, 3: 225, 4: 375, 5: 750, 6: 900, 7: 1100, 8: 1400,
               9: 1600, 10: 1900, 11: 2400, 12: 3000, 13: 3400, 14: 3800,
               15: 4300, 16: 4800, 17: 5900, 18: 6300, 19: 7300, 20: 8500}
    DEADLY_XP = {1: 100, 2: 200, 3: 400, 4: 500, 5: 1100, 6: 1400, 7: 1700, 8: 2100,
                 9: 2400, 10: 2800, 11: 3600, 12: 4500, 13: 5100, 14: 5700,
                 15: 6400, 16: 7200, 17: 8800, 18: 9500, 19: 10900, 20: 12700}

    xp_budgets = {"easy": MEDIUM_XP, "medium": MEDIUM_XP, "hard": HARD_XP, "deadly": DEADLY_XP}
    if difficulty == "easy":
        xp_per_char = int(MEDIUM_XP.get(party_level, 500) * 0.5)
    else:
        xp_per_char = xp_budgets.get(difficulty, MEDIUM_XP).get(party_level, 500)
    xp_budget = xp_per_char * party_size

    # Load monsters
    all_monsters = _load_monster_cache()

    # Filter by environment/theme hints
    candidates = []
    for m in all_monsters:
        m_type = m.get("type", "").lower()
        m_name = m.get("name", "").lower()
        m_env = m.get("environment", [])
        if isinstance(m_env, list):
            m_env = [e.lower() for e in m_env]

        # Environment/climate matching
        env_match = True
        if environment and environment.lower() not in ("any", ""):
            env_keywords = {
                "dungeon": ["dungeon", "underdark", "cavern", "underground"],
                "forest": ["forest", "woodland", "jungle"],
                "mountain": ["mountain", "hill", "peak"],
                "coastal": ["coastal", "coast", "ocean", "sea", "water"],
                "swamp": ["swamp", "marsh", "wetland"],
                "arctic": ["arctic", "tundra", "ice", "cold", "frozen"],
                "desert": ["desert", "sandy", "arid"],
                "grassland": ["grassland", "plain", "savanna"],
                "urban": ["urban", "city", "town", "settlement"],
                "underdark": ["underdark", "cavern", "underground", "dark"],
                "planar": ["planar", "outer", "inner", "abyss", "heaven"],
            }
            env_words = env_keywords.get(environment.lower(), [environment.lower()])
            # Check monster type alignment against environment
            type_env_map = {
                "aberration": ["underdark", "dungeon"],
                "beast": ["forest", "grassland", "mountain", "arctic", "desert", "coastal", "swamp"],
                "celestial": ["planar"],
                "construct": ["dungeon", "urban"],
                "dragon": ["mountain", "coastal", "forest", "swamp", "arctic", "desert"],
                "elemental": ["planar", "mountain"],
                "fey": ["forest", "grassland"],
                "fiend": ["planar", "underdark"],
                "giant": ["mountain", "hill", "grassland"],
                "humanoid": ["urban", "dungeon", "grassland", "mountain", "forest", "arctic", "desert", "swamp", "coastal"],
                "monstrosity": ["underdark", "dungeon", "forest", "mountain", "swamp", "desert"],
                "ooze": ["underdark", "dungeon", "swamp"],
                "plant": ["forest", "swamp", "jungle"],
                "undead": ["underdark", "dungeon", "urban", "swamp"],
            }
            suitable = type_env_map.get(m_type, [])
            if not any(e in env_words or e in suitable for e in env_words) and not any(kw in m_name for kw in env_words):
                env_match = False

        if not env_match:
            continue

        # CR within reasonable range of party level
        try:
            m_cr = float(m.get("challenge_rating", 0))
        except (TypeError, ValueError):
            continue

        # Allow monsters up to party_level+2 CR for tough fights, down to 1/8
        max_cr = party_level + 2
        min_cr = max(0, party_level - 3)
        if m_cr > max_cr or (m_cr < min_cr and m_cr > 0.125):
            continue
        if m_cr < 0.125:
            continue

        m_xp = _xp_for_cr(m_cr)
        if m_xp == 0:
            continue

        candidates.append({
            "index": m["index"], "name": m["name"], "cr": m_cr, "xp": m_xp,
            "type": m_type, "size": m.get("size", ""),
            "ac": m["armor_class"][0]["value"] if m.get("armor_class") else 10,
            "hp": m.get("hit_points", 0),
        })

    # AI composition suggestion
    ai_prompt = f"""Suggest a D&D 5e encounter for a party of {party_size} level {party_level} characters.
Environment: {environment}{f' Theme: {theme}' if theme else ''}{f' Tone: {tone}' if tone else ''}
Difficulty target: {difficulty}

Available monsters (pick 2-4 types, vary roles — one boss-type, some support, some minions):
{candidates[:50]}

Return ONLY valid JSON (no markdown):
{{"name": "encounter name (atmospheric, location-based)", "description": "1-2 sentence setup vignette", 
"composition": [{{"index": "monster index from list", "count": 2}}],
"tactics": "1-2 sentence tactics for this encounter"}}"""

    text = await _call_gemini(ai_prompt) or await _call_openrouter(ai_prompt) or await _call_ollama(ai_prompt)
    ai = _extract_json(text) if text else None

    composition = []
    xp_total = 0
    if ai and ai.get("composition"):
        for entry in ai.get("composition", []):
            count = int(entry.get("count", 1))
            idx = entry.get("index", "").lower()
            # Find matching monster
            m = next((c for c in candidates if c["index"].lower() == idx), None)
            if m:
                xp_total += m["xp"] * count
                composition.append({
                    "index": m["index"], "name": m["name"], "cr": m["cr"],
                    "xp": m["xp"], "count": count,
                    "ac": m["ac"], "hp": m["hp"], "type": m["type"], "size": m["size"],
                })

    # Fallback: algorithmic composition if AI fails
    if not composition:
        import random
        # Pick a boss-appropriate monster (CR ≈ party level ± 1)
        boss_candidates = [c for c in candidates if abs(c["cr"] - party_level) <= 1 and c["cr"] >= 1]
        if not boss_candidates:
            boss_candidates = candidates[:20]
        boss = random.choice(boss_candidates) if boss_candidates else None
        if boss:
            boss_count = 1
            composition.append({**boss, "count": boss_count})
            xp_total += boss["xp"] * boss_count

        # Add minions (lower CR)
        remaining = xp_budget - xp_total
        minion_candidates = [c for c in candidates if c["cr"] < (party_level - 1 if party_level > 1 else 0.5)]
        minion_count = 0
        while remaining > 0 and minion_candidates and minion_count < 6:
            minion = random.choice(minion_candidates)
            if minion["xp"] <= remaining:
                c = min(3, max(1, remaining // minion["xp"]))
                composition.append({**minion, "count": c})
                xp_total += minion["xp"] * c
                remaining -= minion["xp"] * c
                minion_count += c
            else:
                minion_candidates.remove(minion)

    # Calculate adjusted XP multiplier (DMG p.83 — Encounter Multipliers)
    total_monsters = sum(c.get("count", 1) for c in composition)
    if total_monsters == 1: mult = 1.0
    elif total_monsters == 2: mult = 1.5
    elif 3 <= total_monsters <= 6: mult = 2.0
    elif 7 <= total_monsters <= 10: mult = 2.5
    elif 11 <= total_monsters <= 14: mult = 3.0
    else: mult = 4.0
    adjusted_xp = int(xp_total * mult)
    budget_pct = int((adjusted_xp / xp_budget * 100)) if xp_budget > 0 else 100

    # Difficulty label (DMG p.82)
    if budget_pct < 50: diff_label = "Easy"
    elif budget_pct < 100: diff_label = "Medium"
    elif budget_pct < 150: diff_label = "Hard"
    else: diff_label = "Deadly"

    return JSONResponse({
        "name": ai.get("name", f"Random {environment.title()} Encounter") if ai else f"{environment.title()} Encounter",
        "description": ai.get("description", f"A {difficulty} encounter in {environment}.") if ai else "",
        "tactics": ai.get("tactics", "") if ai else "",
        "composition": composition,
        "xp": {"raw_total": xp_total, "adjusted": adjusted_xp, "budget": xp_budget, "budget_pct": budget_pct},
        "difficulty": diff_label,
        "party": {"level": party_level, "size": party_size},
    })


@app.post("/api/dm/ai/build-npc", response_class=JSONResponse)
async def dm_ai_build_npc(request: Request):
    """AI-generated NPC with full build. Uses same PHB-grounded engine as character builder."""
    user = require_user(request)
    data = await request.json()
    race = data.get("race", "Human")
    subrace = data.get("subrace", "")
    class_name = data.get("class_name", "Fighter")
    subclass = data.get("subclass", "")
    level = min(max(int(data.get("level", 1)), 1), 20)
    is_enemy = data.get("is_enemy", False)
    personality_hint = data.get("personality_hint", "")
    role_hint = data.get("role", "NPC")

    # Use the same build engine as PCs
    abilities = allocate_ability_scores(class_name, race, subrace)
    mods = {ability: (abilities[ability] - 10) // 2 for ability in abilities}
    pb = PROFICIENCY_BONUS.get(level, 2)
    con_mod = mods["constitution"]
    hp = calc_hp(class_name, level, con_mod)
    ac = _calculate_ac(class_name, level, mods)
    class_data = CLASSES.get(class_name, CLASSES["Fighter"])
    saves = {ability: mod + (pb if ability in class_data.get("saves", []) else 0)
             for ability, mod in mods.items()}
    skills = _pick_skills(class_name, mods)
    equipment = get_equipment_for_level(class_name, level)
    raw_features = get_class_features(class_name, level, subclass)
    enriched_features = enrich_features(raw_features, class_name=class_name, level=level, mods=mods)
    spells = get_spells_for_level(class_name, level) if get_caster_type(class_name) != "none" else {}
    spell_slots = get_spell_slots(class_name, level) if get_caster_type(class_name) != "none" else {}

    # AI flavor
    ai_prompt = f"""Generate a D&D 5e NPC concept for a {'villain/enemy' if is_enemy else 'friendly NPC'}.
Race: {race}{' (' + subrace + ')' if subrace else ''}
Class: {class_name} L{level}{' — ' + subclass if subclass else ''}
Role: {role_hint}
{personality_hint}
Return: {{\"name\": \"NPC Name\", \"personality\": \"2 traits\", \"backstory\": \"1-2 sentences\", \"alignment\": \"one from PHB list\", \"faction\": \"group name or empty\"}}"""

    text = await _call_gemini(ai_prompt) or await _call_openrouter(ai_prompt) or await _call_ollama(ai_prompt)
    ai = _extract_json(text) if text else None
    if ai:
        if ai.get("alignment") not in ALIGNMENTS:
            ai["alignment"] = random.choice(ALIGNMENTS)

    return JSONResponse({
        "name": ai.get("name", f"{race} {role_hint}") if ai else f"{race} {role_hint}",
        "personality": ai.get("personality", "") if ai else "",
        "backstory": ai.get("backstory", "") if ai else "",
        "alignment": ai.get("alignment", "True Neutral") if ai else "True Neutral",
        "faction": ai.get("faction", "") if ai else "",
        "build": {
            "level": level,
            "class": class_name,
            "subclass": subclass or None,
            "race": race,
            "subrace": subrace or None,
            "ability_scores": abilities,
            "modifiers": mods,
            "hit_points": hp,
            "armor_class": ac,
            "proficiency_bonus": pb,
            "saving_throws": saves,
            "skills": skills,
            "equipment": equipment,
            "features": enriched_features,
            "spells": spells.get("spells", {}),
            "cantrips": spells.get("cantrips", []),
            "spell_slots": spell_slots.get("by_level", {}),
            "hit_dice": f"{level}d{class_data['hd']}",
            "speed": RACES.get(race, RACES["Human"]).get("speed", 30),
        }
    })


# ── DM Tools: Encounter Management ───────────────────────────────────────

@app.post("/api/dm/encounter/create", response_class=JSONResponse)
async def dm_encounter_create(request: Request):
    """Create a new encounter."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    cur = db.execute("""
        INSERT INTO dm_encounters (user_id, name, description, location, environment, difficulty, notes)
        VALUES (?,?,?,?,?,?,?)
    """, (
        user["id"],
        data.get("name", "New Encounter"),
        data.get("description", ""),
        data.get("location", ""),
        data.get("environment", ""),
        data.get("difficulty", "medium"),
        data.get("notes", ""),
    ))
    db.commit()
    enc_id = cur.lastrowid
    db.close()
    return JSONResponse({"id": enc_id, "ok": True})


@app.get("/api/dm/encounters", response_class=JSONResponse)
async def dm_encounters_list(request: Request):
    """List all encounters."""
    user = require_user(request)
    db = get_db()
    rows = [dict(r) for r in db.execute(
        "SELECT * FROM dm_encounters WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],)
    ).fetchall()]
    # Count participants
    for r in rows:
        npcs = db.execute(
            "SELECT COUNT(*) as cnt FROM dm_encounter_npcs WHERE encounter_id = ?",
            (r["id"],)
        ).fetchone()
        r["npc_count"] = npcs["cnt"] if npcs else 0
    db.close()
    return JSONResponse({"encounters": rows})


@app.get("/api/dm/encounter/{enc_id}", response_class=JSONResponse)
async def dm_encounter_detail(enc_id: int, request: Request):
    """Full encounter detail with NPC participants."""
    user = require_user(request)
    db = get_db()
    row = db.execute("SELECT * FROM dm_encounters WHERE id = ? AND user_id = ?",
                     (enc_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404)
    enc = dict(row)
    # Get NPC participants
    participants = [dict(r) for r in db.execute("""
        SELECT en.*, n.name as npc_name, n.race, n.class_name, n.level, n.is_enemy,
               n.hp_max as npc_hp_max, n.ac as npc_ac, n.role, n.xp_reward
        FROM dm_encounter_npcs en
        JOIN dm_npcs n ON n.id = en.npc_id
        WHERE en.encounter_id = ?
        ORDER BY en.initiative DESC
    """, (enc_id,)).fetchall()]
    # Compute spell slots for caster NPCs
    for p in participants:
        cls = p.get("class_name", "")
        lvl = p.get("level", 1)
        if cls and get_caster_type(cls) != "none":
            slots = get_spell_slots(cls, lvl)
            p["npc_spell_slots"] = slots.get("by_level", {})
        else:
            p["npc_spell_slots"] = {}
    enc["participants"] = participants
    xp_total = sum(p.get("xp_reward", 0) or 0 for p in participants)
    db.close()
    return JSONResponse({"encounter": enc, "xp_total": xp_total})


@app.post("/api/dm/encounter/{enc_id}/add-npc", response_class=JSONResponse)
async def dm_encounter_add_npc(enc_id: int, request: Request):
    """Add an NPC to an encounter."""
    user = require_user(request)
    data = await request.json()
    npc_id = int(data.get("npc_id", 0))
    db = get_db()
    # Verify encounter exists
    enc = db.execute("SELECT id FROM dm_encounters WHERE id = ? AND user_id = ?",
                     (enc_id, user["id"])).fetchone()
    if not enc:
        db.close()
        raise HTTPException(status_code=404, detail="Encounter not found")
    # Verify NPC exists
    npc = db.execute("SELECT * FROM dm_npcs WHERE id = ? AND user_id = ?",
                     (npc_id, user["id"])).fetchone()
    if not npc:
        db.close()
        raise HTTPException(status_code=404, detail="NPC not found")
    # Add to encounter
    init = int(data.get("initiative", 0))
    db.execute("""
        INSERT INTO dm_encounter_npcs (encounter_id, npc_id, initiative, hp_current, hp_max, ac)
        VALUES (?,?,?,?,?,?)
    """, (enc_id, npc_id, init, npc["hp_current"], npc["hp_max"], npc["ac"]))
    db.commit()
    en_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    return JSONResponse({"id": en_id, "ok": True})


@app.post("/api/dm/encounter/{enc_id}/remove-npc", response_class=JSONResponse)
async def dm_encounter_remove_npc(enc_id: int, request: Request):
    """Remove an NPC from an encounter."""
    user = require_user(request)
    data = await request.json()
    en_id = int(data.get("en_id", 0))
    db = get_db()
    db.execute("""
        DELETE FROM dm_encounter_npcs
        WHERE id = ? AND encounter_id IN (SELECT id FROM dm_encounters WHERE user_id = ?)
    """, (en_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@app.post("/api/dm/encounter/{enc_id}/update-initiative", response_class=JSONResponse)
async def dm_encounter_update_init(enc_id: int, request: Request):
    """Update initiative, HP, defeated state, and individual spell slots for encounter NPCs."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    for entry in data.get("participants", []):
        en_id = int(entry.get("id", 0))
        init = int(entry.get("initiative", 0))
        hp_cur = int(entry.get("hp_current", 0))
        defeated = 1 if entry.get("defeated", False) else 0
        spell_slots_used = entry.get("spell_slots_used", {})
        if isinstance(spell_slots_used, dict):
            spell_slots_used = json.dumps(spell_slots_used)
        db.execute("""
            UPDATE dm_encounter_npcs SET initiative=?, hp_current=?, defeated=?, spell_slots_used=?
            WHERE id=? AND encounter_id IN (SELECT id FROM dm_encounters WHERE user_id=?)
        """, (init, hp_cur, defeated, spell_slots_used, en_id, user["id"]))

    # If a single participant should be updated (mark defeated / update HP)
    if "single" in data:
        s = data["single"]
        en_id = int(s.get("id", 0))
        updates = []
        vals = []
        if "hp_current" in s:
            updates.append("hp_current=?")
            vals.append(int(s["hp_current"]))
        if "defeated" in s:
            updates.append("defeated=?")
            vals.append(1 if s["defeated"] else 0)
        if "spell_slots_used" in s:
            up = s["spell_slots_used"]
            if isinstance(up, dict):
                up = json.dumps(up)
            updates.append("spell_slots_used=?")
            vals.append(up)
        if updates:
            vals += [en_id, user["id"]]
            db.execute(f"UPDATE dm_encounter_npcs SET {', '.join(updates)} WHERE id=? AND encounter_id IN (SELECT id FROM dm_encounters WHERE user_id=?)", vals)

    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@app.post("/api/dm/encounter/{enc_id}/update", response_class=JSONResponse)
async def dm_encounter_update(enc_id: int, request: Request):
    """Update encounter fields."""
    user = require_user(request)
    data = await request.json()
    allowed = {"name","description","location","environment","difficulty","status","notes"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if updates:
        db = get_db()
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [enc_id, user["id"]]
        db.execute(f"UPDATE dm_encounters SET {sets} WHERE id=? AND user_id=?", vals)
        db.commit()
        db.close()
    return JSONResponse({"ok": True})


@app.post("/api/dm/encounter/{enc_id}/delete", response_class=JSONResponse)
async def dm_encounter_delete(enc_id: int, request: Request):
    """Delete an encounter."""
    user = require_user(request)
    db = get_db()
    db.execute("DELETE FROM dm_encounter_npcs WHERE encounter_id IN (SELECT id FROM dm_encounters WHERE id=? AND user_id=?)",
               (enc_id, user["id"]))
    db.execute("DELETE FROM dm_encounters WHERE id=? AND user_id=?", (enc_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


# ── DM Tools: Campaign Management ────────────────────────────────────────

@app.post("/api/dm/campaign/create", response_class=JSONResponse)
async def dm_campaign_create(request: Request):
    """Create a new campaign."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    cur = db.execute("""
        INSERT INTO dm_campaigns (user_id, name, description, party_level, party_size, notes, quests, locations, characters)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        user["id"],
        data.get("name", "New Campaign"),
        data.get("description", ""),
        int(data.get("party_level", 1)),
        int(data.get("party_size", 4)),
        data.get("notes", ""),
        json.dumps(data.get("quests", [])),
        json.dumps(data.get("locations", [])),
        json.dumps(data.get("characters", [])),
    ))
    db.commit()
    cid = cur.lastrowid
    db.close()
    return JSONResponse({"id": cid, "ok": True})


@app.get("/api/dm/campaigns", response_class=JSONResponse)
async def dm_campaigns_list(request: Request):
    """List all campaigns with live character stats."""
    user = require_user(request)
    db = get_db()
    rows = [dict(r) for r in db.execute(
        "SELECT * FROM dm_campaigns WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],)
    ).fetchall()]

    # Enrich each campaign's characters with live data from the characters table
    for camp in rows:
        try:
            char_ids = json.loads(camp.get("characters") or "[]")
        except (json.JSONDecodeError, TypeError):
            camp["characters"] = []
            continue

        enriched = []
        for entry in char_ids:
            cid = entry.get("id") if isinstance(entry, dict) else entry
            row = db.execute("""
                SELECT id, name, race, subrace, class_name, subclass, level,
                       hp_current, hp_max, temp_hp, ac, strength, dexterity, constitution,
                       intelligence, wisdom, charisma, proficiency_bonus, speed,
                       inspiration, exhaustion, passive_perception, hit_dice, hit_dice_used
                FROM characters WHERE id=? AND user_id=?
            """, (cid, user["id"])).fetchone()
            if row:
                ch = dict(row)
                # Compute modifiers
                mods = {s: (ch[s] - 10) // 2 for s in
                        ["strength","dexterity","constitution","intelligence","wisdom","charisma"]}
                ch["modifiers"] = mods
                ch["status"] = entry.get("status", "active") if isinstance(entry, dict) else "active"
                ch["subrace"] = ch.get("subrace") or ""
                ch["subclass"] = ch.get("subclass") or ""
                # Get spell slots if caster
                cls = ch.get("class_name", "")
                lvl = ch.get("level", 1)
                if cls and get_caster_type(cls) != "none":
                    slots = get_spell_slots(cls, lvl)
                    ch["spell_slots"] = slots.get("by_level", {})
                else:
                    ch["spell_slots"] = {}
                enriched.append(ch)

        camp["characters"] = enriched

    db.close()
    return JSONResponse({"campaigns": rows})


@app.post("/api/dm/campaign/{camp_id}/update", response_class=JSONResponse)
async def dm_campaign_update(camp_id: int, request: Request):
    """Update campaign — supports field updates and array mutations."""
    user = require_user(request)
    data = await request.json()
    allowed = {"name","description","party_level","party_size","notes","session_notes","quests","locations","characters"}
    updates = {}

    # Handle add/remove quest operations
    if "addQuest" in data:
        new_quest = json.loads(data["addQuest"])
        db = get_db()
        row = db.execute("SELECT quests FROM dm_campaigns WHERE id=? AND user_id=?", (camp_id, user["id"])).fetchone()
        if row:
            quests = json.loads(row["quests"] or "[]")
            quests.append(new_quest)
            updates["quests"] = json.dumps(quests)
        db.close()
    elif "removeQuest" in data:
        idx = int(data["removeQuest"])
        db = get_db()
        row = db.execute("SELECT quests FROM dm_campaigns WHERE id=? AND user_id=?", (camp_id, user["id"])).fetchone()
        if row:
            quests = json.loads(row["quests"] or "[]")
            if 0 <= idx < len(quests):
                quests.pop(idx)
            updates["quests"] = json.dumps(quests)
        db.close()

    # Handle add/remove location operations
    if "addLocation" in data:
        new_loc = json.loads(data["addLocation"])
        db = get_db()
        row = db.execute("SELECT locations FROM dm_campaigns WHERE id=? AND user_id=?", (camp_id, user["id"])).fetchone()
        if row:
            locs = json.loads(row["locations"] or "[]")
            locs.append(new_loc)
            updates["locations"] = json.dumps(locs)
        db.close()
    elif "removeLocation" in data:
        idx = int(data["removeLocation"])
        db = get_db()
        row = db.execute("SELECT locations FROM dm_campaigns WHERE id=? AND user_id=?", (camp_id, user["id"])).fetchone()
        if row:
            locs = json.loads(row["locations"] or "[]")
            if 0 <= idx < len(locs):
                locs.pop(idx)
            updates["locations"] = json.dumps(locs)
        db.close()

    # Regular field updates
    for k, v in data.items():
        if k in allowed and k not in ("quests", "locations"):
            if isinstance(v, (list, dict)):
                v = json.dumps(v)
            updates[k] = v
        elif k in allowed and k in ("quests", "locations") and k not in updates:
            if isinstance(v, (list, dict)):
                v = json.dumps(v)
            updates[k] = v

    if updates:
        db = get_db()
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [camp_id, user["id"]]
        db.execute(f"UPDATE dm_campaigns SET {sets} WHERE id=? AND user_id=?", vals)
        db.commit()
        db.close()
    return JSONResponse({"ok": True})


@app.post("/api/dm/campaign/{camp_id}/delete", response_class=JSONResponse)
async def dm_campaign_delete(camp_id: int, request: Request):
    """Delete a campaign."""
    user = require_user(request)
    db = get_db()
    db.execute("DELETE FROM dm_campaigns WHERE id=? AND user_id=?", (camp_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@app.post("/api/dm/campaign/{camp_id}/add-character", response_class=JSONResponse)
async def dm_campaign_add_character(camp_id: int, request: Request):
    """Add a character to a campaign."""
    user = require_user(request)
    data = await request.json()
    char_id = int(data.get("character_id", 0))
    db = get_db()
    # Verify campaign ownership
    camp = db.execute("SELECT * FROM dm_campaigns WHERE id=? AND user_id=?", (camp_id, user["id"])).fetchone()
    if not camp:
        db.close()
        return JSONResponse({"error": "Campaign not found"}, status_code=404)
    # Verify character ownership
    char_row = db.execute("SELECT id, name, class_name, level, race FROM characters WHERE id=? AND user_id=?",
                          (char_id, user["id"])).fetchone()
    if not char_row:
        db.close()
        return JSONResponse({"error": "Character not found"}, status_code=404)

    chars = json.loads(camp["characters"] or "[]")
    # Don't add duplicates
    if any(c.get("id") == char_id for c in chars):
        db.close()
        return JSONResponse({"ok": True, "message": "Already added"})
    chars.append({
        "id": char_id,
        "name": char_row["name"],
        "class_name": char_row["class_name"],
        "level": char_row["level"],
        "race": char_row["race"],
        "status": "active",
    })
    db.execute("UPDATE dm_campaigns SET characters=? WHERE id=?", (json.dumps(chars), camp_id))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@app.post("/api/dm/campaign/{camp_id}/remove-character", response_class=JSONResponse)
async def dm_campaign_remove_character(camp_id: int, request: Request):
    """Remove a character from a campaign."""
    user = require_user(request)
    data = await request.json()
    char_id = int(data.get("character_id", 0))
    db = get_db()
    camp = db.execute("SELECT characters FROM dm_campaigns WHERE id=? AND user_id=?", (camp_id, user["id"])).fetchone()
    if not camp:
        db.close()
        return JSONResponse({"error": "Campaign not found"}, status_code=404)
    chars = json.loads(camp["characters"] or "[]")
    chars = [c for c in chars if c.get("id") != char_id]
    db.execute("UPDATE dm_campaigns SET characters=? WHERE id=?", (json.dumps(chars), camp_id))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@app.get("/api/dm/user-characters", response_class=JSONResponse)
async def dm_user_characters(request: Request):
    """List user's characters (for campaign party picker)."""
    user = require_user(request)
    db = get_db()
    rows = [dict(r) for r in db.execute(
        "SELECT id, name, race, class_name, level, subclass FROM characters WHERE user_id=? ORDER BY name",
        (user["id"],)
    ).fetchall()]
    db.close()
    return JSONResponse({"characters": rows})


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
    # Load background data
    # Load spell_slots_used
    try:
        char["spell_slots_used"] = json.loads(char.get("spell_slots_used") or "{}")
    except (json.JSONDecodeError, TypeError):
        char["spell_slots_used"] = {}
    try:
        char["background_data"] = json.loads(char["background_data"] or "")
    except (json.JSONDecodeError, TypeError):
        char["background_data"] = {}

    spells = [dict(r) for r in db.execute(
        "SELECT * FROM character_spells WHERE character_id = ? ORDER BY spell_level, spell_name",
        (char_id,)
    ).fetchall()]
    # Enrich spells with full SRD descriptions
    enrich_spells(spells)
    db.close()

    # Compute modifiers
    for stat in ["strength","dexterity","constitution","intelligence","wisdom","charisma"]:
        char[f"{stat}_mod"] = (char[stat] - 10) // 2

    # Merged save proficiencies (class-derived + user-toggled)
    class_saves = CLASSES.get(char.get("class_name",""), {}).get("saves", [])
    user_saves = char.get("save_proficiencies", [])
    saves_class = list(set(class_saves) | set(user_saves))

    # Build attacks from inventory weapons + existing attacks_data
    all_attacks = _build_inventory_attacks(char)

    # Caster type detection (PHB rules) — multiclass aware
    class_name = char.get("class_name", "")
    level = char.get("level", 1)
    mods = {s: char.get(f"{s}_mod", 0) for s in
            ["strength","dexterity","constitution","intelligence","wisdom","charisma"]}
    class_levels_data = parse_class_levels(char)
    
    if len(class_levels_data) > 1:
        # Multiclass: detect caster types present
        types = get_multiclass_caster_types(class_levels_data)
        has_full = types.get("full", 0) > 0
        has_half = types.get("half", 0) > 0
        has_pact = types.get("pact", 0) > 0
        has_any = has_full or has_half or has_pact
        if has_full and (has_half or has_pact):
            caster_type = "multiclass"
        elif has_half and has_pact:
            caster_type = "multiclass"
        elif has_full:
            caster_type = "full"
        elif has_half:
            caster_type = "half"
        elif has_pact:
            caster_type = "pact"
        else:
            caster_type = "none"
    else:
        caster_type = get_caster_type(class_name)
    
    sc_mod = get_spellcasting_mod(class_name, mods)
    prepared_max = get_prepared_max(class_name, level, sc_mod)
    spells_known_max = get_spells_known_max(class_name, level)
    cantrips_max = get_cantrips_known_max(class_name, level)

    return _render("sheet.html", request=request, character=char, spells=spells,
                   skill_abilities=SKILL_ABILITIES, classes=CLASSES, races=RACES,
                   bg_info=BACKGROUND_INFO, saves_class=saves_class, attacks=all_attacks,
                   armor_names=[], caster_type=caster_type, prepared_max=prepared_max,
                   spells_known_max=spells_known_max, cantrips_max=cantrips_max,
                   sc_mod=sc_mod, class_levels=class_levels_data)

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
        "languages","features","inventory","spell_slots_used","equipped","feature_data","attacks_data",
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

@app.post("/api/character/{char_id}/toggle-prepared", response_class=JSONResponse)
async def toggle_prepared(char_id: int, request: Request):
    user = require_user(request)
    data = await request.json()
    spell_id = data.get("id")
    prepared = 1 if data.get("prepared", False) else 0
    db = get_db()
    row = db.execute(
        "SELECT id FROM character_spells WHERE id=? AND character_id=?",
        (spell_id, char_id)
    ).fetchone()
    if not row:
        db.close()
        return JSONResponse({"error": "Spell not found"}, status_code=404)
    db.execute("UPDATE character_spells SET prepared=? WHERE id=?",
               (prepared, spell_id))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})

@app.post("/api/character/{char_id}/use-feature", response_class=JSONResponse)
async def use_feature(char_id: int, request: Request):
    """Use one charge of a limited-use feature. Returns updated uses count."""
    user = require_user(request)
    data = await request.json()
    feat_name = data.get("name", "")
    db = get_db()
    row = db.execute(
        "SELECT feature_data, user_id FROM characters WHERE id = ?", (char_id,)
    ).fetchone()
    if not row or row["user_id"] != user["id"]:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    features = json.loads(row["feature_data"] or "[]")
    new_uses = 0
    for feat in features:
        if feat.get("name") == feat_name:
            current = feat.get("uses", 0)
            if current > 0:
                feat["uses"] = current - 1
            new_uses = feat["uses"]
            break
    db.execute(
        "UPDATE characters SET feature_data = ? WHERE id = ?",
        (json.dumps(features), char_id),
    )
    db.commit()
    db.close()
    return JSONResponse({"ok": True, "uses": new_uses})

@app.post("/api/character/{char_id}/reset-features", response_class=JSONResponse)
async def reset_features(char_id: int, request: Request):
    """Reset all limited-use features to max (e.g., after long rest)."""
    user = require_user(request)
    data = await request.json()
    recharge_filter = data.get("recharge", None)
    db = get_db()
    row = db.execute(
        "SELECT feature_data, user_id FROM characters WHERE id = ?", (char_id,)
    ).fetchone()
    if not row or row["user_id"] != user["id"]:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    features = json.loads(row["feature_data"] or "[]")
    for feat in features:
        if "uses_max" not in feat or "recharge" not in feat:
            continue
        if recharge_filter and feat["recharge"] != recharge_filter:
            continue
        feat["uses"] = feat["uses_max"]
    db.execute(
        "UPDATE characters SET feature_data = ? WHERE id = ?",
        (json.dumps(features), char_id),
    )
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


# ── History & Relationships API ────────────────────────────────────────────

@app.get("/api/character/{char_id}/relationships", response_class=JSONResponse)
async def get_relationships(char_id: int, request: Request):
    user = require_user(request)
    db = get_db()
    rows = db.execute(
        "SELECT * FROM character_relationships WHERE character_id = ? AND user_id = ? ORDER BY created_at DESC",
        (char_id, user["id"])
    ).fetchall()
    db.close()
    return JSONResponse([dict(r) for r in rows])

@app.post("/api/character/{char_id}/relationships", response_class=JSONResponse)
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
    cursor = db.execute(
        "INSERT INTO character_relationships (character_id, user_id, name, relationship_type, description, npc_data, ai_generated) VALUES (?,?,?,?,?,?,0)",
        (char_id, user["id"], name, rel_type, description, json.dumps(npc_data))
    )
    rel_id = cursor.lastrowid
    db.commit()
    rel_row = dict(db.execute("SELECT * FROM character_relationships WHERE id = ?", (rel_id,)).fetchone())
    db.close()
    return JSONResponse(rel_row)

@app.post("/api/character/{char_id}/relationships/generate", response_class=JSONResponse)
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
    char_name = char.get("name", "the character")
    char_race = char.get("race", "Human")
    char_class = char.get("class_name", "Fighter")
    char_level = char.get("level", 1)
    ai_prompt = f"""Create a D&D NPC for a character's backstory.
Character: {char_name}, {char_race} {char_class} L{char_level}.
Relationship type: {rel_type}.
Player's description: "{prompt}"

Generate a vivid NPC. Return ONLY valid JSON (no markdown):
{{"name": "NPC Name", "race": "D&D race", "class": "class or occupation", "level": 1-20, "description": "2-3 sentence description of appearance and personality", "relationship_detail": "1-2 sentences about their history with {char_name}"}}"""
    ai_text = await _call_gemini(ai_prompt) or await _call_openrouter(ai_prompt) or await _call_ollama(ai_prompt)
    npc_data = {}
    name = prompt[:50]
    description = ""
    if ai_text:
        try:
            cleaned = ai_text.strip().removeprefix("```json").removesuffix("```").strip()
            ai_json = json.loads(cleaned)
            name = ai_json.get("name", name)
            description = (ai_json.get("description", "") + "\n\n" + ai_json.get("relationship_detail", "")).strip()
            npc_data = {"race": ai_json.get("race", ""), "class": ai_json.get("class", ""), "level": ai_json.get("level", 1)}
        except (json.JSONDecodeError, AttributeError):
            description = ai_text[:500]
    db = get_db()
    cursor = db.execute(
        "INSERT INTO character_relationships (character_id, user_id, name, relationship_type, description, prompt, npc_data, ai_generated) VALUES (?,?,?,?,?,?,?,1)",
        (char_id, user["id"], name, rel_type, description, prompt, json.dumps(npc_data))
    )
    rel_id = cursor.lastrowid
    db.commit()
    rel_row = dict(db.execute("SELECT * FROM character_relationships WHERE id = ?", (rel_id,)).fetchone())
    db.close()
    return JSONResponse(rel_row)

@app.put("/api/character/{char_id}/relationships/{rel_id}", response_class=JSONResponse)
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

@app.delete("/api/character/{char_id}/relationships/{rel_id}", response_class=JSONResponse)
async def delete_relationship(char_id: int, rel_id: int, request: Request):
    user = require_user(request)
    db = get_db()
    db.execute("DELETE FROM character_relationships WHERE id = ? AND character_id = ? AND user_id = ?",
               (rel_id, char_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


# ── Level-Up API ────────────────────────────────────────────────────────────

@app.get("/api/character/{char_id}/level-up-info", response_class=JSONResponse)
async def level_up_info(char_id: int, request: Request):
    """Return everything needed for the level-up wizard. Supports multi-level via ?target=N."""
    user = require_user(request)
    db = get_db()
    row = db.execute("SELECT * FROM characters WHERE id = ? AND user_id = ?", (char_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    char = dict(row)
    db.close()
    
    cl = parse_class_levels(char)
    current_level = total_level(cl)
    cls = char.get("class_name", "Fighter")  # primary class for backward compat
    
    # Parse target level (default next level, cap at 20)
    target_level = int(request.query_params.get("target", current_level + 1))
    target_level = max(current_level + 1, min(target_level, 20))
    
    if current_level >= 20:
        return JSONResponse({"error": "Already max level (20)"}, status_code=400)
    
    # Which class to level? Default: primary class, or specified via ?class_to_level=
    class_to_level = request.query_params.get("class_to_level", cls)
    if class_to_level not in cl:
        # If specified class isn't in the character's class list, it's a multiclass attempt
        # Check prerequisites
        abilities = {a.lower(): char.get(a.lower(), 10) for a in ABILITY_NAMES}
        if not meets_multiclass_prereq(abilities, class_to_level):
            prereqs = MULTICLASS_PREREQS.get(class_to_level, {})
            return JSONResponse({
                "error": f"Prerequisites not met for {class_to_level}: {prereqs}",
                "prerequisites": prereqs,
                "abilities": abilities,
            }, status_code=400)
    
    # Multiclass eligible classes (for UI)
    multiclass_options = []
    if target_level > current_level:
        abilities = {a.lower(): char.get(a.lower(), 10) for a in ABILITY_NAMES}
        for mc_class in MULTICLASS_PREREQS:
            if mc_class not in cl:
                meets = meets_multiclass_prereq(abilities, mc_class)
                profs = get_multiclass_proficiencies(mc_class)
                prereqs = MULTICLASS_PREREQS.get(mc_class, {})
                # Show all classes — eligible and ineligible (grayed out)
                missing = {}
                if not meets:
                    for stat, val in prereqs.items():
                        abil = abilities.get(stat.lower(), 10)
                        if abil < val:
                            missing[stat] = f"{abil}/{val}"
                multiclass_options.append({
                    "class": mc_class,
                    "prerequisites": prereqs,
                    "proficiencies": profs,
                    "available": meets,
                    "missing": missing,
                })
    
    cls = class_to_level  # the class gaining a level
    class_level = cl.get(cls, 0)  # current level IN that class
    
    # HP info (new class HD + CON mod)
    hd = CLASSES.get(cls, {}).get("hd", 8)
    con_mod = (char.get("constitution", 10) - 10) // 2
    avg_hp = (hd // 2) + 1 + con_mod
    hp_options = {"hit_die": f"d{hd}", "con_mod": con_mod, "average": avg_hp, "max_roll": hd + con_mod}
    
    # Accumulate per-level gains
    levels_gained = []
    all_features = []
    levels_gained_count = target_level - current_level
    
    for offset in range(1, levels_gained_count + 1):
        char_lvl = current_level + offset
        cls_lvl = class_level + offset  # level IN the class being leveled
        old_f = get_class_features(cls, cls_lvl - 1, char.get("subclass", ""))
        new_f = get_class_features(cls, cls_lvl, char.get("subclass", ""))
        gained = [f for f in new_f if f not in old_f]
        is_asi = cls_lvl in ASI_LEVELS.get(cls, [])
        levels_gained.append({
            "level": char_lvl,
            "class_level": cls_lvl,
            "class_name": cls,
            "features": gained,
            "is_asi": is_asi,
        })
        all_features.extend(gained)
    
    # ASI levels — these are the character levels where ASIs fire
    asi_levels = [lvl["level"] for lvl in levels_gained if lvl["is_asi"]]
    
    # Pre-compute ASI info for each ASI level
    asi_infos = {}
    abilities = {a: char.get(a.lower(), 10) for a in ABILITY_NAMES}
    for lvl in asi_levels:
        asi_infos[str(lvl)] = {
            "level": lvl,
            "abilities": dict(abilities),  # snapshot
            "max_20": [a for a in ABILITY_NAMES if abilities[a] >= 20],
        }
    
    # Feats — return ALL, JS filters by running abilities per ASI step
    feats_available = []
    for key, feat in FEATS.items():
        feats_available.append({
            "key": key, "name": feat["name"], "desc": feat["desc"],
            "asi": feat.get("asi"), "prereq": feat.get("prereq"),
        })
    
    # Subclass
    subclass_info = None
    sc = SUBCLASS_LEVELS.get(cls)
    if sc and current_level < sc["level"] <= target_level and not char.get("subclass"):
        descs = CLASSES.get(cls, {}).get("subclass_descs", {})
        subclass_info = {
            "level": sc["level"],
            "label": sc["label"],
            "options": sc["options"],
            "descriptions": {opt: descs.get(opt, "") for opt in sc["options"]},
        }
    
    # Proficiency bonus
    old_pb = PROFICIENCY_BONUS.get(current_level, 2)
    new_pb = PROFICIENCY_BONUS.get(target_level, 2)
    
    # Spell changes (aggregate across all levels)
    spell_info = None
    caster_type = get_caster_type(cls)
    if caster_type != "none":
        try:
            old_slots = get_spell_slots(cls, current_level)
            new_slots = get_spell_slots(cls, target_level)
        except:
            old_slots = {}; new_slots = {}
        spell_info = {
            "caster_type": caster_type,
            "spellcasting_ability": _spellcasting_ability(cls),
            "old_slots": old_slots, "new_slots": new_slots,
        }
        # Spells known
        if caster_type in ("full", "half"):
            old_known = get_spells_known_max(cls, current_level)
            new_known = get_spells_known_max(cls, target_level)
            spell_info["spells_known_change"] = max(0, new_known - old_known)
        # Cantrips
        cantrip_key = "cleric" if cls == "Cleric" else ("warlock" if cls == "Warlock" else "full")
        if caster_type in ("full", "pact") or cls == "Cleric":
            ct = CANTRIPS_PROGRESSION.get(cantrip_key, {})
            old_cantrips = sum(v for k, v in ct.items() if k <= current_level)
            new_cantrips = sum(v for k, v in ct.items() if k <= target_level)
            spell_info["cantrips_change"] = max(0, new_cantrips - old_cantrips)
    
    return JSONResponse({
        "class_name": cls,
        "current_level": current_level,
        "class_level": class_level,  # level IN this class (0 for new multiclass)
        "target_level": target_level,
        "levels": levels_gained,
        "all_features": all_features,
        "hp": hp_options,
        "asi_levels": asi_levels,
        "asi_info": asi_infos,
        "feats": feats_available,
        "subclass": subclass_info,
        "proficiency_bonus": {"old": old_pb, "new": new_pb, "changed": old_pb != new_pb},
        "spells": spell_info,
        "has_subclass": bool(char.get("subclass")),
        "multiclass": {
            "class_levels": cl,
            "class_to_level": class_to_level,
            "is_multiclass": class_to_level not in cl or len(cl) > 1,
            "options": multiclass_options,
        },
    })


def _feat_prereq_met(char: dict, prereq: str) -> bool:
    """Check if a character meets a feat prerequisite string."""
    if not prereq:
        return True
    if "Strength" in prereq:
        raw = prereq.replace("Strength ", "").replace("+", "").strip()
        try:
            return char.get("strength", 10) >= int(raw)
        except ValueError:
            pass
    if "Dexterity" in prereq:
        raw = prereq.replace("Dexterity ", "").replace("+", "").strip()
        try:
            return char.get("dexterity", 10) >= int(raw)
        except ValueError:
            pass
    if "Intelligence" in prereq:
        raw = prereq.replace("Intelligence ", "").replace("+", "").strip()
        try:
            return char.get("intelligence", 10) >= int(raw)
        except ValueError:
            pass
    if "Wisdom" in prereq:
        raw = prereq.replace("Wisdom ", "").replace("+", "").strip()
        try:
            return char.get("wisdom", 10) >= int(raw)
        except ValueError:
            pass
    if "Charisma" in prereq:
        raw = prereq.replace("Charisma ", "").replace("+", "").strip()
        try:
            return char.get("charisma", 10) >= int(raw)
        except ValueError:
            pass
    if "Constitution" in prereq:
        raw = prereq.replace("Constitution ", "").replace("+", "").strip()
        try:
            return char.get("constitution", 10) >= int(raw)
        except ValueError:
            pass
    # Spellcasting prereq
    if "spell" in prereq.lower() or "cast" in prereq.lower():
        caster = get_caster_type(char.get("class_name", ""))
        return caster != "none"
    if "armor" in prereq.lower():
        armors = char.get("armor_proficiencies", [])
        if isinstance(armors, str):
            armors = json.loads(armors)
        return prereq.split(" ")[0].lower() in [a.lower() for a in armors]
    return True


def _spellcasting_ability(class_name: str) -> str:
    spell_map = {
        "Bard": "Charisma", "Cleric": "Wisdom", "Druid": "Wisdom",
        "Paladin": "Charisma", "Ranger": "Wisdom", "Sorcerer": "Charisma",
        "Warlock": "Charisma", "Wizard": "Intelligence",
    }
    return spell_map.get(class_name, "Wisdom")


@app.post("/api/character/{char_id}/apply-level-up", response_class=JSONResponse)
async def apply_level_up(char_id: int, request: Request):
    """Apply all level-up choices across potentially multiple levels."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    row = db.execute("SELECT * FROM characters WHERE id = ? AND user_id = ?", (char_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    char = dict(row)
    
    cl = parse_class_levels(char)
    old_total = total_level(cl)
    target_level = int(data.get("target_level", old_total + 1))
    target_level = max(old_total + 1, min(target_level, 20))
    
    # Which class gains the level?
    class_to_level = data.get("class_to_level", char.get("class_name", "Fighter"))
    is_multiclass = class_to_level not in cl
    
    if is_multiclass:
        abilities = {a.lower(): char.get(a.lower(), 10) for a in ABILITY_NAMES}
        if not meets_multiclass_prereq(abilities, class_to_level):
            db.close()
            return JSONResponse({"error": f"Prerequisites not met for {class_to_level}"}, status_code=400)
    
    updates = {"level": target_level}
    changes = []
    
    # Track cumulative ability scores (start from current, apply ASIs in level order)
    cumulative = {a: char.get(a.lower(), 10) for a in ABILITY_NAMES}
    
    # Process levels in order — apply ASIs at each level BEFORE computing HP for that level
    hp_choices = data.get("hp_choices", {})
    asi_choices = data.get("asi_choices", {})
    feat_asi_choices = data.get("feat_asi_choices", {})
    
    total_hp_gain = 0
    hd = CLASSES.get(class_to_level, {}).get("hd", 8)
    levels_gained = target_level - old_total
    
    for offset in range(levels_gained):
        lvl_num = old_total + offset + 1
        lvl_str = str(lvl_num)
        if lvl_str in asi_choices:
            choice = asi_choices[lvl_str]
            if isinstance(choice, dict):
                for ability, increase in choice.items():
                    cumulative[ability] = cumulative.get(ability, 10) + increase
                    updates[ability.lower()] = cumulative[ability]
                    changes.append(f"L{lvl_str}: {ability} +{increase}")
            elif isinstance(choice, str) and choice.startswith("feat:"):
                feat_key = choice[5:]
                feat = FEATS.get(feat_key, {})
                changes.append(f"L{lvl_str}: Feat — {feat.get('name', feat_key)}")
                feat_asi = feat.get("asi")
                if feat_asi:
                    chosen_abi = feat_asi_choices.get(lvl_str)
                    if chosen_abi and chosen_abi in ABILITY_NAMES:
                        cumulative[chosen_abi] = cumulative.get(chosen_abi, 10) + feat_asi["amount"]
                        updates[chosen_abi.lower()] = cumulative[chosen_abi]
        
        # Now compute HP with current CON (includes this level's ASI if any)
        con_mod = (cumulative.get("Constitution", 10) - 10) // 2
        choice = hp_choices.get(lvl_str, "average")
        if choice == "max":
            hp_gain = hd + con_mod
        elif choice == "roll":
            hp_gain = random.randint(1, hd) + con_mod
        else:
            hp_gain = (hd // 2) + 1 + con_mod
        total_hp_gain += hp_gain
    
    updates["hp_max"] = char.get("hp_max", 10) + total_hp_gain
    updates["hp_current"] = updates["hp_max"]  # Full heal on level up
    changes.append(f"HP +{total_hp_gain}")
    
    # Tough feat: +2 HP per character level (PHB p.170)
    tough_level = None
    for lvl_str, choice in asi_choices.items():
        if isinstance(choice, str) and choice == "feat:tough":
            tough_level = int(lvl_str)
            break
    if tough_level:
        tough_hp = tough_level * 2
        updates["hp_max"] += tough_hp
        updates["hp_current"] += tough_hp
        changes.append(f"Tough feat: +{tough_hp} HP")
    
    # Subclass
    subclass_choice = data.get("subclass")
    if subclass_choice:
        updates["subclass"] = subclass_choice
        changes.append(f"Subclass: {subclass_choice}")
    
    # Update class_levels
    new_cl = dict(cl)
    new_cl[class_to_level] = new_cl.get(class_to_level, 0) + levels_gained
    updates["class_levels"] = json.dumps(new_cl)
    # Keep class_name as primary for backward compat
    if not is_multiclass:
        updates["class_name"] = class_to_level
    
    # Proficiency bonus
    updates["proficiency_bonus"] = PROFICIENCY_BONUS.get(target_level, 2)
    
    # Features — rebuild from all classes
    all_features = []
    for cls_n, cls_lvl in new_cl.items():
        sub = updates.get("subclass", char.get("subclass", ""))
        features = get_class_features(cls_n, cls_lvl, sub)
        # Wrap string features as dicts for dedup
        wrapped = [{"name": f, "source_class": cls_n} if isinstance(f, str) else dict(f, source_class=cls_n) for f in features]
        all_features.extend(wrapped)
    all_features = _deduplicate_multiclass_features(all_features, new_cl)
    # Unwrap back to strings for DB storage
    all_feature_names = [f["name"] if isinstance(f, dict) else str(f) for f in all_features]
    updates["features"] = json.dumps(all_feature_names)
    
    # Enriched feature_data
    mods = {a: (cumulative.get(a, 10) - 10) // 2 for a in ABILITY_NAMES}
    enriched = enrich_features(all_feature_names, class_name=class_to_level, level=target_level, mods=mods, class_levels=new_cl)
    updates["feature_data"] = json.dumps(enriched)
    
    # Spell slots — multiclass-aware
    char_copy = dict(char)
    char_copy["class_levels"] = json.dumps(new_cl)
    char_copy["level"] = target_level
    spell_slots = get_character_spell_slots(char_copy)
    updates["spell_slot_data"] = json.dumps(spell_slots)
    
    # Hit dice — per class (e.g. "3d10 + 2d8")
    hd_parts = []
    for cls_n, cls_lvl in new_cl.items():
        cls_hd = CLASSES.get(cls_n, {}).get("hd", 8)
        hd_parts.append(f"{cls_lvl}d{cls_hd}")
    updates["hit_dice"] = " + ".join(hd_parts)
    
    # Grant multiclass proficiencies
    if is_multiclass:
        profs = get_multiclass_proficiencies(class_to_level)
        cur_weapons = json.loads(char.get("weapon_proficiencies", "[]") or "[]")
        cur_armor = json.loads(char.get("armor_proficiencies", "[]") or "[]")
        if profs.get("weapons"):
            for w in profs["weapons"].split(","):
                if w.strip() not in cur_weapons:
                    cur_weapons.append(w.strip())
            updates["weapon_proficiencies"] = json.dumps(cur_weapons)
            changes.append(f"Multiclass {class_to_level}: gained weapon proficiencies")
        if profs.get("armor"):
            for a in profs["armor"].split(","):
                if a.strip() not in cur_armor:
                    cur_armor.append(a.strip())
            updates["armor_proficiencies"] = json.dumps(cur_armor)
            changes.append(f"Multiclass {class_to_level}: gained armor proficiencies")
    
    # Apply updates
    set_clauses = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [char_id]
    db.execute(f"UPDATE characters SET {set_clauses} WHERE id = ?", values)
    db.commit()
    db.close()
    
    return JSONResponse({
        "ok": True,
        "new_level": target_level,
        "changes": changes,
    })

# ── De-Level (rollback) ──────────────────────────────────────────────

@app.get("/api/character/{char_id}/de-level-info", response_class=JSONResponse)
async def de_level_info(char_id: int, request: Request):
    """Return what the character would look like at a lower level."""
    user = require_user(request)
    db = get_db()
    row = db.execute("SELECT * FROM characters WHERE id = ? AND user_id = ?", (char_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    char = dict(row)
    db.close()
    
    cls = char.get("class_name", "Fighter")
    current_level = char.get("level", 1)
    target_level = int(request.query_params.get("target", current_level - 1))
    target_level = max(1, min(target_level, current_level - 1))
    
    if current_level <= 1:
        return JSONResponse({"error": "Already at level 1"}, status_code=400)
    
    hd = CLASSES.get(cls, {}).get("hd", 8)
    con_mod = (char.get("constitution", 10) - 10) // 2
    avg_hp = (hd // 2) + 1 + con_mod
    
    # Features lost (at current but not at target)
    old_features = get_class_features(cls, target_level, char.get("subclass", ""))
    new_features = get_class_features(cls, current_level, char.get("subclass", ""))
    features_lost = [f for f in new_features if f not in old_features]
    
    # Get what target-level features look like
    target_features = get_class_features(cls, target_level, char.get("subclass", ""))
    
    # ASI levels being rolled back
    lost_asi_levels = [lvl for lvl in range(target_level + 1, current_level + 1) if lvl in ASI_LEVELS.get(cls, [])]
    
    # Current ability scores
    abilities = {a: char.get(a.lower(), 10) for a in ABILITY_NAMES}
    
    # Subclass note
    subclass_note = None
    sc = SUBCLASS_LEVELS.get(cls)
    current_subclass = char.get("subclass", "")
    if sc and current_subclass and sc["level"] > target_level:
        subclass_note = f"{sc['label']}: {current_subclass} (chosen at L{sc['level']} — will be cleared since target < L{sc['level']})"
    
    # Proficiency
    old_pb = PROFICIENCY_BONUS.get(current_level, 2)
    new_pb = PROFICIENCY_BONUS.get(target_level, 2)
    
    # Spell changes
    spell_info = None
    caster_type = get_caster_type(cls)
    if caster_type != "none":
        try:
            old_slots = get_spell_slots(cls, current_level)
            new_slots = get_spell_slots(cls, target_level)
        except:
            old_slots = {}; new_slots = {}
        spell_info = {
            "caster_type": caster_type,
            "old_slots": old_slots, "new_slots": new_slots,
        }
    
    # HP estimate: subtract average per level rolled back
    levels_lost = current_level - target_level
    estimated_hp = max(1, char.get("hp_max", 10) - levels_lost * avg_hp)
    
    return JSONResponse({
        "class_name": cls,
        "current_level": current_level,
        "target_level": target_level,
        "levels_lost": levels_lost,
        "features_lost": features_lost,
        "target_features": target_features,
        "hp_estimate": estimated_hp,
        "hp_per_level_avg": avg_hp,
        "hit_die": f"d{hd}",
        "current_abilities": abilities,
        "lost_asi_levels": lost_asi_levels,
        "subclass": char.get("subclass", ""),
        "subclass_note": subclass_note,
        "proficiency_bonus": {"old": old_pb, "new": new_pb, "changed": old_pb != new_pb},
        "spells": spell_info,
    })


@app.post("/api/character/{char_id}/de-level", response_class=JSONResponse)
async def apply_de_level(char_id: int, request: Request):
    """Roll back the character to a lower level."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    row = db.execute("SELECT * FROM characters WHERE id = ? AND user_id = ?", (char_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    char = dict(row)
    cls = char.get("class_name", "Fighter")
    old_level = char.get("level", 1)
    target_level = int(data.get("target_level", old_level - 1))
    target_level = max(1, min(target_level, old_level - 1))
    
    updates = {"level": target_level}
    changes = []
    
    # HP: use user-provided value or estimate
    hd = CLASSES.get(cls, {}).get("hd", 8)
    con_mod = (char.get("constitution", 10) - 10) // 2
    avg_hp = (hd // 2) + 1 + con_mod
    estimated_hp = max(1, char.get("hp_max", 10) - (old_level - target_level) * avg_hp)
    new_hp = int(data.get("hp_max", estimated_hp))
    new_hp = max(1, new_hp)  # Floor at 1
    updates["hp_max"] = new_hp
    updates["hp_current"] = new_hp
    changes.append(f"HP set to {new_hp}")
    
    # Ability scores: user-specified or keep current (user should adjust manually)
    ability_updates = data.get("abilities", {})
    for a in ABILITY_NAMES:
        key = a.lower()
        if a in ability_updates:
            updates[key] = int(ability_updates[a])
            if updates[key] != char.get(key, 10):
                changes.append(f"{a}: {char.get(key, 10)} → {updates[key]}")
    
    # Subclass: keep if target >= subclass level, clear otherwise
    sc = SUBCLASS_LEVELS.get(cls)
    if sc and sc["level"] > target_level:
        updates["subclass"] = ""
        if char.get("subclass"):
            changes.append(f"Subclass cleared ({char.get('subclass')})")
    
    # Proficiency
    updates["proficiency_bonus"] = PROFICIENCY_BONUS.get(target_level, 2)
    
    # Features rebuild
    features_list = get_class_features(cls, target_level, updates.get("subclass", char.get("subclass", "")))
    updates["features"] = json.dumps(features_list)
    
    # Feature data rebuild
    final_mods = {}
    for a in ABILITY_NAMES:
        key = a.lower()
        val = updates.get(key, char.get(key, 10))
        final_mods[a] = (val - 10) // 2
    enriched = enrich_features(features_list, class_name=cls, level=target_level, mods=final_mods)
    updates["feature_data"] = json.dumps(enriched)
    
    # Spell slots
    caster_type = get_caster_type(cls)
    if caster_type != "none":
        try:
            new_slots = get_spell_slots(cls, target_level)
            updates["spell_slot_data"] = json.dumps(new_slots)
        except:
            pass
    
    # Hit dice
    updates["hit_dice"] = f"{target_level}d{hd}"
    
    # Apply
    set_clauses = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [char_id]
    db.execute(f"UPDATE characters SET {set_clauses} WHERE id = ?", values)
    db.commit()
    db.close()
    
    return JSONResponse({
        "ok": True,
        "new_level": target_level,
        "changes": changes,
    })

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

def get_character_spell_slots(char_dict: dict) -> dict:
    """Return spell slots for a character, handling multiclass via PHB p.165.
    Single class: delegates to get_spell_slots. Multiclass: uses multiclass table.
    Warlock Pact Magic always tracked separately — returned as 'pact_slots' key."""
    cl = parse_class_levels(char_dict)
    total_lvl = total_level(cl)
    
    if len(cl) == 1:
        # Single class — use existing logic
        cls, lvl = next(iter(cl.items()))
        return get_spell_slots(cls, lvl)
    
    # Multiclass — separate Warlock from other casters
    result = {"slots": 0, "slot_level": None, "by_level": {}, "multiclass": True}
    pact_slots = {"slots": 0, "slot_level": 0, "note": "No Pact Magic"}
    
    # Warlock Pact Magic
    if "Warlock" in cl:
        warlock_level = cl["Warlock"]
        pact_result = get_spell_slots("Warlock", warlock_level)
        pact_slots = pact_result
        # Remove Warlock from caster level computation
        non_pact = {k: v for k, v in cl.items() if k != "Warlock"}
        if non_pact and has_casters(non_pact):
            result = get_multiclass_spell_slots(non_pact)
        result["pact_slots"] = pact_slots
        result["slots"] += pact_slots.get("slots", 0)
    else:
        if has_casters(cl):
            result = get_multiclass_spell_slots(cl)
    
    result["total_level"] = total_lvl
    return result

def has_casters(class_levels: dict[str, int]) -> bool:
    """Return True if any class in the dict is a spellcaster (full, half, or pact)."""
    return any(get_caster_type(cls) in ("full", "half", "pact") for cls in class_levels)


# ── Subclass Feature Names (PHB 2014) ─────────────────────────────────
# Maps subclass → {level: [feature names]}.
# Used to replace generic SRD names ("Martial Archetype feature") with real names.
SUBCLASS_FEATURES: dict[str, dict[int, list[str]]] = {
    # Barbarian
    "Path of the Berserker": {3: ["Frenzy"], 6: ["Mindless Rage"], 10: ["Intimidating Presence"], 14: ["Retaliation"]},
    "Path of the Totem Warrior": {3: ["Spirit Seeker", "Totem Spirit"], 6: ["Aspect of the Beast"], 10: ["Spirit Walker"], 14: ["Totemic Attunement"]},
    # Bard
    "College of Lore": {3: ["Bonus Proficiencies", "Cutting Words"], 6: ["Additional Magical Secrets"], 14: ["Peerless Skill"]},
    "College of Valor": {3: ["Bonus Proficiencies", "Combat Inspiration"], 6: ["Extra Attack"], 14: ["Battle Magic"]},
    # Cleric
    "Knowledge Domain": {1: ["Blessings of Knowledge"], 2: ["Channel Divinity: Knowledge of the Ages"], 6: ["Channel Divinity: Read Thoughts"], 8: ["Potent Spellcasting"], 17: ["Visions of the Past"]},
    "Life Domain": {1: ["Disciple of Life"], 2: ["Channel Divinity: Preserve Life"], 6: ["Blessed Healer"], 8: ["Divine Strike"], 17: ["Supreme Healing"]},
    "Light Domain": {1: ["Warding Flare"], 2: ["Channel Divinity: Radiance of the Dawn"], 6: ["Improved Flare"], 8: ["Potent Spellcasting"], 17: ["Corona of Light"]},
    "Nature Domain": {1: ["Acolyte of Nature"], 2: ["Channel Divinity: Charm Animals and Plants"], 6: ["Dampen Elements"], 8: ["Divine Strike"], 17: ["Master of Nature"]},
    "Tempest Domain": {1: ["Wrath of the Storm"], 2: ["Channel Divinity: Destructive Wrath"], 6: ["Thunderbolt Strike"], 8: ["Divine Strike"], 17: ["Stormborn"]},
    "Trickery Domain": {1: ["Blessing of the Trickster"], 2: ["Channel Divinity: Invoke Duplicity"], 6: ["Channel Divinity: Cloak of Shadows"], 8: ["Divine Strike"], 17: ["Improved Duplicity"]},
    "War Domain": {1: ["War Priest"], 2: ["Channel Divinity: Guided Strike"], 6: ["Channel Divinity: War God's Blessing"], 8: ["Divine Strike"], 17: ["Avatar of Battle"]},
    # Druid
    "Circle of the Land": {2: ["Bonus Cantrip", "Natural Recovery"], 6: ["Land's Stride"], 10: ["Nature's Ward"], 14: ["Nature's Sanctuary"]},
    "Circle of the Moon": {2: ["Combat Wild Shape", "Circle Forms"], 6: ["Primal Strike"], 10: ["Elemental Wild Shape"], 14: ["Thousand Forms"]},
    # Fighter
    "Champion": {3: ["Improved Critical"], 7: ["Remarkable Athlete"], 10: ["Additional Fighting Style"], 15: ["Superior Critical"], 18: ["Survivor"]},
    "Battle Master": {3: ["Combat Superiority"], 7: ["Know Your Enemy"], 10: ["Improved Combat Superiority"], 15: ["Relentless"]},
    "Eldritch Knight": {3: ["Spellcasting", "Weapon Bond"], 7: ["War Magic"], 10: ["Eldritch Strike"], 15: ["Arcane Charge"], 18: ["Improved War Magic"]},
    # Monk
    "Way of the Open Hand": {3: ["Open Hand Technique"], 6: ["Wholeness of Body"], 11: ["Tranquility"], 17: ["Quivering Palm"]},
    "Way of Shadow": {3: ["Shadow Arts"], 6: ["Shadow Step"], 11: ["Cloak of Shadows"], 17: ["Opportunist"]},
    "Way of the Four Elements": {3: ["Disciple of the Elements"]},
    # Paladin
    "Oath of Devotion": {3: ["Channel Divinity: Sacred Weapon", "Channel Divinity: Turn the Unholy"], 7: ["Aura of Devotion"], 15: ["Purity of Spirit"], 20: ["Holy Nimbus"]},
    "Oath of the Ancients": {3: ["Channel Divinity: Nature's Wrath", "Channel Divinity: Turn the Faithless"], 7: ["Aura of Warding"], 15: ["Undying Sentinel"], 20: ["Elder Champion"]},
    "Oath of Vengeance": {3: ["Channel Divinity: Abjure Enemy", "Channel Divinity: Vow of Enmity"], 7: ["Relentless Avenger"], 15: ["Soul of Vengeance"], 20: ["Avenging Angel"]},
    # Ranger
    "Hunter": {3: ["Hunter's Prey"], 7: ["Defensive Tactics"], 11: ["Multiattack"], 15: ["Superior Hunter's Defense"]},
    "Beast Master": {3: ["Ranger's Companion"], 7: ["Exceptional Training"], 11: ["Bestial Fury"], 15: ["Share Spells"]},
    # Rogue
    "Thief": {3: ["Fast Hands", "Second-Story Work"], 9: ["Supreme Sneak"], 13: ["Use Magic Device"], 17: ["Thief's Reflexes"]},
    "Assassin": {3: ["Assassinate", "Bonus Proficiencies"], 9: ["Infiltration Expertise"], 13: ["Impostor"], 17: ["Death Strike"]},
    "Arcane Trickster": {3: ["Spellcasting", "Mage Hand Legerdemain"], 9: ["Magical Ambush"], 13: ["Versatile Trickster"], 17: ["Spell Thief"]},
    # Sorcerer
    "Draconic Bloodline": {1: ["Dragon Ancestor", "Draconic Resilience"], 6: ["Elemental Affinity"], 14: ["Dragon Wings"], 18: ["Draconic Presence"]},
    "Wild Magic": {1: ["Wild Magic Surge", "Tides of Chaos"], 6: ["Bend Luck"], 14: ["Controlled Chaos"], 18: ["Spell Bombardment"]},
    # Warlock
    "The Archfey": {1: ["Fey Presence"], 6: ["Misty Escape"], 10: ["Beguiling Defenses"], 14: ["Dark Delirium"]},
    "The Fiend": {1: ["Dark One's Blessing"], 6: ["Dark One's Own Luck"], 10: ["Fiendish Resilience"], 14: ["Hurl Through Hell"]},
    "The Great Old One": {1: ["Awakened Mind"], 6: ["Entropic Ward"], 10: ["Thought Shield"], 14: ["Create Thrall"]},
    # Wizard
    "School of Abjuration": {2: ["Abjuration Savant", "Arcane Ward"], 6: ["Projected Ward"], 10: ["Improved Abjuration"], 14: ["Spell Resistance"]},
    "School of Conjuration": {2: ["Conjuration Savant", "Minor Conjuration"], 6: ["Benign Transposition"], 10: ["Focused Conjuration"], 14: ["Durable Summons"]},
    "School of Divination": {2: ["Divination Savant", "Portent"], 6: ["Expert Divination"], 10: ["The Third Eye"], 14: ["Greater Portent"]},
    "School of Enchantment": {2: ["Enchantment Savant", "Hypnotic Gaze"], 6: ["Instinctive Charm"], 10: ["Split Enchantment"], 14: ["Alter Memories"]},
    "School of Evocation": {2: ["Evocation Savant", "Sculpt Spells"], 6: ["Potent Cantrip"], 10: ["Empowered Evocation"], 14: ["Overchannel"]},
    "School of Illusion": {2: ["Illusion Savant", "Improved Minor Illusion"], 6: ["Malleable Illusions"], 10: ["Illusory Self"], 14: ["Illusory Reality"]},
    "School of Necromancy": {2: ["Necromancy Savant", "Grim Harvest"], 6: ["Undead Thralls"], 10: ["Inured to Undeath"], 14: ["Command Undead"]},
    "School of Transmutation": {2: ["Transmutation Savant", "Minor Alchemy"], 6: ["Transmuter's Stone"], 10: ["Shapechanger"], 14: ["Master Transmuter"]},
}


def _deduplicate_multiclass_features(features: list[dict], class_levels: dict[str, int]) -> list[dict]:
    """PHB 2014 p.164: Deduplicate features across classes.
    - Channel Divinity: keep all options, cap uses at max single-class value
    - Extra Attack: keep one (max 2), unless Fighter 11+ (3) or Fighter 20 (4)
    - Unarmored Defense: keep only first class's version
    Tags each feature with source_class for display."""
    import re
    seen = {}
    result = []
    has_extra_attack = False
    has_uad = False
    fighter_level = class_levels.get("Fighter", 0)
    
    for f in features:
        name = f.get("name", "")
        name_lower = name.lower()
        source = f.get("source_class", "")
        
        if not source and "/" not in name:  # skip if already merged
            # Infer source_class from feature name context (set by caller)
            pass
        
        # Channel Divinity — keep all entries, first one sets max uses
        if "channel divinity" in name_lower:
            key = "channel_divinity"
            if key not in seen:
                seen[key] = f
                result.append(f)
            else:
                existing = seen[key]
                # Merge options into name
                if name not in existing["name"]:
                    existing["name"] = existing["name"] + " | " + name
            continue
        
        # Extra Attack — no stacking (PHB p.164)
        if "extra attack" in name_lower:
            if has_extra_attack:
                continue
            has_extra_attack = True
            if fighter_level >= 20:
                f["name"] = "Extra Attack (4)"
            elif fighter_level >= 11:
                f["name"] = "Extra Attack (3)"
            result.append(f)
            continue
        
        # Unarmored Defense — first class only (PHB p.164)
        if "unarmored defense" in name_lower:
            if has_uad:
                continue
            has_uad = True
            result.append(f)
            continue
        
        # General dedup — strip use-count suffix differences
        base = re.sub(r'\s*\(\d+\s+(use|ki|point)s?\)', '', name).strip()
        key = (base.lower(), source)
        if key in seen:
            existing = seen[key]
            # Keep higher uses
            if f.get("uses", 0) > existing.get("uses", 0):
                result[result.index(existing)] = f
                seen[key] = f
            continue
        
        seen[key] = f
        result.append(f)
    
    return result


def get_class_features(class_name: str, level: int, subclass: str = "") -> list[str]:
    """Return class features gained by this level from SRD API cache.
    Deduplicates features that differ only by use count, and replaces
    generic subclass names with real feature names from SUBCLASS_FEATURES."""
    import re
    key = class_name.lower()
    levels = SRD_LEVELS.get(key, [])
    gained_raw = []
    for l in levels:
        lvl = l.get("level", 0)
        if lvl <= level:
            for feat in l.get("features", []):
                name = feat.get("name", "")
                if name:
                    gained_raw.append((lvl, name))
    
    # Deduplicate: strip use-count suffixes, keep highest-level entry
    # Only deduplicate when the original name had a use-count suffix
    import re
    _use_suffix_re = re.compile(r'\s*\(\d+\s+uses?(\s+per\s+rest)?\s*\)\s*$')
    
    def _strip_uses(name: str) -> str:
        return _use_suffix_re.sub('', name).strip()
    
    seen = {}  # base_name → (level, original_name, had_use_suffix)
    for lvl, name in gained_raw:
        base = _strip_uses(name)
        had_suffix = bool(_use_suffix_re.search(name))
        # Only deduplicate if the original had a use suffix
        if had_suffix:
            if base not in seen or lvl > seen[base][0]:
                seen[base] = (lvl, name, True)
        else:
            # Keep all entries that don't have use suffixes
            # Use a compound key to avoid collisions
            ckey = f"{base}__{lvl}"
            seen[ckey] = (lvl, name, False)
    
    # Build list sorted by level
    gained = []
    for key, (lvl, name, _had_suffix) in sorted(seen.items(), key=lambda x: x[1][0]):
        # Replace generic subclass names with real ones
        name = _replace_subclass_name(name, class_name, subclass, lvl)
        gained.append(f"L{lvl}: {name}")
    
    # Add subclass features that SRD doesn't include
    if subclass and subclass in SUBCLASS_FEATURES:
        sc_feats = SUBCLASS_FEATURES[subclass]
        for sc_lvl, feat_names in sc_feats.items():
            if sc_lvl <= level:
                for fn in feat_names:
                    entry = f"L{sc_lvl}: {fn}"
                    if entry not in gained:
                        # Insert at correct position
                        insert_at = 0
                        for i, g in enumerate(gained):
                            g_lvl = int(g.split(":")[0][1:])
                            if g_lvl > sc_lvl:
                                insert_at = i
                                break
                            insert_at = i + 1
                        gained.insert(insert_at, entry)
    
    return gained


_GENERIC_SUBCLASS_NAMES: dict[str, list[str]] = {
    "Barbarian": ["Primal Path", "Path feature"],
    "Bard": ["Bard College", "Bard College feature", "Countercharm"],
    "Cleric": ["Divine Domain"],
    "Druid": ["Druid Circle"],
    "Fighter": ["Martial Archetype", "Martial Archetype feature"],
    "Monk": ["Monastic Tradition"],
    "Paladin": ["Sacred Oath"],
    "Ranger": ["Ranger Archetype"],
    "Rogue": ["Roguish Archetype"],
    "Sorcerer": ["Sorcerous Origin"],
    "Warlock": ["Otherworldly Patron"],
    "Wizard": ["Arcane Tradition"],
}


def _replace_subclass_name(name: str, class_name: str, subclass: str, level: int) -> str:
    """Replace generic SRD subclass feature names with real ones from SUBCLASS_FEATURES."""
    if not subclass or subclass not in SUBCLASS_FEATURES:
        return name
    generics = _GENERIC_SUBCLASS_NAMES.get(class_name, [])
    # Check if this name matches a generic pattern
    name_clean = name.strip()
    for gen in generics:
        if name_clean == gen or name_clean.startswith(gen):
            sc_feats = SUBCLASS_FEATURES[subclass]
            if level in sc_feats:
                return sc_feats[level][0]  # Replace with first real feature
    return name


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


# ── Caster type detection & prepared spell computation ────────────────────

FULL_CASTERS = {"Bard", "Cleric", "Druid", "Sorcerer", "Wizard"}
HALF_CASTERS = {"Paladin", "Ranger"}
PACT_CASTERS = {"Warlock"}
PREPARED_CASTERS = {"Cleric", "Druid", "Paladin", "Wizard"}
SPELLS_KNOWN_CASTERS = {"Bard", "Ranger", "Sorcerer", "Warlock"}

# ── PHB 2014 Limited-Use Feature Definitions ─────────────────────────────
# (feature_key_lower, (min_level_uses, max_cap, recharge_type))
# recharge_type: 'short' (short or long rest), 'long' (long rest only), 'dawn' (at dawn)
# max_cap of 99 means scales with character level (capped by level-based formula)

# PHB Limited-Use Abilities (p.186+ per class)
LIMITED_USE = {
    # Barbarian (PHB p.46-50)
    "rage":                {"min": 2, "max": 99, "recharge": "long", "class": "Barbarian", "per": "level"},
    # Bard (PHB p.51-55) — Bardic Inspiration die increases at L5/10/15
    "bardic inspiration":  {"min": 3, "max": 99, "recharge": "short", "class": "Bard", "per": "level"},
    # Cleric (PHB p.56-62) / Paladin (PHB p.83-89) — single entry, class-differentiated in get_uses_for_level
    "channel divinity":    {"min": 1, "max": 3,  "recharge": "short", "class": "", "per": "level"},
    # Druid (PHB p.63-68)
    "wild shape":          {"min": 2, "max": 99, "recharge": "short", "class": "Druid", "per": "level"},
    # Fighter (PHB p.69-75)
    "action surge":        {"min": 1, "max": 2,  "recharge": "short", "class": "Fighter", "per": "fixed"},
    "second wind":         {"min": 1, "max": 1,  "recharge": "short", "class": "Fighter", "per": "fixed"},
    "indomitable":         {"min": 1, "max": 3,  "recharge": "long", "class": "Fighter", "per": "fixed"},
    # Monk (PHB p.76-82)
    "ki":                  {"min": 2, "max": 99, "recharge": "short", "class": "Monk", "per": "level"},
    # Paladin (PHB p.83-89)
    "divine sense":        {"min": 1, "max": 99, "recharge": "long", "class": "Paladin", "per": "level"},
    "lay on hands":        {"min": 5, "max": 99, "recharge": "long", "class": "Paladin", "per": "level"},
    # (channel divinity merged above — class-differentiated in get_uses_for_level)
    # Sorcerer (PHB p.99-105)
    "sorcery points":      {"min": 2, "max": 99, "recharge": "long", "class": "Sorcerer", "per": "level"},
    # Warlock (PHB p.105-112)
    "mystic arcanum":      {"min": 1, "max": 1,  "recharge": "long", "class": "Warlock", "per": "fixed"},
    # Wizard (PHB p.112-120)
    "arcane recovery":     {"min": 1, "max": 1,  "recharge": "long", "class": "Wizard", "per": "fixed"},
    # Dragonborn (PHB p.34) — Breath Weapon, 1/short rest
    "breath weapon":       {"min": 1, "max": 1,  "recharge": "short", "class": "", "per": "fixed"},
}


# ── Multiclass Support (PHB 2014 p.163-165) ──────────────────────────────

MULTICLASS_PREREQS = {
    "Barbarian": {"Strength": 13},
    "Bard": {"Charisma": 13},
    "Cleric": {"Wisdom": 13},
    "Druid": {"Wisdom": 13},
    "Fighter": {"Strength": 13, "Dexterity": 13},  # OR — either meets requirement
    "Monk": {"Dexterity": 13, "Wisdom": 13},
    "Paladin": {"Strength": 13, "Charisma": 13},
    "Ranger": {"Dexterity": 13, "Wisdom": 13},
    "Rogue": {"Dexterity": 13},
    "Sorcerer": {"Charisma": 13},
    "Warlock": {"Charisma": 13},
    "Wizard": {"Intelligence": 13},
}

# Proficiencies gained when multiclassing INTO a class (PHB p.164)
# None of these grant saving throw proficiencies
MULTICLASS_PROFICIENCIES = {
    "Barbarian": {"weapons": "simple,martial", "armor": "shields"},
    "Bard": {"armor": "light", "skills": 1},
    "Cleric": {"armor": "light,medium,shields"},
    "Druid": {"armor": "light,medium,shields"},
    "Fighter": {"weapons": "simple,martial", "armor": "light,medium,shields"},
    "Monk": {"weapons": "simple,shortswords"},
    "Paladin": {"weapons": "simple,martial", "armor": "light,medium,shields"},
    "Ranger": {"weapons": "simple,martial", "armor": "light,medium,shields", "skills": 1},
    "Rogue": {"armor": "light", "skills": 1},
    "Sorcerer": {},
    "Warlock": {"weapons": "simple", "armor": "light"},
    "Wizard": {},
}

def parse_class_levels(char_dict: dict) -> dict[str, int]:
    """Parse class_levels JSON from DB. Falls back to {class_name: level} for old chars."""
    import json as _json
    raw = char_dict.get("class_levels")
    if raw and raw not in ("{}", "", None):
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, dict) and parsed:
                return parsed
        except (_json.JSONDecodeError, TypeError):
            pass
    # Fallback: old single-class character
    cls = char_dict.get("class_name", "Fighter")
    lvl = char_dict.get("level", 1)
    return {cls: int(lvl)} if cls else {"Fighter": 1}

def total_level(class_levels: dict[str, int]) -> int:
    """Total character level = sum of all class levels."""
    return sum(class_levels.values())

def primary_class(class_levels: dict[str, int]) -> tuple[str, int]:
    """Return (class_name, level) for the highest-level class. Ties go to first class taken."""
    if not class_levels:
        return ("Fighter", 0)
    # Return first key (insertion order = first class taken)
    for cls, lvl in class_levels.items():
        return (cls, lvl)
    return ("Fighter", 0)

def meets_multiclass_prereq(char_abilities: dict, new_class: str) -> bool:
    """Check if character meets ability prerequisites to multiclass INTO new_class."""
    prereqs = MULTICLASS_PREREQS.get(new_class, {})
    if not prereqs:
        return True
    # Fighter: either STR 13 OR DEX 13
    if new_class == "Fighter":
        return (char_abilities.get("strength", 10) >= 13 or
                char_abilities.get("dexterity", 10) >= 13)
    for ability, minimum in prereqs.items():
        if char_abilities.get(ability.lower(), 10) < minimum:
            return False
    return True

def get_multiclass_proficiencies(new_class: str) -> dict:
    """Return proficiencies gained when multiclassing INTO new_class."""
    return MULTICLASS_PROFICIENCIES.get(new_class, {})

# PHB 2014 p.165 — Multiclass Spellcaster: Spell Slots per Spell Level
# Key = combined caster level. Value = [1st, 2nd, 3rd, 4th, 5th, 6th, 7th, 8th, 9th]
MULTICLASS_SPELL_SLOTS = {
    1:  [2, 0, 0, 0, 0, 0, 0, 0, 0],
    2:  [3, 0, 0, 0, 0, 0, 0, 0, 0],
    3:  [4, 2, 0, 0, 0, 0, 0, 0, 0],
    4:  [4, 3, 0, 0, 0, 0, 0, 0, 0],
    5:  [4, 3, 2, 0, 0, 0, 0, 0, 0],
    6:  [4, 3, 3, 0, 0, 0, 0, 0, 0],
    7:  [4, 3, 3, 1, 0, 0, 0, 0, 0],
    8:  [4, 3, 3, 2, 0, 0, 0, 0, 0],
    9:  [4, 3, 3, 3, 1, 0, 0, 0, 0],
    10: [4, 3, 3, 3, 2, 0, 0, 0, 0],
    11: [4, 3, 3, 3, 2, 1, 0, 0, 0],
    12: [4, 3, 3, 3, 2, 1, 0, 0, 0],
    13: [4, 3, 3, 3, 2, 1, 1, 0, 0],
    14: [4, 3, 3, 3, 2, 1, 1, 0, 0],
    15: [4, 3, 3, 3, 2, 1, 1, 1, 0],
    16: [4, 3, 3, 3, 2, 1, 1, 1, 0],
    17: [4, 3, 3, 3, 2, 1, 1, 1, 1],
    18: [4, 3, 3, 3, 3, 1, 1, 1, 1],
    19: [4, 3, 3, 3, 3, 2, 1, 1, 1],
    20: [4, 3, 3, 3, 3, 2, 2, 1, 1],
}

def compute_multiclass_caster_level(class_levels: dict[str, int]) -> int:
    """PHB p.165: Full = level, Half = floor(level/2), Third = floor(level/3). Warlock excluded."""
    import math
    total = 0
    for cls, level in class_levels.items():
        if cls in FULL_CASTERS:
            total += level
        elif cls in HALF_CASTERS:
            total += int(math.floor(level / 2))
    return total

def get_multiclass_spell_slots(class_levels: dict[str, int]) -> dict:
    """Return spell slots using PHB p.165 multiclass table. Same shape as get_spell_slots."""
    caster_level = compute_multiclass_caster_level(class_levels)
    slots = MULTICLASS_SPELL_SLOTS.get(min(caster_level, 20), MULTICLASS_SPELL_SLOTS[1])
    by_level = {i+1: slots[i] for i in range(9)}
    return {"slots": sum(slots), "slot_level": None, "by_level": by_level,
            "caster_level": caster_level, "multiclass": True}

# ── Level-Up Data ──────────────────────────────────────────────────────
ABILITY_NAMES = ["Strength", "Dexterity", "Constitution", "Intelligence", "Wisdom", "Charisma"]

# Which levels grant ASI per class (PHB 2014)
ASI_LEVELS: dict[str, list[int]] = {
    "Barbarian": [4,8,12,16,19],
    "Bard": [4,8,12,16,19],
    "Cleric": [4,8,12,16,19],
    "Druid": [4,8,12,16,19],
    "Fighter": [4,6,8,12,14,16,19],
    "Monk": [4,8,12,16,19],
    "Paladin": [4,8,12,16,19],
    "Ranger": [4,8,12,16,19],
    "Rogue": [4,8,10,12,16,19],
    "Sorcerer": [4,8,12,16,19],
    "Warlock": [4,8,12,16,19],
    "Wizard": [4,8,12,16,19],
}

# Subclass selection levels + options per class (PHB 2014)
SUBCLASS_LEVELS: dict[str, dict] = {
    "Barbarian": {"level": 3, "label": "Primal Path",
        "options": ["Path of the Berserker","Path of the Totem Warrior"]},
    "Bard": {"level": 3, "label": "Bard College",
        "options": ["College of Lore","College of Valor"]},
    "Cleric": {"level": 1, "label": "Divine Domain",
        "options": ["Knowledge Domain","Life Domain","Light Domain","Nature Domain","Tempest Domain","Trickery Domain","War Domain"]},
    "Druid": {"level": 2, "label": "Druid Circle",
        "options": ["Circle of the Land","Circle of the Moon"]},
    "Fighter": {"level": 3, "label": "Martial Archetype",
        "options": ["Champion","Battle Master","Eldritch Knight"]},
    "Monk": {"level": 3, "label": "Monastic Tradition",
        "options": ["Way of the Open Hand","Way of Shadow","Way of the Four Elements"]},
    "Paladin": {"level": 3, "label": "Sacred Oath",
        "options": ["Oath of Devotion","Oath of the Ancients","Oath of Vengeance"]},
    "Ranger": {"level": 3, "label": "Ranger Archetype",
        "options": ["Hunter","Beast Master"]},
    "Rogue": {"level": 3, "label": "Roguish Archetype",
        "options": ["Thief","Assassin","Arcane Trickster"]},
    "Sorcerer": {"level": 1, "label": "Sorcerous Origin",
        "options": ["Draconic Bloodline","Wild Magic"]},
    "Warlock": {"level": 1, "label": "Otherworldly Patron",
        "options": ["The Archfey","The Fiend","The Great Old One"]},
    "Wizard": {"level": 2, "label": "Arcane Tradition",
        "options": ["School of Abjuration","School of Conjuration","School of Divination","School of Enchantment","School of Evocation","School of Illusion","School of Necromancy","School of Transmutation"]},
}

# Cantrip progression
CANTRIPS_PROGRESSION: dict[str, dict[int, int]] = {
    "full": {1: 2, 4: 3, 10: 4},
    "warlock": {1: 2, 4: 3, 10: 4},
    "cleric": {1: 3, 4: 4, 10: 5},
}

# ── PHB 2014 Feats ─────────────────────────────────────────────────────
FEATS: dict[str, dict] = {
    "alert": {"name":"Alert","desc":"+5 initiative, can't be surprised, hidden creatures don't get advantage on attack rolls","prereq":None},
    "athlete": {"name":"Athlete","desc":"+1 Str/Dex, standing from prone costs 5 ft, climbing doesn't cost extra movement, running jump only needs 5 ft","prereq":None,"asi":{"choices":["Strength","Dexterity"],"amount":1}},
    "actor": {"name":"Actor","desc":"+1 Cha, adv on Deception/Performance to pass as someone else, mimic speech","prereq":None,"asi":{"choices":["Charisma"],"amount":1}},
    "charger": {"name":"Charger","desc":"When you Dash, bonus action melee attack with +5 dmg or shove 10 ft","prereq":None},
    "crossbow_expert": {"name":"Crossbow Expert","desc":"Ignore loading, no disadv on ranged attacks in melee, bonus action hand crossbow attack","prereq":None},
    "defensive_duelist": {"name":"Defensive Duelist","desc":"While wielding finesse weapon, add prof to AC as reaction vs melee attack","prereq":"Dexterity 13+"},
    "dual_wielder": {"name":"Dual Wielder","desc":"+1 AC while dual wielding, use non-light one-handed weapons, draw/stow two at once","prereq":None},
    "dungeon_delver": {"name":"Dungeon Delver","desc":"Adv on Perception/Investigation vs secret doors & traps, adv on trap saves, resist trap dmg","prereq":None},
    "durable": {"name":"Durable","desc":"+1 Con, min heal from Hit Die = 2×Con mod","prereq":None,"asi":{"choices":["Constitution"],"amount":1}},
    "elemental_adept": {"name":"Elemental Adept","desc":"Pick one damage type; spells ignore resistance, treat 1s as 2s on dmg dice","prereq":"Ability to cast at least one spell"},
    "fey_touched": {"name":"Fey Touched","desc":"+1 Int/Wis/Cha, learn Misty Step + 1 L1 div/ench spell, free 1/day each","prereq":None,"asi":{"choices":["Intelligence","Wisdom","Charisma"],"amount":1}},
    "grappler": {"name":"Grappler","desc":"Adv on attacks vs grappled targets, can pin restrained creature","prereq":"Strength 13+"},
    "great_weapon_master": {"name":"Great Weapon Master","desc":"On crit/kill with heavy melee, bonus action attack. -5 atk for +10 dmg on heavy attacks","prereq":None},
    "healer": {"name":"Healer","desc":"Stabilize → 1 HP. Use healer's kit: 1d6+4+target's HD HP per short rest","prereq":None},
    "heavily_armored": {"name":"Heavily Armored","desc":"+1 Str, gain heavy armor proficiency","prereq":"Medium armor proficiency","asi":{"choices":["Strength"],"amount":1}},
    "heavy_armor_master": {"name":"Heavy Armor Master","desc":"+1 Str, B/P/S from nonmagical weapons reduced by 3 while in heavy armor","prereq":"Heavy armor proficiency","asi":{"choices":["Strength"],"amount":1}},
    "inspiring_leader": {"name":"Inspiring Leader","desc":"10-min speech gives up to 6 allies temp HP = level + Cha mod","prereq":"Charisma 13+"},
    "keen_mind": {"name":"Keen Mind","desc":"+1 Int, always know north, time till sunrise/sunset, recall past month perfectly","prereq":None,"asi":{"choices":["Intelligence"],"amount":1}},
    "lightly_armored": {"name":"Lightly Armored","desc":"+1 Str/Dex, gain light armor proficiency","prereq":None,"asi":{"choices":["Strength","Dexterity"],"amount":1}},
    "linguist": {"name":"Linguist","desc":"+1 Int, learn 3 languages, create written ciphers","prereq":None,"asi":{"choices":["Intelligence"],"amount":1}},
    "lucky": {"name":"Lucky","desc":"3 luck points per long rest, spend to reroll any d20 or force enemy reroll","prereq":None},
    "mage_slayer": {"name":"Mage Slayer","desc":"Reaction melee attack vs adjacent caster, adv on saves vs adjacent spells","prereq":None},
    "magic_initiate": {"name":"Magic Initiate","desc":"Learn 2 cantrips + 1 L1 spell from one class's list; free 1/day casting","prereq":None},
    "martial_adept": {"name":"Martial Adept","desc":"Learn 2 Battle Master maneuvers, one d6 superiority die","prereq":None},
    "medium_armor_master": {"name":"Medium Armor Master","desc":"No disadv on Stealth in medium armor, Dex cap +3 instead of +2","prereq":"Medium armor proficiency"},
    "mobile": {"name":"Mobile","desc":"Speed +10 ft, Dash ignores difficult terrain, no OA from targets you attacked","prereq":None},
    "moderately_armored": {"name":"Moderately Armored","desc":"+1 Str/Dex, gain medium armor + shield proficiency","prereq":"Light armor proficiency","asi":{"choices":["Strength","Dexterity"],"amount":1}},
    "mounted_combatant": {"name":"Mounted Combatant","desc":"Adv on melee vs unmounted smaller than mount, redirect attacks to you, mount takes half/zero AoE","prereq":None},
    "observant": {"name":"Observant","desc":"+1 Int/Wis, +5 passive Perception and Investigation, read lips","prereq":None,"asi":{"choices":["Intelligence","Wisdom"],"amount":1}},
    "polearm_master": {"name":"Polearm Master","desc":"Bonus action 1d4 butt attack, OA when creatures enter reach with polearms","prereq":None},
    "resilient": {"name":"Resilient","desc":"+1 to one ability, gain proficiency in that ability's saving throw","prereq":None,"asi":{"choices":["Strength","Dexterity","Constitution","Intelligence","Wisdom","Charisma"],"amount":1}},
    "ritual_caster": {"name":"Ritual Caster","desc":"Gain ritual book; learn 2 L1 rituals, can add more from scrolls","prereq":"Intelligence or Wisdom 13+"},
    "savage_attacker": {"name":"Savage Attacker","desc":"Once per turn, reroll melee weapon damage dice and use either total","prereq":None},
    "sentinel": {"name":"Sentinel","desc":"OA reduces speed to 0, OA even vs Disengage, reaction attack vs attackers who target allies","prereq":None},
    "shadow_touched": {"name":"Shadow Touched","desc":"+1 Int/Wis/Cha, learn Invisibility + one L1 necro/illusion spell, free 1/day each","prereq":None,"asi":{"choices":["Intelligence","Wisdom","Charisma"],"amount":1}},
    "sharpshooter": {"name":"Sharpshooter","desc":"No disadv at long range, ignore half/three-quarters cover, -5 atk for +10 dmg","prereq":None},
    "shield_master": {"name":"Shield Master","desc":"Bonus action shove after Attack, add shield AC to Dex saves vs single-target spells, take zero dmg instead of half on successful AoE Dex save","prereq":None},
    "skilled": {"name":"Skilled","desc":"Gain proficiency in any 3 skills or tools","prereq":None},
    "skulker": {"name":"Skulker","desc":"Ranged attacks in dim light don't reveal position, hiding only needs light obscurement","prereq":"Dexterity 13+"},
    "spell_sniper": {"name":"Spell Sniper","desc":"Ranged spell attacks ignore half/three-quarters cover, range doubled, learn one attack cantrip","prereq":"Ability to cast at least one spell"},
    "tavern_brawler": {"name":"Tavern Brawler","desc":"+1 Str/Con, proficient in improvised weps (d4), bonus action grapple on unarmed hit","prereq":None,"asi":{"choices":["Strength","Constitution"],"amount":1}},
    "tough": {"name":"Tough","desc":"HP maximum increases by 2 per character level (retroactive)","prereq":None},
    "war_caster": {"name":"War Caster","desc":"Adv on Con saves for concentration, somatic components with weapon/shield, cast spell as OA","prereq":"Ability to cast at least one spell"},
    "weapon_master": {"name":"Weapon Master","desc":"+1 Str/Dex, gain proficiency with 4 weapons","prereq":None,"asi":{"choices":["Strength","Dexterity"],"amount":1}},
}

# ── Feature → Combat Action mapping ──────────────────────────────────
# Maps feature name (lowercase) to (action_type, short_action_label)
# action_type: "Action", "Bonus Action", or "Reaction"
FEATURE_ACTION_TYPES = {
    # Barbarian
    "rage":                 ("Bonus Action", "Rage — advantage on STR, +2 dmg, resist B/P/S"),
    # Bard
    "bardic inspiration":   ("Bonus Action", "Bardic Inspiration — grant 1d6 to ally"),
    # Cleric / Paladin
    "channel divinity":     ("Action", "Channel Divinity — invoke divine power"),
    # Druid
    "wild shape":           ("Action", "Wild Shape — transform into a beast"),
    # Fighter
    "action surge":         ("Action", "Action Surge — take an additional action"),
    "indomitable":          ("Reaction", "Indomitable — reroll a failed saving throw"),
    "second wind":          ("Bonus Action", "Second Wind — regain 1d10 + level HP"),
    # Paladin
    "divine sense":         ("Action", "Divine Sense — detect celestials/fiends/undead"),
    "lay on hands":         ("Action", "Lay on Hands — heal 5×level HP"),
    # Rogue (features not in LIMITED_USE but present in get_class_features)
    "cunning action":       ("Bonus Action", "Cunning Action — Dash, Disengage, or Hide"),
    "uncanny dodge":        ("Reaction", "Uncanny Dodge — halve damage from one attack"),
    # Monk
    "flurry of blows":      ("Bonus Action", "Flurry of Blows — two unarmed strikes (1 ki)"),
    "patient defense":      ("Bonus Action", "Patient Defense — Dodge as bonus action (1 ki)"),
    "step of the wind":     ("Bonus Action", "Step of the Wind — Dash/Disengage + jump (1 ki)"),
    # Dragonborn
    "breath weapon":        ("Action", "Breath Weapon — 2d6 damage, DEX save (DC 8+CON+PB)"),
    # Resource pools (not combat actions per se, but tracked on Actions tab)
    "ki":                   ("Resource", "Ki — spend on Flurry, Patient Defense, Step of the Wind"),
    "sorcery points":       ("Resource", "Sorcery Points — spend on Metamagic options"),
    # Recovery / out-of-combat features
    "mystic arcanum":       ("Action", "Mystic Arcanum — cast a high-level Warlock spell (1/LR)"),
    "arcane recovery":      ("Short Rest", "Arcane Recovery — regain spell slots on short rest"),
}

# ── PHB scale functions per feature ──
def get_uses_for_level(feat_key: str, class_name: str, level: int) -> int:
    """Return the number of uses for a limited-use feature at this level."""
    lu = LIMITED_USE.get(feat_key, {})
    if not lu:
        return 0
    lu_class = lu.get("class", "")
    if lu_class and lu_class != class_name:
        return 0
    if lu["per"] == "fixed":
        # Fixed-scaling features: use level thresholds
        if feat_key == "action surge":
            return 1 if level < 17 else 2  # PHB p.72: L2=1, L17=2
        if feat_key == "indomitable":
            return 1 if level < 13 else 2 if level < 17 else 3  # PHB p.72: L9=1, L13=2, L17=3
        if feat_key == "second wind":
            return 1  # Always 1 use
        if feat_key == "mystic arcanum":
            return 1  # 1 per mystic arcanum level (but each is a separate feature)
    if lu["per"] == "level":
        # Level-scaling features
        if feat_key == "rage":
            # PHB Barbarian table: L1-2=2, L3-5=3, L6-11=4, L12-16=5, L17-20=6
            if level >= 17: return 6
            if level >= 12: return 5
            if level >= 6:  return 4
            if level >= 3:  return 3
            return 2
        if feat_key == "bardic inspiration":
            # PHB Bard table: L1-4=3, L5-9=4, L10-14=5, L15-20=6
            if level >= 15: return 6
            if level >= 10: return 5
            if level >= 5:  return 4
            return 3
        if feat_key == "channel divinity":
            # Cleric: L1-5=1, L6-17=2, L18+=3
            if class_name == "Cleric":
                if level >= 18: return 3
                if level >= 6:  return 2
                return 1
            # Paladin: always 1 use per short rest (PHB p.85)
            return 1
        if feat_key == "wild shape":
            # PHB p.66: always 2 uses per short rest. (Archdruid at L20 = unlimited)
            return 2
        if feat_key == "ki":
            # Ki = monk level (PHB p.78)
            return level
        if feat_key == "divine sense":
            # Paladin: 1 + Cha mod (min 1). We return 1 as base, Cha mod handled separately
            return 1  # + Cha mod added at enrichment time
        if feat_key == "lay on hands":
            # Paladin: 5 * level (HP pool, not per-use)
            return level * 5
        if feat_key == "sorcery points":
            # Sorcery points = sorcerer level (PHB p.101)
            return level
    return lu.get("min", 1)


def get_caster_type(class_name: str) -> str:
    """Return 'full', 'half', 'pact', 'third', or 'none' for a single class."""
    if class_name in FULL_CASTERS:
        return "full"
    if class_name in HALF_CASTERS:
        return "half"
    if class_name in PACT_CASTERS:
        return "pact"
    return "none"

def get_multiclass_caster_types(class_levels: dict[str, int]) -> dict:
    """Return dict of {caster_type: total_level} for multiclass character.
    e.g. {'full': 5, 'pact': 3, 'none': 2} for Wizard 5 / Warlock 3 / Barb 2."""
    types = {}
    for cls, level in class_levels.items():
        ct = get_caster_type(cls)
        types[ct] = types.get(ct, 0) + level
    return types

def is_multiclass_caster(class_levels: dict[str, int]) -> bool:
    """Return True if character has 2+ spellcasting classes (pact + normal counts)."""
    types = get_multiclass_caster_types(class_levels)
    caster_count = sum(1 for ct in types if ct != "none")
    return caster_count >= 2

def get_prepared_max(class_name: str, level: int, spellcasting_mod: int) -> int:
    """PHB p.xxx: prepared casters prepare = level + spellcasting_mod spells.
    Paladin uses Cha mod, Cleric/Druid use Wis mod, Wizard uses Int mod."""
    if class_name not in PREPARED_CASTERS:
        return 0
    return max(1, level + spellcasting_mod)

def get_spellcasting_mod(class_name: str, mods: dict) -> int:
    """Return the spellcasting ability modifier for this class (PHB p.xxx)."""
    if class_name in ("Bard", "Paladin", "Sorcerer", "Warlock"):
        return mods.get("charisma", 0)
    if class_name in ("Cleric", "Druid", "Ranger"):
        return mods.get("wisdom", 0)
    if class_name in ("Wizard",):
        return mods.get("intelligence", 0)
    return 0

def get_spells_known_max(class_name: str, level: int) -> int:
    """Return max spells known at this level from SRD data, or 0 if prepared caster / non-caster."""
    if class_name not in SPELLS_KNOWN_CASTERS:
        return 0
    key = class_name.lower()
    entries = SRD_LEVELS.get(key, [])
    for e in entries:
        if e.get("level") == level:
            return e.get("spellcasting", {}).get("spells_known", 0)
    return 0

def get_cantrips_known_max(class_name: str, level: int) -> int:
    """Return max cantrips known at this level from SRD data."""
    key = class_name.lower()
    entries = SRD_LEVELS.get(key, [])
    for e in entries:
        if e.get("level") == level:
            return e.get("spellcasting", {}).get("cantrips_known", 0)
    return 0

# ── Spell enrichment (SRD descriptions) ───────────────────────────────────

def enrich_spells(spells: list[dict]) -> None:
    """Add full SRD spell data to each spell dict in-place."""
    if not SRD_SPELLS:
        return
    # Build lookup by lowercase name
    srd_lookup = {s.get("name", "").lower(): s for s in SRD_SPELLS}
    for sp in spells:
        name = sp.get("spell_name", "")
        srd = srd_lookup.get(name.lower())
        if srd:
            sp["srd"] = {
                "desc": srd.get("desc", []),
                "higher_level": srd.get("higher_level", []),
                "range": srd.get("range", ""),
                "components": srd.get("components", []),
                "material": srd.get("material", ""),
                "ritual": srd.get("ritual", False),
                "duration": srd.get("duration", ""),
                "concentration": srd.get("concentration", False),
                "casting_time": srd.get("casting_time", ""),
                "school": (srd.get("school") or {}).get("name", ""),
                "attack_type": srd.get("attack_type", ""),
                "damage": srd.get("damage"),
            }

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
        desc = " ".join(item.get("desc", []))
        result.append({"name": name, "rarity": rarity, "description": desc})
    return result


def enrich_features(feature_list: list[str], class_name: str = "", level: int = 0, mods: dict = None, class_levels: dict = None) -> list[dict]:
    """Add SRD descriptions to feature names, and track limited-use abilities.
    When class_levels dict provided, uses per-class levels for multiclass limited uses."""
    enriched = []
    for feat_str in feature_list:
        if ": " in feat_str:
            level_part, name = feat_str.split(": ", 1)
        else:
            level_part, name = feat_str, feat_str
        key = name.lower()
        desc = FEATURE_DESCRIPTIONS.get(key, "")
        entry = {"name": name, "level": level_part, "description": desc}
        # Determine source class + level for limited-use computation
        source_class = None
        source_level = 0
        if class_levels and len(class_levels) > 1:
            # Multiclass: infer source from feature context or use primary
            for cls_name in class_levels:
                if cls_name.lower() in key or key in cls_name.lower():
                    source_class = cls_name
                    source_level = class_levels[cls_name]
                    break
            if not source_class:
                source_class = class_name
                source_level = class_levels.get(class_name, level)
        else:
            source_class = class_name
            source_level = level
        # Check limited-use features
        if source_class and source_level > 0:
            for lkey, lu in LIMITED_USE.items():
                if lkey in key or key.startswith(lkey) or lkey.startswith(key):
                    uses_max = get_uses_for_level(lkey, source_class, source_level)
                    if uses_max > 0:
                        if lkey == "divine sense":
                            cha_mod = (mods or {}).get("charisma", 0)
                            uses_max = max(1, uses_max + cha_mod)
                        entry["uses_max"] = uses_max
                        entry["uses"] = uses_max
                        entry["recharge"] = lu["recharge"]
                    break
        # Check if this feature is a combat action
        # Strip use-count suffix for matching (e.g. "Action Surge (2 uses)" -> "action surge")
        import re
        _clean_key = re.sub(r'\s*\(\d+\s+uses?(?:\s+per\s+rest)?\s*\)\s*$', '', key, flags=re.IGNORECASE).strip()
        action_info = FEATURE_ACTION_TYPES.get(_clean_key) or FEATURE_ACTION_TYPES.get(key)
        if action_info:
            entry["action_type"] = action_info[0]
            entry["action_desc"] = action_info[1]
        enriched.append(entry)
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
    enriched_features = enrich_features(raw_features, class_name=class_name, level=level, mods=mods)

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


# ── Item search & description endpoints ──────────────────────────────────────

@app.get("/api/items/search", response_class=JSONResponse)
async def search_items(q: str = "", limit: int = 12):
    """Search equipment + magic items by name (fuzzy prefix match)."""
    if not q or len(q.strip()) < 2:
        return JSONResponse({"results": []})
    query = q.strip().lower()
    results = []
    for key, item in ITEM_INDEX.items():
        if query in key:
            results.append({
                "name": item["name"],
                "type": item["type"],
                "rarity": item.get("rarity", ""),
            })
            if len(results) >= limit:
                break
    # Boost exact matches to top
    results.sort(key=lambda r: (0 if r["name"].lower().startswith(query) else 1, r["name"]))
    return JSONResponse({"results": results[:limit], "total": len(ITEM_INDEX)})


@app.get("/api/items/describe", response_class=JSONResponse)
async def describe_item(name: str = ""):
    """Get full description and metadata for a single item."""
    if not name or not name.strip():
        return JSONResponse({"error": "No item name provided"}, status_code=400)
    key = name.strip().lower()
    item = ITEM_INDEX.get(key)
    if not item:
        # Try partial match
        for k, v in ITEM_INDEX.items():
            if key in k:
                item = v
                break
    if not item:
        return JSONResponse({"name": name, "description": "No description available.", "type": "Unknown"})
    return JSONResponse(item)


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
