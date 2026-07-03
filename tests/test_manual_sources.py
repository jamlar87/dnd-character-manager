"""Test: validate all manual data sources have clean (Manual, pg#) format."""
import json, re, pytest
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data" / "manual_data"

# Known slug → display name (must stay in sync with main.py)
SLUG_DISPLAYS = {
    "AIPG": "Adventures in Middle-earth Player's Guide",
    "AW": "Ancestral Weapons", "BLRG": "Bree-land Region Guide",
    "CC": "Creature Codex", "CSF": "Courts of the Shadow Fey",
    "CotN": "Call of the Netherdeep", "DD": "Dues for the Dead",
    "DDP": "Defiance in Phlan", "DMG": "Dungeon Master's Guide",
    "DPM": "Deep Magic: Elven High Magic", "DPM1": "Deep Magic: Ley Lines",
    "DTCOE": "Tasha's Cauldron of Everything", "EBT": "Book of Ebon Tides",
    "EEPC": "Elemental Evil Player's Companion", "EGW": "Explorer's Guide to Wildemount",
    "EIA": "Encounters in Avernus", "EREA": "Erebor Adventures",
    "ERIA": "Eriador Adventures", "ETR": "Expanding the Ranger",
    "GGR": "Guildmasters' Guide to Ravnica", "GoS": "Ghosts of Saltmarsh",
    "HotDQ": "Hoard of the Dragon Queen", "KW": "Kobold Quarterly 20",
    "LMG": "Adventures in Middle-earth Loremaster's Guide",
    "LMRG": "Lonely Mountain Region Guide", "LMoP": "Lost Mine of Phandelver",
    "MM": "Monster Manual", "MOM": "Marauders of the Margreve",
    "MPG": "Margreve Player's Guide", "MTF": "Mordenkainen's Tome of Foes",
    "MWC": "Mirkwood Campaign", "PHB": "Player's Handbook",
    "RAT": "Ratatosk", "RGEO": "The Road Goes Ever On",
    "RRG": "Rhovanion Region Guide", "RVR": "Rivendell Region Guide",
    "RoT": "The Rise of Tiamat", "SCAG": "Sword Coast Adventurer's Guide",
    "SDQ": "Shadows of the Dusk Queen", "SME": "Saltmarsh Encounters",
    "SOM": "Shadows over the Moonsea", "SSK": "Secrets of Sokol Keep",
    "TCE": "Tasha's Cauldron of Everything", "TCSR": "Tal'Dorei Campaign Setting Reborn",
    "TFS": "Tales from the Shadows", "TLT": "The Tortured Land",
    "TMFRV": "Tales of the Margreve", "TTP": "The Tortle Package",
    "ToA": "Tomb of Annihilation", "VGM": "Volo's Guide to Monsters",
    "W": "Wrath of the Bramble King", "W1": "Pride of the Mushroom Queen",
    "W2": "Warlock 7", "W3": "Warlock 17", "W4": "Warlock 22: Druids",
    "W5": "Warlock 32", "W6": "Warlock 34", "W7": "Warlock Bestiary",
    "W8": "Warlock Lair: The Returners' Tower", "W9": "Warlock Lair: The Dark Aerie",
    "WDH": "Waterdeep: Dragon Heist", "WGE": "Wayfinder's Guide to Eberron",
    "WLA": "Wilderland Adventures", "WLL": "Warlock Lairs: Into the Wilds",
    "WRKF": "Wrath of the River King", "WS": "Shadows Envy",
    "WSC": "The Wild Sheep Chase", "XGE": "Xanathar's Guide to Everything",
    "BGDIA": "Baldur's Gate: Descent into Avernus",
}

SLUG_LOOKUP = {k.upper(): v for k, v in SLUG_DISPLAYS.items()}

