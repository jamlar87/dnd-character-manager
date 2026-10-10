#!/usr/bin/env python3
"""
Build a racial trait→page mapping for D&D 5e racial traits.
Maps each trait to the PHB page of its parent race.

Output: data/trait_page_map.json
"""
import json
from pathlib import Path

DATA_DIR = Path("/home/james/dnd-character-manager/data")
OUTPUT_PATH = DATA_DIR / "page_maps" / "trait_page_map.json"

# ── Race→Page mapping ──
RACE_PAGES = {
    # PHB Races
    "Dwarf": (18, "PHB 2014"),
    "Hill Dwarf": (20, "PHB 2014"),
    "Mountain Dwarf": (20, "PHB 2014"),
    "Elf": (21, "PHB 2014"),
    "High Elf": (23, "PHB 2014"),
    "Wood Elf": (24, "PHB 2014"),
    "Dark Elf (Drow)": (24, "PHB 2014"),
    "Halfling": (26, "PHB 2014"),
    "Lightfoot Halfling": (28, "PHB 2014"),
    "Stout Halfling": (28, "PHB 2014"),
    "Human": (29, "PHB 2014"),
    "Variant Human": (31, "PHB 2014"),
    "Dragonborn": (32, "PHB 2014"),
    "Gnome": (35, "PHB 2014"),
    "Forest Gnome": (37, "PHB 2014"),
    "Rock Gnome": (37, "PHB 2014"),
    "Half-Elf": (38, "PHB 2014"),
    "Half-Orc": (40, "PHB 2014"),
    "Tiefling": (42, "PHB 2014"),
    # Supplements
    "Duergar": (20, "SCAG"),     # SCAG p.115 also
    "Gold Dwarf": (20, "SCAG"),
    "Sea Elf": (10, "MTF"),      # MTF p.16
    "Eladrin": (10, "DMG 2014"),  # DMG p.286
    "Shadar-kai": (10, "MTF"),   # MTF p.16
    "Ghostwise Halfling": (10, "SCAG"),  # SCAG p.110
    "Deep Gnome": (7, "EEPC"),   # EEPC p.7
    "Genasi": (7, "EEPC"),
    "Air Genasi": (10, "EEPC"),
    "Earth Genasi": (10, "EEPC"),
    "Fire Genasi": (10, "EEPC"),
    "Water Genasi": (10, "EEPC"),
}

# ── Trait→Parent Race mapping ──
# Each trait maps to the race/subrace that provides it
TRAIT_RACE_MAP = {
    # Dwarf traits (PHB p.18-20)
    "Dwarven Resilience": "Dwarf",
    "Stonecunning": "Dwarf",
    "Dwarven Toughness": "Hill Dwarf",
    "Dwarven Armor Training": "Mountain Dwarf",
    "Duergar Resilience": "Duergar",
    "Duergar Magic": "Duergar",
    
    # Elf traits (PHB p.21-24)
    "Keen Senses": "Elf",
    "Fey Ancestry": "Elf",
    "Trance": "Elf",
    "Elf Weapon Training": "High Elf",
    "Cantrip (High Elf)": "High Elf",
    "Fleet of Foot": "Wood Elf",
    "Mask of the Wild": "Wood Elf",
    "Superior Darkvision": "Dark Elf (Drow)",
    "Sunlight Sensitivity": "Dark Elf (Drow)",
    "Drow Magic": "Dark Elf (Drow)",
    "Sea Elf Training": "Sea Elf",
    "Child of the Sea": "Sea Elf",
    "Fey Step": "Eladrin",
    
    # Halfling traits (PHB p.26-28)
    "Lucky": "Halfling",
    "Brave": "Halfling",
    "Halfling Nimbleness": "Halfling",
    "Naturally Stealthy": "Lightfoot Halfling",
    "Stout Resilience": "Stout Halfling",
    "Silent Speech": "Ghostwise Halfling",
    
    # Dragonborn traits (PHB p.32-34)
    "Draconic Ancestry": "Dragonborn",
    "Breath Weapon": "Dragonborn",
    "Damage Resistance": "Dragonborn",
    
    # Gnome traits (PHB p.35-37)
    "Gnome Cunning": "Gnome",
    "Natural Illusionist": "Forest Gnome",
    "Speak with Small Beasts": "Forest Gnome",
    "Artificer's Lore": "Rock Gnome",
    "Tinker": "Rock Gnome",
    "Stone Camouflage": "Deep Gnome",
    
    # Genasi traits (EEPC)
    "Unending Breath": "Air Genasi",
    "Mingle with the Wind": "Air Genasi",
    "Earth Walk": "Earth Genasi",
    "Merge with Stone": "Earth Genasi",
    "Fire Resistance": "Fire Genasi",
    "Reach to the Blaze": "Fire Genasi",
    "Amphibious": "Water Genasi",
    "Swim": "Water Genasi",
    "Acid Resistance": "Water Genasi",
    "Call to the Wave": "Water Genasi",
    
    # Half-Elf traits (PHB p.38-39)
    "Skill Versatility": "Half-Elf",
    
    # Half-Orc traits (PHB p.40-41)
    "Relentless Endurance": "Half-Orc",
    "Savage Attacks": "Half-Orc",
    
    # Tiefling traits (PHB p.42-43)
    "Hellish Resistance": "Tiefling",
    "Infernal Legacy": "Tiefling",
    
    # Shadar-kai traits (MTF)
    "Necrotic Resistance": "Shadar-kai",
    "Blessing of the Raven Queen": "Shadar-kai",
}


