#!/usr/bin/env python3
"""
Build an item→page mapping by searching D&D PDFs with pymupdf.
Reads the ITEM_INDEX data files, finds which PDF each item belongs to,
and searches for the item name to find its page number.

Output: data/item_page_map.json
  {item_key: {"page": N, "source_book": "PHB", "source_str": "PHB 2014 p.149"}}
"""
import json
import re
import sys
from pathlib import Path
from collections import defaultdict

try:
    import fitz
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False
    print("WARNING: pymupdf not available, will only use known page data", file=sys.stderr)

# ── Config ──────────────────────────────────────────────────────────────────
DATA_DIR = Path("/home/james/dnd-character-manager/data")
MANUALS_DIR = Path("/media/james/SlowDisk1tb/home-move/DnD-Manuals")
OUTPUT_PATH = DATA_DIR / "item_page_map.json"

# Source string → (pdf_path, display_prefix) mapping
SOURCE_TO_PDF = {
    "PHB 2014": ("D&D 5E - Player's Handbook.pdf", "PHB 2014"),
    "DMG 2014": ("D&D 5E - Dungeon Master's Guide.pdf", "DMG 2014"),
    "Player's Handbook": ("D&D 5E - Player's Handbook.pdf", "PHB 2014"),
    "Dungeon Master's Guide": ("D&D 5E - Dungeon Master's Guide.pdf", "DMG 2014"),
    "Tomb of Annihilation": ("Campaigns/D&D 5E - Tomb of Annihilation.pdf", "ToA"),
    "Sword Coast Adventurer's Guide": ("D&D 5E - Sword Coast Adventurer's Guide.pdf", "SCAG"),
    "Waterdeep: Dragon Heist": ("Campaigns/D&D 5E - Waterdeep - Dragon Heist.pdf", "WDH"),
    "Xanathar's Guide to Everything": ("D&D 5E - Xanathar's Guide to Everything.pdf", "XGE"),
    "Guildmasters' Guide to Ravnica": ("D&D 5E - Guildmasters' Guide to Ravnica.pdf", "GGR"),
    "Mordenkainen's Tome of Foes": ("D&D 5E - Mordenkainen's Tome of Foes.pdf", "MTF"),
    "Volo's Guide to Monsters": ("D&D 5E - Volo's Guide to Monsters.pdf", "VGM"),
    "Lost Mine of Phandelver": ("Campaigns/D&D 5E - Lost Mine of Phandelver.pdf", "LMoP"),
    "The Rise of Tiamat": ("Campaigns/D&D 5E - Tyranny of Dragons - The Rise of Tiamat.pdf", "RoT"),
    "Hoard of the Dragon Queen": ("Campaigns/D&D 5E - Tyranny of Dragons - Hoard of the Dragon Queen.pdf", "HotDQ"),
    "The Tortle Package": ("D&D 5E - The Tortle Package.pdf", "TTP"),
    "Elemental Evil Player's Companion": ("D&D 5E - Elemental Evil Player's Companion.pdf", "EEPC"),
    "Wayfinder's Guide to Eberron": ("D&D 5E - Wayfinders Guide to Eberron.pdf", "WGE"),
}

# Source strings that already have page numbers — skip
HAS_PAGE_RE = re.compile(r'\b[pP]\.?\s*\d+')

# Items to skip (too generic, would match everywhere)
SKIP_NAMES = {
    "arrow", "arrows", "crossbow bolt", "crossbow bolts",
    "blowgun needle", "blowgun needles", "sling bullet", "sling bullets",
}

