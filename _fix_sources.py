"""Normalize all source fields to (Manual, pg#) format — v2 with case-insensitive slug lookup."""
import json, re, os
from pathlib import Path

DATA_DIR = Path("data/manual_data")

# ── Slug → display name (mixed-case keys as they appear in data _source_manual) ──
SLUG_DISPLAYS = {
    "AIPG": "Adventures in Middle-earth Player's Guide",
    "AW": "Ancestral Weapons",
    "BLRG": "Bree-land Region Guide",
    "CC": "Creature Codex",
    "CSF": "Courts of the Shadow Fey",
    "CotN": "Call of the Netherdeep",
    "DD": "Dues for the Dead",
    "DDP": "Defiance in Phlan",
    "DMG": "Dungeon Master's Guide",
    "DPM": "Deep Magic: Elven High Magic",
    "DPM1": "Deep Magic: Ley Lines",
    "DTCOE": "Tasha's Cauldron of Everything",
    "EBT": "Book of Ebon Tides",
    "EEPC": "Elemental Evil Player's Companion",
    "EGW": "Explorer's Guide to Wildemount",
    "EIA": "Encounters in Avernus",
    "EREA": "Erebor Adventures",
    "ERIA": "Eriador Adventures",
    "ETR": "Expanding the Ranger",
    "GGR": "Guildmasters' Guide to Ravnica",
    "HotDQ": "Hoard of the Dragon Queen",
    "KW": "Kobold Quarterly 20",
    "LMG": "Adventures in Middle-earth Loremaster's Guide",
    "LMRG": "Lonely Mountain Region Guide",
    "LMoP": "Lost Mine of Phandelver",
    "MM": "Monster Manual",
    "MOM": "Marauders of the Margreve",
    "MPG": "Margreve Player's Guide",
    "MTF": "Mordenkainen's Tome of Foes",
    "MWC": "Mirkwood Campaign",
    "PHB": "Player's Handbook",
    "RAT": "Ratatosk",
    "RGEO": "The Road Goes Ever On",
    "RRG": "Rhovanion Region Guide",
    "RVR": "Rivendell Region Guide",
    "RoT": "The Rise of Tiamat",
    "SCAG": "Sword Coast Adventurer's Guide",
    "SDQ": "Shadows of the Dusk Queen",
    "SME": "Saltmarsh Encounters",
    "SOM": "Shadows over the Moonsea",
    "SSK": "Secrets of Sokol Keep",
    "TCE": "Tasha's Cauldron of Everything",
    "TCSR": "Tal'Dorei Campaign Setting Reborn",
    "TFS": "Tales from the Shadows",
    "TLT": "The Tortured Land",
    "TMFRV": "Tales of the Margreve",
    "TTP": "The Tortle Package",
    "ToA": "Tomb of Annihilation",
    "VGM": "Volo's Guide to Monsters",
    "W": "Wrath of the Bramble King",
    "W1": "Pride of the Mushroom Queen",
    "W2": "Warlock 7",
    "W3": "Warlock 17",
    "W4": "Warlock 22: Druids",
    "W5": "Warlock 32",
    "W6": "Warlock 34",
    "W7": "Warlock Bestiary",
    "W8": "Warlock Lair: The Returners' Tower",
    "W9": "Warlock Lair: The Dark Aerie",
    "WDH": "Waterdeep: Dragon Heist",
    "WGE": "Wayfinder's Guide to Eberron",
    "WLA": "Wilderland Adventures",
    "WLL": "Warlock Lairs: Into the Wilds",
    "WRKF": "Wrath of the River King",
    "WS": "Shadows Envy",
    "WSC": "The Wild Sheep Chase",
    "XGE": "Xanathar's Guide to Everything",
}

# Build case-insensitive lookup: uppercase version → display
SLUG_LOOKUP = {k.upper(): v for k, v in SLUG_DISPLAYS.items()}

