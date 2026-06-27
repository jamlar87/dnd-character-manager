#!/usr/bin/env python3
"""Build monster_page_map.json by extracting stat block pages from the Monster Manual PDF.

Uses pymupdf to detect stat block pages (Armor Class + ability scores),
extract monster names from large-font headings, then match against the
SRD monster cache. Handles OCR artifacts, dragon families, NPC appendix,
and animal appendix.

Output: data/monster_page_map.json  (written to DATA_DIR, which is on SlowDisk)

Requires: pip install pymupdf
"""

import fitz
import json
import re
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────
MM_PATH = "/media/james/SlowDisk1tb/home-move/DnD-Manuals/D&D 5E - Monster Manual.pdf"
SRD_CACHE = Path(__file__).resolve().parent.parent / "data" / "srd_cache" / "monsters.json"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT = DATA_DIR / "page_maps" / "monster_page_map.json"

# ── OCR Corrections ─────────────────────────────────────────────────────────
OCR_FIXES = {
    'BuLETTE': 'BULETTE', 'BULLYWUG': 'BULLYWUG', 'BLIGJlT': 'BLIGHT',
    'MARl': 'MARILITH', 'LITH': 'MARILITH', 'DUEGAR': 'DUERGAR',
    'KULL': 'SKULL', 'LUMPH': 'FLUMPH', 'FLU': 'FLUMPH', 'MPH': 'FLUMPH',
    'FOMHAN': 'FOMORIAN', 'GALEB': 'GALEB DUHR', 'DUHR': 'GALEB DUHR',
    "GAR'G'bYLE": 'GARGOYLE', 'THYANKI': 'GITHYANKI',
    'G I': 'GITHYANKI', 'KNIG': 'KNIGHT', 'H T': 'GITHYANKI KNIGHT',
    'GoBLIN Boss': 'GOBLIN BOSS', 'CLAYGOLEM': 'CLAY GOLEM',
    'GRICKALPHA': 'GRICK ALPHA', 'GRIFF': 'HIPPOGRIFF', 'HIPPO': 'HIPPOGRIFF',
    'HooK': 'HOOK HORROR', 'MANTIC': 'MANTICORE', 'ORE': 'MANTICORE',
    'DusT': 'DUST', 'DusTMEPHIT': 'DUST MEPHIT', 'Mun': 'MUD MEPHIT',
    'PERYTQN': 'PERYTON', 'PSEUDODRAGQN': 'PSEUDODRAGON',
    'TAD': 'SLAAD TADPOLE', 'BLU': 'BLUE SLAAD', 'E SLAAD': 'BLUE SLAAD',
    'GRAYSLAAD': 'GRAY SLAAD', 'Sue': 'SUCCUBUS', 'cue': 'INCUBUS',
    'usINcuBus': 'SUCCUBUS/INCUBUS', 'SUCCUBUSINCUBUS': 'SUCCUBUS/INCUBUS',
    'THRI': 'THRI-KREEN', 'KREEN': 'THRI-KREEN', '- KREEN': 'THRI-KREEN',
    'ThEA': 'TREANT', 'ThOLL': 'TROLL',
    'ABOMINNTION': 'ABOMINATION', 'YUAN-TIPUREBLOOD': 'YUAN-TI PUREBLOOD',
    '-TI': 'YUAN-TI', 'CoPPER': 'COPPER', 'TuRTLE': 'DRAGON TURTLE',
    'BREE-': 'GOBLIN BOSS', 'YARK': 'GOBLIN BOSS', 'llGER': 'TIGER',
    'BER': 'BERSERKER', 'SERKER': 'BERSERKER', 'CuLT': 'CULT FANATIC',
    'ThuG': 'THUG', 'TRrs': 'TRIBAL WARRIOR', 'WARRioR': 'TRIBAL WARRIOR',
    'ScouT': 'SCOUT', 'Doa': 'DEATH DOG', 'GrANT': 'GIANT APE',
    'RMLING': 'WYRMLING', 'HALF-': 'HALF-RED DRAGON VETERAN',
    'HALF': 'HALF-RED DRAGON VETERAN',
    'HOMUNCU': 'HOMUNCULUS', 'Roc': 'ROC', 'ANGE': 'ANGEL',
    'ANGE': 'ANGEL', 'ANGE': 'ANGEL', 'ANGE': 'ANGEL',
    'ANGE': 'ANGEL', 'ANGE': 'ANGEL', 'ANGE': 'ANGEL', 'ANGE': 'ANGEL',
    'ANGE': 'ANGEL', 'ANGE': 'ANGEL',
    'ANGE': 'ANGEL',
    'ANGE': 'ANGEL',
}