# Items with known page numbers from common knowledge / manual data
# These override the search results
KNOWN_PAGES_PHB = {
    # Armor table: PHB p.145
    "padded": 145, "leather": 145, "studded leather": 145,
    "hide": 145, "chain shirt": 145, "scale mail": 145, "breastplate": 145,
    "half plate": 145, "ring mail": 145, "chain mail": 145, "splint": 145,
    "plate": 145, "shield": 145,
    # Weapons table: PHB p.149
    "club": 149, "dagger": 149, "greatclub": 149, "handaxe": 149,
    "javelin": 149, "light hammer": 149, "mace": 149, "quarterstaff": 149,
    "sickle": 149, "spear": 149, "crossbow, light": 149, "dart": 149,
    "shortbow": 149, "sling": 149, "battleaxe": 149, "flail": 149,
    "glaive": 149, "greataxe": 149, "greatsword": 149, "halberd": 149,
    "lance": 149, "longsword": 149, "maul": 149, "morningstar": 149,
    "pike": 149, "rapier": 149, "scimitar": 149, "shortsword": 149,
    "trident": 149, "war pick": 149, "warhammer": 149, "whip": 149,
    "blowgun": 149, "crossbow, hand": 149, "crossbow, heavy": 149,
    "longbow": 149, "net": 149,
    # Adventuring Gear: PHB pp.150-153
    "abacus": 150, "acid (vial)": 150, "alchemist's fire (flask)": 150,
    "alchemist's supplies": 154, "antitoxin (vial)": 150,
    "archery target": 150, "backpack": 150, "bag of ball bearings": 150,
    "ball bearings (bag of 1,000)": 150, "barrel": 150, "basket": 150,
    "bedroll": 150, "bell": 150, "blanket": 150, "block and tackle": 150,
    "book": 150, "bottle, glass": 150, "bucket": 150,
    "burglar's pack": 151, "caltrops": 150, "candle": 150,
    "case, crossbow bolt": 150, "case, map or scroll": 150,
    "chain (10 feet)": 150, "chalk (1 piece)": 150,
    "chest": 150, "climber's kit": 150, "clothes, common": 150,
    "clothes, costume": 150, "clothes, fine": 150, "clothes, traveler's": 150,
    "component pouch": 150, "crowbar": 150,
    "diplomat's pack": 151, "dungeoneer's pack": 151,
    "entertainer's pack": 151, "explorer's pack": 151,
    "fishing tackle": 150, "flask or tankard": 150,
    "grappling hook": 150, "hammer": 150, "hammer, sledge": 150,
    "healer's kit": 150, "holy water (flask)": 150,
    "hourglass": 150, "hunting trap": 150,
    "ink (1 ounce bottle)": 150, "ink pen": 150,
    "jug or pitcher": 150, "ladder (10-foot)": 150,
    "lamp": 150, "lantern, bullseye": 150, "lantern, hooded": 150,
    "lock": 150, "magnifying glass": 150, "manacles": 150,
    "mess kit": 150, "mirror, steel": 150,
    "oil (flask)": 150, "paper (one sheet)": 150,
    "parchment (one sheet)": 150, "perfume (vial)": 150,
    "pick, miner's": 150, "piton": 150,
    "poison, basic (vial)": 150, "pole (10-foot)": 150,
    "pot, iron": 150, "potion of healing": 150,
    "pouch": 150, "priest's pack": 151,
    "quiver": 150, "ram, portable": 150, "rations (1 day)": 150,
    "robes": 150, "rope, hempen (50 feet)": 150,
    "rope, silk (50 feet)": 150, "sack": 150,
    "scale, merchant's": 150, "scholar's pack": 151,
    "sealing wax": 150, "shovel": 150,
    "signal whistle": 150, "signet ring": 150, "soap": 150,
    "spellbook": 150, "spikes, iron (10)": 150, "spyglass": 150,
    "tent, two-person": 150, "tinderbox": 150,
    "torch": 150, "vial": 150, "waterskin": 150,
    "whetstone": 150,
    # Tools: PHB p.154
    "artisan's tools": 154, "disguise kit": 154,
    "forgery kit": 154, "gaming set": 154,
    "herbalism kit": 154, "musical instrument": 154,
    "navigator's tools": 154, "poisoner's kit": 154,
    "thieves' tools": 154,
    # Mounts & Vehicles: PHB p.157
    "camel": 157, "donkey": 157, "mule": 157,
    "elephant": 157, "horse, draft": 157, "horse, riding": 157,
    "mastiff": 157, "pony": 157, "warhorse": 157,
    # Food, Drink, Lodging: PHB p.158
    "ale (gallon)": 158, "ale (mug)": 158,
    "banquet (per person)": 158, "bread, loaf": 158,
    "cheese, hunk": 158, "inn stay (squalid)": 158,
    "inn stay (poor)": 158, "inn stay (modest)": 158,
    "inn stay (comfortable)": 158, "inn stay (wealthy)": 158,
    "inn stay (aristocratic)": 158,
    "meals (squalid)": 158, "meals (poor)": 158,
    "meals (modest)": 158, "meals (comfortable)": 158,
    "meals (wealthy)": 158, "meals (aristocratic)": 158,
    "meat, chunk": 158, "wine, common (pitcher)": 158,
    "wine, fine (bottle)": 158,
}