PDF_PAGE_RANGES = {
    "MM": 354, "DMG": 320, "PHB": 322, "XGE": 195,
    "MTF": 258, "VGM": 226, "GGR": 258, "EGW": 307,
    "CotN": 226, "TCSR": 283, "ToA": 260, "LMoP": 64,
    "WDH": 228, "HotDQ": 97, "RoT": 98, "TTP": 28,
    "SCAG": 160, "EEPC": 32, "WGE": 224, "TCE": 256,
    "CC": 426, "EBT": 257, "CSF": 151, "TMFRV": 206,
    "MPG": 64, "TFS": 196, "SME": 22, "SOM": 100,
    "WRKF": 70, "WLL": 180, "W2": 30, "W5": 32,
    "W7": 38, "EIA": 37, "TLT": 32, "SDQ": 26,
    "DD": 25, "RRG": 144, "ERIA": 144, "BGDIA": 256,
    "GoS": 256, "AIPG": 256, "BLRG": 160, "LMRG": 160,
    "EREA": 200, "RVR": 160, "WLA": 200, "MWC": 200,
    "KW": 100, "DPM1": 100, "AW": 50, "W": 9,
    "W1": 9, "W3": 30, "W4": 30, "W6": 30,
    "MOM": 15, "WSC": 50, "WS": 9, "W8": 7,
    "W9": 7, "LMG": 256, "RAT": 50, "RGEO": 100,
    "ETR": 50, "DDP": 50, "SSK": 50, "SME": 22,
    "DPM": 100, "WLL": 180,
}

CLEAN_FORMAT = re.compile(r'^\([A-Za-z][^)]+\)$')
PAGE_FORMAT = re.compile(r'^\(([^,]+),\s*p\.(\d+)\)$')
NO_PAGE_FORMAT = re.compile(r'^\(([^)]+)\)$')

FILES = [
    "races.json", "spells.json", "magic_items.json", "equipment.json",
    "monsters.json", "npcs.json", "feats.json", "backgrounds.json",
    "subclasses.json",
    "traps.json",
]


def _entries(path: Path):
    """Yield (name, source, slug) for every entry in a manual data file."""
    data = json.loads(path.read_text())
    items = list(data.values()) if isinstance(data, dict) else data
    for entry in items:
        yield (
            entry.get("name", "?"),
            entry.get("source", ""),
            (entry.get("_source_manual") or "").strip(),
        )


@pytest.mark.parametrize("filename", FILES)
def test_all_sources_have_clean_format(filename):
    """Every entry with a source must use (Manual) or (Manual, p.#) format."""
    path = DATA_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not found")
    
    bad = []
    for name, src, slug in _entries(path):
        if not src:
            continue  # No source is OK (homebrew, dropped garbage)
        if not CLEAN_FORMAT.match(src):
            bad.append(f"{name}: '{src}'")
    
    assert not bad, f"{filename}: {len(bad)} bad-format sources:\n" + "\n".join(bad[:10])


@pytest.mark.parametrize("filename", FILES)
def test_page_numbers_within_range(filename):
    """Page numbers must not exceed the PDF's total page count."""
    path = DATA_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not found")
    
    bad = []
    for name, src, slug in _entries(path):
        if not src:
            continue
        m = PAGE_FORMAT.match(src)
        if m:
            page = int(m.group(2))
            max_p = PDF_PAGE_RANGES.get(slug.upper())
            if max_p and page > max_p:
                bad.append(f"{name}: page={page}, max={max_p} ({slug})")
            if page <= 0:
                bad.append(f"{name}: page={page} (≤0)")
    
    assert not bad, f"{filename}: {len(bad)} out-of-range page numbers:\n" + "\n".join(bad)


@pytest.mark.parametrize("filename", FILES)
def test_slug_display_names_match(filename):
    """Display names in source must match the slug→display map."""
    path = DATA_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not found")
    
    bad = []
    for name, src, slug in _entries(path):
        if not src or not slug:
            continue
        m = PAGE_FORMAT.match(src)
        if not m:
            m = NO_PAGE_FORMAT.match(src)
        if m:
            display = m.group(1)
            expected = SLUG_LOOKUP.get(slug.upper())
            if expected and display != expected:
                bad.append(f"{name}: slug={slug}, display='{display}', expected='{expected}'")
    
    assert not bad, f"{filename}: {len(bad)} display-name mismatches:\n" + "\n".join(bad)