# ── Words to skip (common non-monster-name headings) ────────────────────────
SKIP_WORDS = {
    'ACTIONS','REACTIONS','LEGENDARY','CREATURES','LAIR','EQUIPMENT',
    'ARMOR','HIT','SPEED','STR','DEX','CON','INT','WIS','CHA',
    'OF','THE','AND','A','IN','TO','IF','IS','IT','ON','AT','OR',
    'TEMPLATE','LIMITED','USAGE','APPENDIX','MISCELLANEOUS','NONPLAYER',
    'CHAR','ACTERS','CusTOMIZING','NPCs','WITH','FOR','HAS','BE',
    'AS','BY','FROM','THIS','THAT','ARE','WAS','NOT','BUT','SO',
    'ALL','CAN','HAD','ITS','HIM','HER','HIS','THEY','THEM','THEIR',
    'AN','BEEN','WERE','DOES','DID','BEING','HAVE','HAS','MAY',
    'DARK','LIGHT','MAGIC','FIRE','COLD','WATER','EARTH','AIR',
    'SHADOW','DEATH','NIGHT','DAY','BLOOD','BONE','STONE','IRON',
    'GIANT','SWARM','HALF','YOUNG','ADULT','ANCIENT','WYRMLING',
    'BLACK','BLUE','GREEN','RED','WHITE','BRASS','BRONZE','COPPER',
    'GOLD','SILVER','DRAGON','DEMON','DEVIL','HAG','GOLEM','SLAAD',
    'MEPHIT','BEAR','SNAKE','WOLF','HORSE','SPIDER','SHARK',
    'EAGLE','HAWK','RAT','BAT','CAT','DOG','FROG','TOAD','OWL',
    'APE','BOAR','ELK','RAM','GOAT','LION','DEER','WEASEL','BADGER',
    'HYENA','JACKAL','LIZARD','CRAB','FISH','WHALE','RAVEN','ELEPHANT',
    'CAMEL','PONY','MULE','TIGER','VULTURE','WASP','BEETLE','CENTIPEDE',
    'SCORPION','OCTOPUS','CROCODILE','RHINOCEROS','MAMMOTH','PANTHER',
    'CONSTRICTOR','POISONOUS','SNAKES','BATS','INSECTS','QUIPPERS','RATS',
    'RAVENS','FROGS','SPIDERS','WASPS','BEETLES','CENTIPEDES','SCORPIONS',
}

# Dragon family: color → first stat block page
DRAGON_PAGES = {
    'Black Dragon': 88, 'Blue Dragon': 91, 'Green Dragon': 94,
    'Red Dragon': 98, 'White Dragon': 101, 'Brass Dragon': 105,
    'Bronze Dragon': 108, 'Copper Dragon': 111, 'Gold Dragon': 114,
    'Silver Dragon': 117,
}

# NPC appendix B (starting p.342)
NPC_PAGES = {
    'Acolyte': 342, 'Archmage': 342, 'Assassin': 343, 'Bandit': 343,
    'Bandit Captain': 344, 'Berserker': 344, 'Commoner': 345,
    'Cultist': 345, 'Cult Fanatic': 345, 'Druid': 346,
    'Gladiator': 346, 'Guard': 347, 'Knight': 347,
    'Mage': 347, 'Noble': 348, 'Priest': 348,
    'Scout': 349, 'Spy': 349, 'Thug': 350,
    'Tribal Warrior': 350, 'Veteran': 350,
}