# DMG magic item page ranges (approximate based on alphabetical listing)
# Items A-Z span DMG pp.150-214
KNOWN_PAGES_DMG = {
    # These are actually well-known from the DMG layout
    "adamantine armor": 150, "ammunition, +1, +2, or +3": 150,
    "amulet of health": 150, "amulet of proof against detection and location": 150,
    "amulet of the planes": 150, "animated shield": 151,
    "apparatus of kwalish": 151, "armor of invulnerability": 152,
    "armor of resistance": 152, "armor of vulnerability": 152,
    "armor, +1, +2, or +3": 152, "arrow of slaying": 152,
    "arrow-catching shield": 152, "bag of beans": 152,
    "bag of devouring": 153, "bag of holding": 153,
    "bag of tricks": 154, "bead of force": 154,
    "belt of dwarvenkind": 155, "belt of giant strength": 155,
    "berserker axe": 155, "boots of elvenkind": 155,
    "boots of levitation": 155, "boots of speed": 155,
    "boots of striding and springing": 156, "boots of the winterlands": 156,
    "bowl of commanding water elementals": 156,
    "bracers of archery": 156, "bracers of defense": 156,
    "brazier of commanding fire elementals": 156,
    "brooch of shielding": 156, "broom of flying": 156,
    "candle of invocation": 157, "cape of the mountebank": 157,
    "carpet of flying": 157, "censer of controlling air elementals": 158,
    "chime of opening": 158, "circlet of blasting": 158,
    "cloak of arachnida": 158, "cloak of displacement": 158,
    "cloak of elvenkind": 158, "cloak of protection": 159,
    "cloak of the bat": 159, "cloak of the manta ray": 159,
    "crystal ball": 159, "cube of force": 159,
    "cubic gate": 160, "dagger of venom": 160,
    "dancing sword": 161, "decanter of endless water": 161,
    "deck of illusions": 161, "deck of many things": 162,
    "defender": 164, "demon armor": 165,
    "dimensional shackles": 165, "dragon scale mail": 165,
    "dragon slayer": 166, "dust of disappearance": 166,
    "dust of dryness": 166, "dust of sneezing and choking": 166,
    "dwarven plate": 167, "dwarven thrower": 167,
    "efficient quiver": 167, "efreeti bottle": 167,
    "elemental gem": 167, "elven chain": 168,
    "eversmoking bottle": 168, "eyes of charming": 168,
    "eyes of minute seeing": 168, "eyes of the eagle": 168,
    "feather token": 168, "figurine of wondrous power": 169,
    "flame tongue": 170, "folding boat": 170,
    "frost brand": 171, "gauntlets of ogre power": 171,
    "gem of brightness": 171, "gem of seeing": 172,
    "giant slayer": 172, "glamoured studded leather": 172,
    "gloves of missile snaring": 172, "gloves of swimming and climbing": 172,
    "goggles of night": 172, "hammer of thunderbolts": 173,
    "handy haversack": 174, "hat of disguise": 174,
    "headband of intellect": 174, "helm of brilliance": 174,
    "helm of comprehending languages": 175, "helm of telepathy": 175,
    "helm of teleportation": 175, "holy avenger": 175,
    "horn of blasting": 175, "horn of valhalla": 175,
    "horseshoes of a zephyr": 175, "horseshoes of speed": 175,
    "immovable rod": 175, "instrument of the bards": 176,
    "ioun stone": 176, "iron bands of bilarro": 177,
    "iron flask": 177, "javelin of lightning": 178,
    "lantern of revealing": 179, "luck blade": 179,
    "mace of disruption": 179, "mace of smiting": 179,
    "mace of terror": 180, "mantle of spell resistance": 180,
    "manual of bodily health": 180, "manual of gainful exercise": 180,
    "manual of golems": 180, "manual of quickness of action": 181,
    "marvelous pigments": 183, "medallion of thoughts": 183,
    "mirror of life trapping": 183, "mithral armor": 184,
    "necklace of adaptation": 184, "necklace of fireballs": 184,
    "nine lives stealer": 184, "oathbow": 185,
    "oil of etherealness": 185, "oil of sharpness": 185,
    "oil of slipperiness": 185, "orb of dragonkind": 185,
    "pearl of power": 185, "periapt of health": 185,
    "periapt of proof against poison": 185, "periapt of wound closure": 185,
    "philter of love": 185, "pipes of haunting": 185,
    "pipes of the sewers": 185, "plate armor of etherealness": 185,
    "portable hole": 186, "potion of animal friendship": 187,
    "potion of clairvoyance": 187, "potion of climbing": 187,
    "potion of diminution": 187, "potion of fire breath": 187,
    "potion of flying": 187, "potion of gaseous form": 187,
    "potion of giant strength": 187, "potion of greater healing": 187,
    "potion of growth": 187, "potion of healing": 187,
    "potion of heroism": 188, "potion of invisibility": 188,
    "potion of mind reading": 188, "potion of poison": 188,
    "potion of resistance": 188, "potion of speed": 188,
    "potion of superior healing": 188, "potion of supreme healing": 188,
    "potion of water breathing": 188,
    "restorative ointment": 188, "ring of animal influence": 189,
    "ring of djinni summoning": 190, "ring of elemental command": 190,
    "ring of evasion": 191, "ring of feather falling": 191,
    "ring of free action": 191, "ring of invisibility": 191,
    "ring of jumping": 191, "ring of mind shielding": 191,
    "ring of protection": 191, "ring of regeneration": 191,
    "ring of resistance": 192, "ring of shooting stars": 192,
    "ring of spell storing": 192, "ring of spell turning": 193,
    "ring of swimming": 193, "ring of telekinesis": 193,
    "ring of the ram": 193, "ring of three wishes": 193,
    "ring of warmth": 193, "ring of water walking": 193,
    "ring of x-ray vision": 193, "robe of eyes": 193,
    "robe of scintillating colors": 193, "robe of stars": 194,
    "robe of the archmagi": 194, "robe of useful items": 195,
    "rod of absorption": 195, "rod of alertness": 196,
    "rod of lordly might": 196, "rod of resurrection": 197,
    "rod of rulership": 197, "rod of security": 197,
    "rope of climbing": 197, "rope of entanglement": 197,
    "scarab of protection": 199, "scimitar of speed": 199,
    "sentinel shield": 199, "shield of missile attraction": 200,
    "shield, +1, +2, or +3": 200, "slippers of spider climbing": 200,
    "sovereign glue": 200, "spell scroll": 200,
    "spellguard shield": 200, "sphere of annihilation": 201,
    "staff of charming": 201, "staff of fire": 201,
    "staff of frost": 202, "staff of healing": 202,
    "staff of power": 202, "staff of striking": 203,
    "staff of swarming insects": 203, "staff of the magi": 203,
    "staff of the python": 204, "staff of the woodlands": 204,
    "staff of thunder and lightning": 204, "staff of withering": 205,
    "stone of controlling earth elementals": 205,
    "stone of good luck (luckstone)": 205,
    "sun blade": 205, "sword of life stealing": 206,
    "sword of sharpness": 206, "sword of vengeance": 206,
    "sword of wounding": 207, "talisman of pure good": 207,
    "talisman of the sphere": 207, "talisman of ultimate evil": 207,
    "tome of clear thought": 208, "tome of leadership and influence": 208,
    "tome of the stilled tongue": 208, "tome of understanding": 208,
    "trident of fish command": 209, "universal solvent": 209,
    "vicious weapon": 209, "vorpal sword": 209,
    "wand of binding": 209, "wand of enemy detection": 210,
    "wand of fear": 210, "wand of fireballs": 210,
    "wand of lightning bolts": 211, "wand of magic detection": 211,
    "wand of magic missiles": 211, "wand of paralysis": 211,
    "wand of polymorph": 211, "wand of secrets": 211,
    "wand of the war mage, +1, +2, or +3": 212,
    "wand of web": 212, "wand of wonder": 212,
    "weapon, +1, +2, or +3": 213, "well of many worlds": 213,
    "wind fan": 213, "winged boots": 214,
    "wings of flying": 214, "wondrous figurine": 169,
}

