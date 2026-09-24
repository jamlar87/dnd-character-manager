"""Re-attribute the Tortle race from the 2022 reprint to the 2017 original the library owns.

Why: the record carried "(Monsters of the Multiverse, p.34)" + slug MToM and MPMM's wording,
but that PDF is not in the library, so the badge could only alert. The library DOES hold
"D&D 5E - The Tortle Package.pdf" (slug TTP) — and its page 4 has the Tortle writeup. The
trait sets differ, so this is a real content change, taken verbatim from TTP p.4:

  MPMM (2022): Claws 1d6 · Natural Armor "can't wear light, medium or heavy armor" ·
               Nature's Intuition (one skill of your choice)
  TTP  (2017): Claws 1d4 · Natural Armor "ill-suited to wearing armor / gain no benefit" ·
               Survival Instinct (proficiency in Survival)

The record's `asi` already held TTP's +2 Strength / +1 Wisdom, so the ASI trait joins it.
Trait text and the trait list (Ability Score Increase, Age, Alignment, Size, Speed, ..., 
Languages) follow the app's convention for book-sourced races — see Triton (MTF p.8).

Backs up races.json and race_page_map.json first.   Usage: --apply
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RACES = REPO / "data" / "manual_data" / "races.json"
RACE_MAP = REPO / "data" / "page_maps" / "race_page_map.json"
# data.py seeds RACES from this export, and services/data_loader.py SKIPS any race already
# present there (`if name in RACES:` at the top of the race loop) — so for Tortle this file,
# not manual_data, is what the app serves. Editing the record alone changes nothing visible.
EXPORT = REPO / "data" / "exports" / "races_export.json"
BACKUP = REPO / "data" / "backups" / f"tortle-ttp-{datetime.now():%Y%m%d-%H%M%S}"

SOURCE = "(The Tortle Package, p.4)"
SLUG = "TTP"
# Style used by the other races: "<slug> p.<n>".
EXPORT_SOURCE = "TTP p.4"
# The export's summary line said "450–500 pounds"; the cited book says "average 450 pounds".
EXPORT_DESC_FIX = ("450–500 pounds", "about 450 pounds")

# Verbatim from The Tortle Package, p.4 ("TORTLE TRAITS").
TRAITS = [
    ("Ability Score Increase",
     "Your Strength score increases by 2, and your Wisdom score increases by 1."),
    ("Age",
     "Young tortles crawl for a few weeks after birth before learning to walk on two legs. "
     "They reach adulthood by the age of 15 and live an average of 50 years."),
    ("Alignment",
     "Tortles tend to lead orderly, ritualistic lives. They develop customs and routines, "
     "becoming more set in their ways as they age. Most are lawful good. A few can be selfish "
     "and greedy, tending more toward evil, but it's unusual for a tortle to shuck off order in "
     "favor of chaos."),
    ("Size",
     "Tortle adults stand 5 to 6 feet tall and average 450 pounds. Their shells account for "
     "roughly one-third of their weight. Your size is Medium."),
    ("Speed", "Your base walking speed is 30 feet."),
    ("Claws",
     "Your claws are natural weapons, which you can use to make unarmed strikes. If you hit "
     "with them, you deal slashing damage equal to 1d4 + your Strength modifier, instead of the "
     "bludgeoning damage normal for an unarmed strike."),
    ("Hold Breath",
     "You can hold your breath for up to 1 hour at a time. Tortles aren't natural swimmers, but "
     "they can remain underwater for some time before needing to come up for air."),
    ("Natural Armor",
     "Due to your shell and the shape of your body, you are ill-suited to wearing armor. Your "
     "shell provides ample protection, however; it gives you a base AC of 17 (your Dexterity "
     "modifier doesn't affect this number). You gain no benefit from wearing armor, but if you "
     "are using a shield, you can apply the shield's bonus as normal."),
    ("Shell Defense",
     "You can withdraw into your shell as an action. Until you emerge, you gain a +4 bonus to "
     "AC, and you have advantage on Strength and Constitution saving throws. While in your "
     "shell, you are prone, your speed is 0 and can't increase, you have disadvantage on "
     "Dexterity saving throws, you can't take reactions, and the only action you can take is a "
     "bonus action to emerge from your shell."),
    ("Survival Instinct",
     "You gain proficiency in the Survival skill. Tortles have finely honed survival instincts."),
    ("Languages", "You can speak, read, and write Aquan and Common."),
]

# Traits whose sheet automation is driven by rules text rather than flavour.
MECHANICAL = {"Claws", "Hold Breath", "Natural Armor", "Shell Defense", "Survival Instinct"}


def scaffold():
    return {
        "armor_profs": [], "weapon_profs": [], "tool_profs": [], "skill_profs": [],
        "damage_resist": [], "damage_immune": [], "condition_immune": [],
        "speed": None, "darkvision": None, "hp_per_level": 0, "natural_armor": None,
        "notes": "",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    races = json.loads(RACES.read_text())
    tortle = next((r for r in races if str(r.get("name", "")).lower() == "tortle"), None)
    if tortle is None:
        print("  no Tortle record found", file=sys.stderr)
        return 1

    print(f"  before: source={tortle.get('source')!r} slug={tortle.get('_source_manual')!r}")
    print(f"          traits={[t.get('name') for t in tortle.get('traits') or []]}")
    print(f"  after:  source={SOURCE!r} slug={SLUG!r}")
    print(f"          traits={[n for n, _ in TRAITS]}")

    if not args.apply:
        print("\n  (dry run — add --apply)")
        return 0

    BACKUP.mkdir(parents=True, exist_ok=True)
    shutil.copy2(RACES, BACKUP / "races.json")
    shutil.copy2(RACE_MAP, BACKUP / "race_page_map.json")

    old_effects = tortle.get("_effects") or {}
    tortle["source"] = SOURCE
    tortle["_source_manual"] = SLUG
    tortle["traits"] = [{"name": n, "description": d, "uses": 0, "recharge": ""}
                        if n in MECHANICAL else {"name": n, "description": d}
                        for n, d in TRAITS]

    effects = {}
    for name, _ in TRAITS:
        prev = old_effects.get(name)
        effects[name] = prev if isinstance(prev, dict) else scaffold()
    if "Nature's Intuition" in old_effects:  # carry its Survival proficiency across
        effects["Survival Instinct"] = dict(old_effects["Nature's Intuition"])
    effects["Claws"]["notes"] = ("Your claws are natural weapons. You can make unarmed strikes "
                                 "with them, dealing 1d4 + Strength modifier slashing damage "
                                 "instead of normal bludgeoning damage.")
    effects["Natural Armor"]["notes"] = ("You are ill-suited to wearing armor and gain no "
                                         "benefit from it; shield bonus applies normally.")
    effects["Hold Breath"]["notes"] = ("You can hold your breath for up to 1 hour at a time. "
                                       "Tortles aren't natural swimmers, but can remain "
                                       "underwater before needing to come up for air.")
    tortle["_effects"] = effects

    RACES.write_text(json.dumps(races, indent=2, ensure_ascii=False) + "\n")

    page_map = json.loads(RACE_MAP.read_text())
    page_map["tortle"] = {"page": 4, "source_str": "TTP p.4"}
    RACE_MAP.write_text(json.dumps(page_map, indent=2, ensure_ascii=False) + "\n")

    # The app actually serves this file for Tortle — see the EXPORT comment above.
    export = json.loads(EXPORT.read_text())
    shutil.copy2(EXPORT, BACKUP / "races_export.json")
    tortle_export = export.setdefault("Tortle", {})
    tortle_export["traits"] = [n for n, _ in TRAITS]
    tortle_export["source"] = EXPORT_SOURCE
    tortle_export["_source_slug"] = SLUG
    if EXPORT_DESC_FIX[0] in str(tortle_export.get("desc", "")):
        tortle_export["desc"] = tortle_export["desc"].replace(*EXPORT_DESC_FIX)
    EXPORT.write_text(json.dumps(export, indent=2, ensure_ascii=False) + "\n")
    print(f"  export: slug={tortle_export['_source_slug']} source={tortle_export['source']!r} "
          f"traits={len(tortle_export['traits'])}")

    print(f"\n  applied; backup {BACKUP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