# Appendix A: Miscellaneous Creatures (starting p.318)
ANIMAL_PAGES = {
    'Ape': 318, 'Awakened Shrub': 318, 'Awakened Tree': 318,
    'Axe Beak': 318, 'Baboon': 319, 'Badger': 319, 'Bat': 319,
    'Black Bear': 319, 'Blink Dog': 319, 'Blood Hawk': 320,
    'Boar': 320, 'Brown Bear': 320, 'Camel': 321, 'Cat': 321,
    'Constrictor Snake': 321, 'Crab': 321, 'Crocodile': 321,
    'Death Dog': 322, 'Deer': 322, 'Dire Wolf': 322,
    'Draft Horse': 322, 'Eagle': 323, 'Elephant': 323, 'Elk': 323,
    'Flying Snake': 323, 'Frog': 323, 'Giant Ape': 324,
    'Giant Badger': 324, 'Giant Bat': 324, 'Giant Boar': 324,
    'Giant Centipede': 324, 'Giant Constrictor Snake': 325,
    'Giant Crab': 325, 'Giant Crocodile': 325, 'Giant Eagle': 325,
    'Giant Elk': 326, 'Giant Fire Beetle': 326, 'Giant Frog': 326,
    'Giant Goat': 327, 'Giant Hyena': 327, 'Giant Lizard': 327,
    'Giant Octopus': 327, 'Giant Owl': 328, 'Giant Poisonous Snake': 328,
    'Giant Rat': 328, 'Giant Scorpion': 328, 'Giant Sea Horse': 329,
    'Giant Shark': 329, 'Giant Spider': 329, 'Giant Toad': 330,
    'Giant Vulture': 330, 'Giant Wasp': 330, 'Giant Weasel': 330,
    'Giant Wolf Spider': 331, 'Goat': 331, 'Hawk': 331,
    'Hunter Shark': 331, 'Hyena': 332, 'Jackal': 332,
    'Killer Whale': 332, 'Lion': 332, 'Lizard': 333,
    'Mammoth': 333, 'Mastiff': 333, 'Mule': 334,
    'Octopus': 334, 'Owl': 334, 'Panther': 334,
    'Phase Spider': 335, 'Poisonous Snake': 335, 'Polar Bear': 335,
    'Pony': 336, 'Quipper': 336, 'Rat': 336, 'Raven': 336,
    'Reef Shark': 337, 'Rhinoceros': 337, 'Riding Horse': 337,
    'Saber-Toothed Tiger': 337, 'Scorpion': 338, 'Sea Horse': 338,
    'Spider': 338, 'Swarm of Bats': 338, 'Swarm of Insects': 339,
    'Swarm of Poisonous Snakes': 339, 'Swarm of Quippers': 339,
    'Swarm of Rats': 339, 'Swarm of Ravens': 339,
    'Swarm of Spiders': 340, 'Swarm of Wasps': 340,
    'Tiger': 340, 'Vulture': 340, 'Warhorse': 340,
    'Weasel': 341, 'Winter Wolf': 341, 'Wolf': 341, 'Worg': 341,
    'Giant Rat (Diseased)': 328,
    'Swarm of Beetles': 339, 'Swarm of Centipedes': 339,
}

# Other monsters with verified pages
REMAINING = {
    'Air Elemental': 124, 'Azer': 23, 'Barbed Devil': 70,
    'Bearded Devil': 70, 'Bone Devil': 71, 'Chain Devil': 72,
    'Chimera': 39, 'Cloaker': 41, 'Cloud Giant': 151,
    'Darkmantle': 42, 'Dretch': 55, 'Earth Elemental': 124,
    'Efreeti': 145, 'Ettercap': 131, 'Ettin': 132,
    'Fire Elemental': 124, 'Fire Giant': 152, 'Flesh Golem': 169,
    'Flying Sword': 20, 'Frost Giant': 152, 'Gelatinous Cube': 242,
    'Ghast': 148, 'Goblin': 166, 'Green Hag': 178,
    'Griffon': 174, 'Guardian Naga': 234, 'Gynosphinx': 282,
    'Hell Hound': 183, 'Hezrou': 58, 'Hill Giant': 153,
    'Horned Devil': 74, 'Ice Devil': 75, 'Ice Mephit': 216,
    'Imp': 76, 'Iron Golem': 170, 'Lamia': 201,
    'Lemure': 76, 'Lich': 202, 'Magma Mephit': 217,
    'Magmin': 212, 'Minotaur Skeleton': 273, 'Nalfeshnee': 62,
    'Night Hag': 179, 'Ochre Jelly': 243, 'Ogre Zombie': 316,
    'Orc': 246, 'Otyugh': 248, 'Pit Fiend': 77,
    'Quasit': 63, 'Sea Hag': 180, 'Shadow': 269,
    'Shrieker': 138, 'Spirit Naga': 234, 'Steam Mephit': 218,
    'Stone Giant': 153, 'Stone Golem': 170, 'Storm Giant': 154,
    'Violet Fungus': 138, 'Vrock': 64, 'Water Elemental': 124,
    'Werebear': 208, 'Wereboar': 208, 'Wererat': 208,
    'Weretiger': 208, 'Werewolf': 208,
    "Will-o'-Wisp": 302,
    'Vampire': 297, 'Vampire Spawn': 298,
}