# Additional specific items from PHB
KNOWN_PAGES_PHB.update({
    # Specific tool sets from PHB p.154 detail
    "alchemist's supplies": 154, "brewer's supplies": 154,
    "calligrapher's supplies": 154, "carpenter's tools": 154,
    "cartographer's tools": 154, "cobbler's tools": 154,
    "cook's utensils": 154, "glassblower's tools": 154,
    "jeweler's tools": 154, "leatherworker's tools": 154,
    "mason's tools": 154, "painter's supplies": 154,
    "potter's tools": 154, "smith's tools": 154,
    "tinker's tools": 154, "weaver's tools": 154,
    "woodcarver's tools": 154,
    # Gaming sets
    "dice set": 154, "dragonchess set": 154,
    "playing card set": 154, "three-dragon ante set": 154,
    # Musical instruments
    "bagpipes": 154, "drum": 154, "dulcimer": 154,
    "flute": 154, "lute": 154, "lyre": 154,
    "horn": 154, "pan flute": 154, "shawm": 154, "viol": 154,
    # Additional adventuring gear p.150-153
    "ammunition": 150,
    "crampons": 150, "crossbow bolt case": 150,
    "map or scroll case": 150,
    # Barding (armor for mounts) — PHB p.155 under Mounts and Vehicles
    "barding: breastplate": 145,
    "barding: chain mail": 145,
    "barding: chain shirt": 145,
    "barding: half plate": 145,
    "barding: hide": 145,
    "barding: leather": 145,
    "barding: padded": 145,
    "barding: plate": 145,
    "barding: ring mail": 145,
    "barding: scale mail": 145,
    "barding: splint": 145,
    "barding: studded leather": 145,
    # Misc PHB items
    "bit and bridle": 157,
    "animal feed (1 day)": 157,
    "block of incense": 151,
    "censer": 151,
    "vestments": 150,
    "blank spellbook": 150,
    "spellbook": 150,
    "exotic saddle": 157,
    "military saddle": 157,
    "pack saddle": 157,
    "riding saddle": 157,
    "saddlebags": 157,
    "carriage": 157,
    "cart": 157,
    "chariot": 157,
    "galley": 157,
    "keelboat": 157,
    "longship": 157,
    "rowboat": 157,
    "sailing ship": 157,
    "warship": 157,
    "feed (per day)": 157,
    "stabling (per day)": 158,
    "candle of the deep": 150,  # from XGE common items but may appear
})