# Source text → slug override for entries without _source_manual
SOURCE_TO_SLUG = {
    "player's handbook": "PHB",
    "dungeon master's guide": "DMG",
    "xanathar's guide to everything": "XGE",
    "sword coast adventurer's guide": "SCAG",
    "elemental evil player's companion": "EEPC",
    "guildmasters' guide to ravnica": "GGR",
    "explorer's guide to wildemount": "EGW",
    "tal'dorei campaign setting: reborn": "TCSR",
    "tomb of annihilation": "ToA",
    "lost mine of phandelver": "LMoP",
    "waterdeep: dragon heist": "WDH",
    "hoard of the dragon queen": "HotDQ",
    "the rise of tiamat": "RoT",
    "volo's guide to monsters": "VGM",
    "mordenkainen's tome of foes": "MTF",
    "call of the netherdeep": "CotN",
    "eberron: rising from the last war": "WGE",
    "wayfinder's guide to eberron": "WGE",
    "creature codex": "CC",
    "tome of beasts": "CC",
    "book of ebon tides": "EBT",
    "adventures in middle-earth": "AIPG",
    "marauders of the margreve": "MOM",
    "tales of the margreve": "TMFRV",
    "courts of the shadow fey": "CSF",
    "bree-land region guide": "BLRG",
    "erebor adventures": "EREA",
    "eriador adventures": "ERIA",
    "rhovanion region guide": "RRG",
    "rivendell region guide": "RVR",
    "lonely mountain region guide": "LMRG",
    "mirkwood campaign": "MWC",
    "wilderland adventures": "WLA",
    "tortle package": "TTP",
    "warlock lairs": "WLL",
    "warlock bestiary": "W7",
    "kobold quarterly": "KW",
    "shadows over the moonsea": "SOM",
    "saltmarsh encounters": "SME",
    "tales from the shadows": "TFS",
    "wrath of the river king": "WRKF",
    "shadows of the dusk queen": "SDQ",
    "ancestral weapons": "AW",
    "margreve player's guide": "MPG",
    "deep magic": "DPM1",
    "the wild sheep chase": "WSC",
    "dragon season": "WLL",
    "midgard worldbook": "MOM",
    "the tortured land": "TLT",
    "dues for the dead": "DD",
    "pride of the mushroom queen": "W1",
    "shadows envy": "WS",
    "warlock 7": "W2",
    "warlock 17": "W3",
    "warlock 22": "W4",
    "warlock 32": "W5",
    "warlock 34": "W6",
    "winter wizardry": "W",
    "winter 2012": "KW",
    "tasha's cauldron": "TCE",
    "mythic odysseys of theros": "TCE",
    "baldur's gate": "TCE",
    "descent into avernus": "TCE",
    # Short codes
    "cotn": "CotN", "egw": "EGW", "tcsr": "TCSR",
    "ttp": "TTP", "wge": "WGE", "phb": "PHB", "dmg": "DMG",
    "xge": "XGE", "scag": "SCAG", "ggr": "GGR", "toa": "ToA",
    "lmop": "LMoP", "wdh": "WDH", "hotdq": "HotDQ", "rot": "RoT",
    "vgm": "VGM", "mtf": "MTF", "tce": "TCE", "cc": "CC",
    "mom": "MOM", "eepc": "EEPC", "tcsr": "TCSR",
}

PAGE_RE = re.compile(r'(?:p\.?\s*|page\s+)(\d+)(?:\s*[-–]\s*(\d+))?', re.IGNORECASE)
PAGE_ONLY_RE = re.compile(r'^\s*(?:p\.?\s*|page\s+)?(\d+)\s*(?:[-–]\s*(\d+))?\s*$')

GARBAGE_PATTERNS = [
    "unknown", "introductory", "homebrew", "fragment",
    "james larsen", "text provided", "page not determinable",
    "sourcebook", "not determinable",
]


def extract_page(source: str) -> str | None:
    if not source:
        return None
    source = source.strip()
    m = PAGE_RE.search(source)
    if m:
        return f"p.{m.group(1)}"
    m2 = PAGE_ONLY_RE.match(source)
    if m2:
        pg = m2.group(1)
        if 1 <= int(pg) <= 999:
            return f"p.{pg}"
    return None


def get_display(slug: str) -> str | None:
    """Look up display name case-insensitively."""
    if not slug:
        return None
    return SLUG_LOOKUP.get(slug.upper())


def normalize_source(old_source: str, slug: str | None) -> str:
    display = get_display(slug)
    page = extract_page(old_source)
    
    if display and page:
        return f"({display}, {page})"
    elif display:
        return f"({display})"
    elif page:
        return f"(Unknown Source, {page})"
    return old_source if (old_source and "Unknown" not in old_source) else ""


def resolve_slug(entry: dict) -> str | None:
    slug = (entry.get("_source_manual") or "").strip()
    if slug:
        return slug
    src = (entry.get("source") or "").strip()
    if not src:
        return None
    src_lower = src.lower().strip()
    if src_lower in SOURCE_TO_SLUG:
        return SOURCE_TO_SLUG[src_lower]
    for key, val in sorted(SOURCE_TO_SLUG.items(), key=lambda x: -len(x[0])):
        if key in src_lower:
            return val
    return None


def is_garbage(source: str) -> bool:
    if not source:
        return True
    s = source.lower()
    return any(p in s for p in GARBAGE_PATTERNS)


FILES = [
    "magic_items.json", "npcs.json", "equipment.json", "spells.json",
    "feats.json", "backgrounds.json", "races.json", "subclasses.json",
    "monsters.json",
]

total_changed = 0
total_dropped = 0

for fn in FILES:
    path = DATA_DIR / fn
    data = json.load(open(path))
    items = list(data.values()) if isinstance(data, dict) else data
    is_dict = isinstance(data, dict)
    
    changed = 0
    dropped = 0
    already = 0
    
    for entry in items:
        old_source = (entry.get("source") or "").strip()
        if not old_source or old_source == "Unknown":
            old_source = ""
        
        slug = resolve_slug(entry)
        new_source = normalize_source(old_source, slug)
        
        if new_source and new_source != old_source:
            entry["source"] = new_source
            changed += 1
        elif not new_source and old_source:
            if is_garbage(old_source):
                entry["source"] = ""
                dropped += 1
            else:
                already += 1  # Already looks clean enough
        elif new_source == old_source:
            already += 1
        else:
            already += 1
    
    if changed or dropped:
        if is_dict:
            keys = list(data.keys())
            for k, v in zip(keys, items):
                data[k] = v
        json.dump(data, open(path, "w"), indent=2, ensure_ascii=False)
    
    total_changed += changed
    total_dropped += dropped
    
    print(f"{fn}: changed={changed}, dropped={dropped}, clean_already={already}")

print(f"\nTotal: {total_changed} changed, {total_dropped} dropped")