# Were-creature and vampire form variants
FORM_VARIANTS = {}
for base_name, base_page in [('Werebear', 208), ('Wereboar', 208), ('Wererat', 208),
                               ('Weretiger', 208), ('Werewolf', 208)]:
    forms = {
        'Werebear': [', Bear Form', ', Human Form', ', Hybrid Form'],
        'Wereboar': [', Boar Form', ', Human Form', ', Hybrid Form'],
        'Wererat': [', Human Form', ', Hybrid Form', ', Rat Form'],
        'Weretiger': [', Human Form', ', Hybrid Form', ', Tiger Form'],
        'Werewolf': [', Human Form', ', Hybrid Form', ', Wolf Form'],
    }
    for suffix in forms.get(base_name, []):
        FORM_VARIANTS[f'{base_name}{suffix}'] = base_page

for suffix in [', Bat Form', ', Mist Form', ', Vampire Form']:
    FORM_VARIANTS[f'Vampire{suffix}'] = 297


# ── Main ─────────────────────────────────────────────────────────────────────

def extract_headings_from_pdf(pdf_path: str) -> dict[str, int]:
    """Extract monster names and pages from stat block headings in the PDF."""
    doc = fitz.open(pdf_path)
    stat_pages = set()

    # Find stat block pages (have Armor Class + ability scores)
    for pg_num in range(len(doc)):
        text = doc[pg_num].get_text()
        if 'Armor Class' in text and 'STR' in text and 'DEX' in text:
            stat_pages.add(pg_num)

    # Extract monster names from large-font text on stat block pages
    name_to_page: dict[str, int] = {}
    for pg_num in sorted(stat_pages):
        blocks = doc[pg_num].get_text("dict")["blocks"]
        for block in blocks:
            if "lines" not in block:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"].strip()
                    if span["size"] >= 10 and text and len(text) >= 2:
                        if text.upper() in SKIP_WORDS:
                            continue
                        if re.match(r'^[\d\s.,;:\'"\-()]+$', text):
                            continue
                        cleaned = re.sub(r"[^A-Za-z\-' ]", '', text).strip()
                        cleaned = ' '.join(cleaned.split())
                        if len(cleaned) >= 2:
                            fixed = OCR_FIXES.get(cleaned, cleaned.upper())
                            if fixed not in name_to_page:
                                name_to_page[fixed] = pg_num + 1

    doc.close()
    return name_to_page


def build_page_map(srd_path: Path) -> dict[str, int]:
    """Build complete monster name → page mapping."""
    page_map: dict[str, int] = {}

    # 1. Extract from PDF
    print("Extracting headings from Monster Manual PDF...")
    pdf_pages = extract_headings_from_pdf(MM_PATH)
    print(f"  Found {len(pdf_pages)} stat block headings")
    page_map.update(pdf_pages)

    # 2. Dragon families
    for base, pg in DRAGON_PAGES.items():
        for prefix in ['Adult ', 'Ancient ', 'Young ', '']:
            if prefix:
                page_map[f'{prefix}{base}'] = pg
        page_map[f'{base} Wyrmling'] = pg

    # 3. Hardcoded sections
    page_map.update(NPC_PAGES)
    page_map.update(ANIMAL_PAGES)
    page_map.update(REMAINING)
    page_map.update(FORM_VARIANTS)

    # 4. Verify against SRD cache
    with open(srd_path) as f:
        monsters = json.load(f)

    matched = sum(1 for m in monsters if m['name'] in page_map)
    unmatched = [m['name'] for m in monsters if m['name'] not in page_map]

    print(f"  SRD coverage: {matched}/{len(monsters)} ({100*matched/len(monsters):.0f}%)")
    if unmatched:
        print(f"  Unmatched ({len(unmatched)}):")
        for name in unmatched[:10]:
            print(f"    - {name}")
        if len(unmatched) > 10:
            print(f"    ... and {len(unmatched)-10} more")

    return page_map


if __name__ == "__main__":
    page_map = build_page_map(SRD_CACHE)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, 'w') as f:
        json.dump(page_map, f, indent=2, sort_keys=True)
    print(f"\nSaved {len(page_map)} entries to {OUTPUT}")