# ── DMG variant aliases ──
# Many DMG items have variants not individually named in the text
DMG_VARIANT_PAGES = {
    # Apparatus variants → p.151
    "apparatus of the crab": 151,
    # Dragon scale mail colors → p.165 (all under "dragon scale mail")
    "black dragon scale mail": 165, "blue dragon scale mail": 165,
    "brass dragon scale mail": 165, "bronze dragon scale mail": 165,
    "copper dragon scale mail": 165, "gold dragon scale mail": 165,
    "green dragon scale mail": 165, "red dragon scale mail": 165,
    "silver dragon scale mail": 165, "white dragon scale mail": 165,
    # Belt of giant strength variants → p.155
    "belt of hill giant strength": 155,
    "belt of stone giant strength": 155,
    "belt of frost giant strength": 155,
    "belt of fire giant strength": 155,
    "belt of cloud giant strength": 155,
    "belt of storm giant strength": 155,
    # Elemental gem variants → p.167
    "air elemental gem": 167, "earth elemental gem": 167,
    "fire elemental gem": 167, "water elemental gem": 167,
    # Feather token variants → p.168
    "anchor feather token": 168, "bird feather token": 168,
    "fan feather token": 168, "swan boat feather token": 168,
    "tree feather token": 168, "whip feather token": 168,
    # Figurine of wondrous power variants → p.169
    "bronze griffon": 169, "ebony fly": 169,
    "golden lions": 169, "ivory goats": 169,
    "marble elephant": 169, "obsidian steed": 169,
    "onyx dog": 169, "serpentine owl": 169,
    "silver raven": 169,
    # Potion variants (some have specific entries, others are variants)
    "potion of frost giant strength": 187,
    "potion of stone giant strength": 187,
    "potion of fire giant strength": 187,
    "potion of cloud giant strength": 187,
    "potion of storm giant strength": 187,
    "potion of hill giant strength": 187,
    # Armor resistance variants → p.152
    "armor of cold resistance": 152,
    "armor of fire resistance": 152,
    "armor of force resistance": 152,
    "armor of lightning resistance": 152,
    "armor of necrotic resistance": 152,
    "armor of poison resistance": 152,
    "armor of psychic resistance": 152,
    "armor of radiant resistance": 152,
    "armor of thunder resistance": 152,
    # Ring resistance variants → p.192
    "ring of acid resistance": 192,
    "ring of cold resistance": 192,
    "ring of fire resistance": 192,
    "ring of force resistance": 192,
    "ring of lightning resistance": 192,
    "ring of necrotic resistance": 192,
    "ring of poison resistance": 192,
    "ring of psychic resistance": 192,
    "ring of radiant resistance": 192,
    "ring of thunder resistance": 192,
    # Ammunition variants → p.150
    "arrow +1": 150, "arrow +2": 150, "arrow +3": 150,
    "crossbow bolt +1": 150, "crossbow bolt +2": 150, "crossbow bolt +3": 150,
    "sling bullet +1": 150, "sling bullet +2": 150, "sling bullet +3": 150,
    # Common magic items (XGE pp.136-140, but some in DMG)
    "armor of gleaming": 136,  # XGE p.136
    "bead of nourishment": 136,  # XGE p.136
    "bead of refreshment": 136,  # XGE p.136
    "boots of false tracks": 136,  # XGE p.136
    "candle of the deep": 136,  # XGE p.136
    "cape of billowing": 136,  # XGE p.136
    "cast-off armor": 136,  # XGE p.136
    "charlatan's die": 136,  # XGE p.136
    "cloak of billowing": 136,  # XGE p.136
    "cloak of many fashions": 136,  # XGE p.136
    "clockwork amulet": 137,  # XGE p.137
    "clothes of mending": 137,  # XGE p.137
    "dark shard amulet": 137,  # XGE p.137
    "dread helm": 137,  # XGE p.137
    "ear horn of hearing": 137,  # XGE p.137
    "enduring spellbook": 137,  # XGE p.137
    "ersatz eye": 137,  # XGE p.137
    "hat of wizardry": 137,  # XGE p.137
    "hat of vermin": 137,  # XGE p.137
    "hew": 192,  # DMG p.192 — "Hew" is a specific weapon
    "hew (battleaxe)": 192,
    "instrument of illusions": 137,  # XGE p.137
    "lock of trickery": 138,  # XGE p.138
    "moon-touched sword": 138,  # XGE p.138
    "mystery key": 138,  # XGE p.138
    "orb of direction": 138,  # XGE p.138
    "orb of time": 138,  # XGE p.138
    "perfume of bewitching": 138,  # XGE p.138
    "pipe of smoke monsters": 138,  # XGE p.138
    "pole of angling": 138,  # XGE p.138
    "pole of collapsing": 138,  # XGE p.138
    "pot of awakening": 138,  # XGE p.138
    "pressure capsule": 138,  # XGE p.138
    "rope of mending": 138,  # XGE p.138
    "ruby of the war mage": 138,  # XGE p.138
    "shield of expression": 139,  # XGE p.139
    "smoldering armor": 139,  # XGE p.139
    "spell scroll (cantrip)": 200,
    "spell scroll (1st level)": 200,
    "spell scroll (2nd level)": 200,
    "spell scroll (3rd level)": 200,
    "staff of adornment": 139,  # XGE p.139
    "staff of birdcalls": 139,  # XGE p.139
    "staff of flowers": 139,  # XGE p.139
    "talking doll": 139,  # XGE p.139
    "tankard of sobriety": 139,  # XGE p.139
    "unbreakable arrow": 139,  # XGE p.139
    "veteran's cane": 139,  # XGE p.139
    "walloping ammunition": 139,  # XGE p.139
    "wand of conducting": 140,  # XGE p.140
    "wand of pyrotechnics": 140,  # XGE p.140
    "wand of scowls": 140,  # XGE p.140
    "wand of smiles": 140,  # XGE p.140
    # Spellguard shield → p.200
    "spellguard shield": 200,
    # Ioun stones — additional colors
    "ioun stone of absorption": 177,
    "ioun stone of agility": 177,
    "ioun stone of awareness": 177,
    "ioun stone of fortitude": 177,
    "ioun stone of greater absorption": 177,
    "ioun stone of insight": 177,
    "ioun stone of intellect": 177,
    "ioun stone of leadership": 177,
    "ioun stone of mastery": 177,
    "ioun stone of protection": 177,
    "ioun stone of regeneration": 177,
    "ioun stone of reserve": 177,
    "ioun stone of strength": 177,
    "ioun stone of sustenance": 177,
    # Scroll of protection → p.199
    "scroll of protection": 199,
}

KNOWN_PAGES_DMG.update(DMG_VARIANT_PAGES)