def prefix_of(src: str) -> str:
    """Short book code for this map, from a record's display source."""
    lower = (src or "").lower()
    for needle, prefix in (("volo", "VGM"), ("vgm", "VGM"), ("mordenkainen", "MTF"),
                           ("mtf", "MTF"), ("sword coast", "SCAG"), ("scag", "SCAG"),
                           ("elemental evil", "EEPC"), ("ee", "EEPC"), ("ravnica", "GGR"),
                           ("ggr", "GGR"), ("eberron", "ERLW"), ("erlw", "ERLW"),
                           ("tortle", "TTP"), ("ttp", "TTP")):
        if needle in lower:
            return prefix
    return "PHB 2014"


def verified_race_pages() -> dict:
    """race -> (page, prefix) from the app's OWN race records.

    RACE_PAGES above is hand-curated and disagrees with the shipped records for a
    handful of races (Dwarven Armor Training cited PHB p.20 for Shield Dwarf, whose
    record is SCAG p.104). The record wins: it is what the race row displays.
    """
    out = {}
    path = DATA_DIR / "manual_data" / "races.json"
    if not path.exists():
        return out
    import re
    for r in json.loads(path.read_text()):
        if not isinstance(r, dict):
            continue
        name = (r.get("name") or "").strip().lower()
        src = (r.get("source") or "").strip()
        if not name or not src:
            continue
        m = re.search(r"\b[pP]\.?\s*(\d+)", src)
        if m:
            out[name] = (int(m.group(1)), prefix_of(src))
    return out


def traits_from_app_data() -> dict:
    """trait -> race, for traits exactly ONE race owns, taken from the app's RACES.

    The hand table below covers 51 traits; the app ships 231, so most trait rows had
    no source at all. A trait two races share is left out — its page cannot be one
    race's page (Silent Speech is Ghostwise-only, Darkvision is not).
    """
    import sys
    REPO = Path("/home/james/dnd-character-manager")
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    import main            # noqa: PLC0415
    import routes.characters  # noqa: F401,PLC0415  (runs the manual merge)
    owners: dict[str, set] = {}
    for race_name, race in (getattr(main, "RACES", {}) or {}).items():
        if not isinstance(race, dict):
            continue
        for t in (race.get("traits") or []):
            n = t.get("name") if isinstance(t, dict) else t
            if n:
                owners.setdefault(str(n).strip(), set()).add(race_name)
    return {t: next(iter(r)) for t, r in owners.items() if len(r) == 1}


def main():
    print("=== Building Racial Trait Page Map ===\n")
    
    # Cite what the RACE map cites: a trait used to carry its own hand-curated
    # page, so a race row and its traits could disagree (Shield Dwarf's record is
    # SCAG p.104 while Dwarven Armor Training claimed PHB p.20).
    race_map = {}
    race_path = DATA_DIR / "page_maps" / "race_page_map.json"
    if race_path.exists():
        race_map = {k.strip().lower(): v for k, v in json.loads(race_path.read_text()).items()}
    print(f"race map entries: {len(race_map)}")

    # Owners: the app's own race data first (it covers 231 traits vs the hand
    # table's 51), the hand table as a supplement for anything else.
    owners = traits_from_app_data()
    for trait, race in TRAIT_RACE_MAP.items():
        owners.setdefault(trait, race)
    print(f"traits with a single owner: {len(owners)}")

    def race_source(race_name):
        info = race_map.get(str(race_name).strip().lower())
        if isinstance(info, dict) and (info.get("source_str") or "").strip():
            return info["source_str"]
        if race_name in RACE_PAGES:
            page, prefix = RACE_PAGES[race_name]
            return f"{prefix} p.{page}"
        return ""

    results = {}
    corrected = 0
    unmapped = []
    for trait_name, race_name in sorted(owners.items()):
        entry = race_source(race_name)
        if not entry:
            unmapped.append((trait_name, race_name))
            continue
        if race_name in RACE_PAGES:
            page, prefix = RACE_PAGES[race_name]
            if f"{prefix} p.{page}" != entry:
                corrected += 1
        results[trait_name] = entry
    
    print(f"Mapped traits: {len(results)} of {len(owners)} "
          f"({corrected} differ from the hand table's page)")
    
    if unmapped:
        print(f"\nUnmapped traits: {len(unmapped)}")
        for trait, race in unmapped:
            print(f"  {trait} ← {race}")
    
    # Stats
    total = len(results)
    with_page = sum(1 for v in results.values() if "p." in v)
    print(f"\nTotal: {total}, with pages: {with_page}")
    
    # Save
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(results, f, indent=2, sort_keys=True)
    print(f"\nSaved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