# ── Additional variant aliases for differently-named items ──
DMG_VARIANT_PAGES_2 = {
    # Figurine of wondrous power — full names
    "bronze griffon figurine of wondrous power": 169,
    "ebony fly figurine of wondrous power": 169,
    "golden lions figurine of wondrous power": 169,
    "ivory goats figurine of wondrous power": 169,
    "marble elephant figurine of wondrous power": 169,
    "obsidian steed figurine of wondrous power": 169,
    "onyx dog figurine of wondrous power": 169,
    "serpentine owl figurine of wondrous power": 169,
    "silver raven figurine of wondrous power": 169,
    # Horn of Valhalla variants → p.175
    "brass horn of valhalla": 175,
    "bronze horn of valhalla": 175,
    "iron horn of valhalla": 175,
    "silver horn of valhalla": 175,
    # Manual of golems variants → pp.180-181
    "manual of clay golems": 180,
    "manual of flesh golems": 180,
    "manual of iron golems": 180,
    "manual of stone golems": 180,
    # Potion of resistance variants → p.188
    "potion of acid resistance": 188,
    "potion of cold resistance": 188,
    "potion of fire resistance": 188,
    "potion of force resistance": 188,
    "potion of lightning resistance": 188,
    "potion of necrotic resistance": 188,
    "potion of poison resistance": 188,
    "potion of psychic resistance": 188,
    "potion of radiant resistance": 188,
    "potion of thunder resistance": 188,
    # Known DMG items with naming differences
    "glamoured studded leather armor": 172,  # "glamoured studded leather" in DMG
    "iron bands of binding": 177,  # "iron bands of bilarro" in DMG
    # More spell scroll level variants
    "spell scroll (4th level)": 200,
    "spell scroll (5th level)": 200,
    "spell scroll (6th level)": 200,
    "spell scroll (7th level)": 200,
    "spell scroll (8th level)": 200,
    "spell scroll (9th level)": 200,
}
KNOWN_PAGES_DMG.update(DMG_VARIANT_PAGES_2)

# Additional PHB items
PHB_EXTRAS = {
    # Armor with "armor" suffix
    "half plate armor": 145,
    "hide armor": 145,
    "padded armor": 145,
    "splint armor": 145,
    "plate armor": 145,
    "leather armor": 145,
    "studded leather armor": 145,
    "chain mail armor": 145,
    "chain shirt armor": 145,
    "ring mail armor": 145,
    "scale mail armor": 145,
    "breastplate armor": 145,
    # Misc items
    "little bag of sand": 150,
    "wooden staff": 149,  # quarterstaff is on weapons table
    "stabling (1 day)": 158,
    "wine - common (pitcher)": 158,
    "wine - fine (bottle)": 158,
    "hireling - skilled": 159,
    "hireling - untrained": 159,
    "coach cab - between towns": 158,
    "coach cab - within a city": 158,
    "road or gate toll": 158,
    "ship's passage": 158,
    "hempen rope": 150,
}
KNOWN_PAGES_PHB.update(PHB_EXTRAS)


def normalize_name(name):
    """Normalize item name for comparison."""
    return name.lower().strip().rstrip('.')


def resolve_source(source_str):
    """Given a source string, return (pdf_path, display_prefix) or None."""
    if not source_str:
        return None

    # Try exact match first
    if source_str in SOURCE_TO_PDF:
        return SOURCE_TO_PDF[source_str]

    # Try substring match
    for key, value in SOURCE_TO_PDF.items():
        if key.lower() in source_str.lower():
            return value

    # Common abbreviations
    lower = source_str.lower()
    if 'phb' in lower or "player's handbook" in lower or 'players handbook' in lower:
        return SOURCE_TO_PDF["Player's Handbook"]
    if 'dmg' in lower or "dungeon master" in lower:
        return SOURCE_TO_PDF["Dungeon Master's Guide"]
    if 'xge' in lower or 'xanathar' in lower:
        return SOURCE_TO_PDF["Xanathar's Guide to Everything"]
    if 'toa' in lower or 'tomb of annihilation' in lower:
        return SOURCE_TO_PDF["Tomb of Annihilation"]
    if 'scag' in lower or 'sword coast' in lower:
        return SOURCE_TO_PDF["Sword Coast Adventurer's Guide"]
    if 'wdh' in lower or 'dragon heist' in lower:
        return SOURCE_TO_PDF["Waterdeep: Dragon Heist"]
    if 'ggr' in lower or 'ravnica' in lower:
        return SOURCE_TO_PDF["Guildmasters' Guide to Ravnica"]
    if 'mtf' in lower or 'mordenkainen' in lower:
        return SOURCE_TO_PDF["Mordenkainen's Tome of Foes"]
    if 'vgm' in lower or "volo" in lower:
        return SOURCE_TO_PDF["Volo's Guide to Monsters"]
    if 'lmop' in lower or 'lost mine' in lower or 'phandelver' in lower:
        return SOURCE_TO_PDF["Lost Mine of Phandelver"]
    if 'rot' in lower or 'rise of tiamat' in lower or 'tyranny' in lower:
        return SOURCE_TO_PDF["The Rise of Tiamat"]
    if 'hotdq' in lower or 'hoard' in lower:
        return SOURCE_TO_PDF["Hoard of the Dragon Queen"]
    if 'ttp' in lower or 'tortle' in lower:
        return SOURCE_TO_PDF["The Tortle Package"]
    if 'ee' in lower or 'elemental evil' in lower:
        return SOURCE_TO_PDF["Elemental Evil Player's Companion"]
    if 'wge' in lower or 'eberron' in lower or 'wayfinder' in lower:
        return SOURCE_TO_PDF["Wayfinder's Guide to Eberron"]

    return None


def has_page_number(source_str):
    """Check if source string already includes a page number."""
    if not source_str:
        return False
    return bool(HAS_PAGE_RE.search(source_str))


def extract_clean_name(name):
    """Extract the base name, stripping parentheticals for searching."""
    # "Acid (vial)" -> "Acid"
    # "Potion of Healing (*)" -> "Potion of Healing"
    clean = re.sub(r'\s*\(.*?\)', '', name).strip()
    # Also handle SRD suffix patterns
    clean = re.sub(r'\s*\*+$', '', clean).strip()
    return clean if clean else name


def search_pdf_for_page(pdf_path, item_name):
    """Search a PDF for an item name and return the page number (1-indexed)."""
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        print(f"  WARNING: Cannot open {pdf_path}: {e}", file=sys.stderr)
        return None

    name_lower = item_name.lower().strip()

    # Strategy 1: Try exact name match
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text")
        if name_lower in text.lower():
            # Found it! But also check it's in a relevant context
            # (not just a passing mention in a spell description etc.)
            doc.close()
            return page_num + 1  # 1-indexed

    # Strategy 2: Try with parenthetical stripped
    clean = extract_clean_name(item_name)
    if clean.lower() != name_lower:
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("text")
            if clean.lower() in text.lower():
                doc.close()
                return page_num + 1

    # Strategy 3: Try without comma (e.g., "potion of healing" not "potion of healing (*)")
    simple = re.sub(r'[,\*\(\)\[\]].*', '', item_name).strip().lower()
    if simple != name_lower:
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("text")
            if simple in text.lower():
                doc.close()
                return page_num + 1

    doc.close()
    return None


def main():
    print("=== Building Item Page Map ===\n")

    # ── Load all items ──
    items_to_process = []  # (item_key, item_name, source_str, data_source)

    # 1. SRD Equipment
    with open(DATA_DIR / "srd_cache/equipment.json") as f:
        srd_equip = json.load(f)
    for item in srd_equip:
        name = item.get("name", "")
        key = name.lower()
        if key in SKIP_NAMES:
            continue
        # These all get "PHB 2014" in main.py
        items_to_process.append((key, name, "PHB 2014", "srd_equip"))
    print(f"SRD equipment: {len(srd_equip)} items")

    # 2. SRD Magic Items
    with open(DATA_DIR / "srd_cache/magic-items.json") as f:
        srd_magic = json.load(f)
    for item in srd_magic:
        name = item.get("name", "")
        key = name.lower()
        src = item.get("source", "") or "DMG 2014"
        items_to_process.append((key, name, src, "srd_magic"))
    print(f"SRD magic items: {len(srd_magic)} items")

    # 3. Manual Equipment
    with open(DATA_DIR / "manual_data/equipment.json") as f:
        man_equip = json.load(f)
    for item in man_equip:
        name = item.get("name", "")
        key = name.lower()
        src = item.get("source", "")
        items_to_process.append((key, name, src, "man_equip"))
    print(f"Manual equipment: {len(man_equip)} items")

    # 4. Manual Magic Items
    with open(DATA_DIR / "manual_data/magic_items.json") as f:
        man_magic = json.load(f)
    for item in man_magic:
        name = item.get("name", "")
        key = name.lower()
        src = item.get("source", "")
        items_to_process.append((key, name, src, "man_magic"))
    print(f"Manual magic items: {len(man_magic)} items")

    # 5. Firearm items (from main.py inline data)
    firearm_names = [
        "pistol", "musket", "bullets (10)",
        "pistol, automatic", "revolver", "rifle, hunting",
        "rifle, automatic", "shotgun",
        "laser pistol", "antimatter rifle", "laser rifle", "energy cell",
        "bomb", "gunpowder, powder horn", "gunpowder, keg",
        "dynamite (stick)", "grenade, fragmentation", "grenade, smoke",
        "grenade launcher",
    ]
    for name in firearm_names:
        items_to_process.append((name.lower(), name, "DMG 2014 p.267", "firearm"))
    print(f"Firearm items: {len(firearm_names)} items")

    total = len(items_to_process)
    print(f"\nTotal items to process: {total}")

    # ── Filter: only items without page numbers ──
    no_page = []
    has_page = []
    for key, name, src, ds in items_to_process:
        if has_page_number(src):
            has_page.append((key, name, src, ds))
        else:
            no_page.append((key, name, src, ds))

    print(f"Already have page numbers: {len(has_page)}")
    print(f"Need page numbers: {len(no_page)}")

    # ── Group by PDF to search ──
    by_pdf = defaultdict(list)  # pdf_path -> [(key, name, src_str)]
    unknown = []

    for key, name, src, ds in no_page:
        resolved = resolve_source(src)
        if resolved:
            pdf_filename, display_prefix = resolved
            pdf_path = MANUALS_DIR / pdf_filename
            if pdf_path.exists():
                by_pdf[str(pdf_path)].append((key, name, src, display_prefix))
            else:
                unknown.append((key, name, src, f"MISSING:{pdf_path}"))
        else:
            unknown.append((key, name, src, "UNKNOWN_SOURCE"))

    print(f"\nUnknown/unresolvable: {len(unknown)}")
    for key, name, src, reason in unknown[:10]:
        print(f"  {name!r} — source={src!r} — {reason}")
    if len(unknown) > 10:
        print(f"  ... and {len(unknown)-10} more")

    print(f"\nPDFs to search: {len(by_pdf)}")
    for pdf_path, items in sorted(by_pdf.items(), key=lambda x: -len(x[1])):
        print(f"  {Path(pdf_path).name}: {len(items)} items")

    # ── Search each PDF ──
    results = {}  # item_key -> {"page": N, "source_str": "PHB 2014 p.149"}

    for pdf_path_str, items in sorted(by_pdf.items(), key=lambda x: -len(x[1])):
        pdf_filename = Path(pdf_path_str).name
        print(f"\n--- Searching {pdf_filename} ({len(items)} items) ---")

        # Determine if it's PHB or DMG for known pages lookup
        is_phb = 'player' in pdf_filename.lower()
        is_dmg = 'dungeon master' in pdf_filename.lower() or 'dmg' in pdf_filename.lower()

        # Phase 1: Check known pages (covers PHB and DMG comprehensively)
        found = 0
        remaining = []
        for key, name, src, display_prefix in items:
            name_lower = key.lower() if key else name.lower()

            if is_phb and name_lower in KNOWN_PAGES_PHB:
                page = KNOWN_PAGES_PHB[name_lower]
                results[key] = {"page": page, "source_str": f"{display_prefix} p.{page}"}
                found += 1
                continue

            if is_dmg and name_lower in KNOWN_PAGES_DMG:
                page = KNOWN_PAGES_DMG[name_lower]
                results[key] = {"page": page, "source_str": f"{display_prefix} p.{page}"}
                found += 1
                continue

            if name_lower in SKIP_NAMES:
                results[key] = {"page": None, "source_str": src}
                continue

            remaining.append((key, name, src, display_prefix))

        print(f"  Known pages: {found}, Need PDF search: {len(remaining)}")

        # Phase 2: PDF search for remaining items
        if remaining and HAS_FITZ:
            doc = None
            try:
                doc = fitz.open(pdf_path_str)
                total_pages = len(doc)

                # Pre-extract page text
                page_texts = []
                for p in range(total_pages):
                    page_texts.append(doc[p].get_text("text"))
                doc.close()

                found2 = 0
                for key, name, src, display_prefix in remaining:
                    page = None

                    # Strategy 1: exact name match
                    name_clean = name.lower().strip()
                    for pg, text in enumerate(page_texts):
                        if name_clean in text.lower():
                            page = pg + 1
                            break

                    # Strategy 2: normalized match (strip parentheticals)
                    if page is None:
                        name_stripped = re.sub(r'\s*\(.*?\)', '', name).strip().lower()
                        if name_stripped and name_stripped != name_clean:
                            for pg, text in enumerate(page_texts):
                                if name_stripped in text.lower():
                                    page = pg + 1
                                    break

                    # Strategy 3: simple name (before comma)
                    if page is None:
                        name_simple = re.sub(r'[,\*\(\)\[\]].*', '', name).strip().lower()
                        if name_simple and name_simple != name_clean:
                            for pg, text in enumerate(page_texts):
                                if name_simple in text.lower():
                                    page = pg + 1
                                    break

                    if page:
                        results[key] = {"page": page, "source_str": f"{display_prefix} p.{page}"}
                        found2 += 1
                    else:
                        results[key] = {"page": None, "source_str": src}

                print(f"  PDF search found: {found2}, Not found: {len(remaining) - found2}")

            except Exception as e:
                print(f"  PDF search error: {e}")
                for key, name, src, display_prefix in remaining:
                    results[key] = {"page": None, "source_str": src}
        elif remaining:
            print(f"  (no pymupdf, skipping PDF search for {len(remaining)} items)")
            for key, name, src, display_prefix in remaining:
                results[key] = {"page": None, "source_str": src}

    # ── Also include items that already have page numbers ──
    for key, name, src, ds in has_page:
        # Parse existing page number
        m = re.search(r'\b[pP]\.?\s*(\d+)', src)
        page = int(m.group(1)) if m else None
        # Normalize source string
        resolved = resolve_source(src)
        if resolved and page:
            _, display_prefix = resolved
            results[key] = {"page": page, "source_str": f"{display_prefix} p.{page}"}
        else:
            results[key] = {"page": page, "source_str": src}

    # ── Stats ──
    total_found = sum(1 for v in results.values() if v.get("page"))
    total_missing = len(results) - total_found
    print(f"\n=== RESULTS ===")
    print(f"Total items: {len(results)}")
    print(f"With page numbers: {total_found}")
    print(f"Without page numbers: {total_missing}")

    # Show some missing
    if total_missing:
        print("\nSample missing:")
        shown = 0
        for key, val in results.items():
            if not val.get("page"):
                print(f"  {key!r}: {val['source_str']!r}")
                shown += 1
                if shown >= 10:
                    break

    # ── Save ──
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(results, f, indent=2, sort_keys=True)
    print(f"\nSaved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
