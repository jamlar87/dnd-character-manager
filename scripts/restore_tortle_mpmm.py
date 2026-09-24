"""Restore Tortle to Monsters of the Multiverse (MPMM) now that the book is in the library.

Tortle was moved to The Tortle Package's 2017 traits only because MPMM was not owned; MPMM is
in the library now (wired via scripts/wire_manual.py, slug MPMM), so the record goes back to the
2022 printing it was originally ingested from.

Every string below is quoted from MPMM pages 34-35 of
"D&D 5E - Mordenkainen Presents Monsters of the Multiverse.pdf" (the Tortle entry: Creature Type,
Size, Speed, Claws 1d6, Hold Breath, Natural Armor, Nature's Intuition, Shell Defense), and the
ability score rule is MPMM's own chapter rule:

  "When determining your character's ability scores, increase one score by 2 and increase a
   different score by 1, or increase three different scores by 1."

It touches all three shadowing layers (record, seed export, page map) so the badge text and the
slug agree, and backs up first.  Usage: python3 scripts/restore_tortle_mpmm.py [--apply]
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

RACES = REPO / "data" / "manual_data" / "races.json"
EXPORT = REPO / "data" / "exports" / "races_export.json"
PAGE_MAP = REPO / "data" / "page_maps" / "race_page_map.json"
BACKUP = REPO / "data" / "backups" / f"tortle-mpmm-{datetime.now():%Y%m%d-%H%M%S}"

SOURCE = "(Monsters of the Multiverse, p.34)"
SLUG = "MPMM"
PAGE = 34

# trait name -> description straight out of MPMM p.34-35
MPMM_TRAITS = {
    "Ability Score Increase": (
        "When determining your character's ability scores, increase one score by 2 and increase "
        "a different score by 1, or increase three different scores by 1. Follow this rule "
        "regardless of the method you use to determine the scores, such as rolling or point buy."
    ),
    "Creature Type": "You are a Humanoid.",
    "Claws": (
        "You have claws that you can use to make unarmed strikes. When you hit with them, the "
        "strike deals 1d6 + your Strength modifier slashing damage, instead of the bludgeoning "
        "damage normal for an unarmed strike."
    ),
    "Hold Breath": "You can hold your breath for up to 1 hour.",
    "Natural Armor": (
        "Your shell provides you a base AC of 17 (your Dexterity modifier doesn't affect this "
        "number). You can't wear light, medium, or heavy armor, but if you are using a shield, "
        "you can apply the shield's bonus as normal."
    ),
    "Nature's Intuition": (
        "Thanks to your mystical connection to nature, you gain proficiency with one of the "
        "following skills of your choice: Animal Handling, Medicine, Nature, Perception, "
        "Stealth, or Survival."
    ),
    "Shell Defense": (
        "You can withdraw into your shell as an action. Until you emerge, you gain a +4 bonus to "
        "your AC, and you have advantage on Strength and Constitution saving throws. While in "
        "your shell, you are prone, your speed is 0 and can't increase, you have disadvantage on "
        "Dexterity saving throws, you can't take reactions, and the only action you can take is a "
        "bonus action to emerge from your shell."
    ),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    races = json.loads(RACES.read_text())
    tortle = next((r for r in races if isinstance(r, dict) and r.get("name") == "Tortle"), None)
    if tortle is None:
        print("  ERROR no Tortle record in races.json")
        return 1

    export = json.loads(EXPORT.read_text())
    page_map = json.loads(PAGE_MAP.read_text())

    changes = []

    # --- record: source, slug, trait texts ------------------------------------------------
    if tortle.get("source") != SOURCE:
        changes.append(("record.source", tortle.get("source"), SOURCE))
    if tortle.get("_source_manual") != SLUG:
        changes.append(("record._source_manual", tortle.get("_source_manual"), SLUG))

    traits = list(tortle.get("traits") or [])
    # drop anything MPMM does not have for the tortle
    traits = [t for t in traits if isinstance(t, dict) and t.get("name") != "Survival Instinct"]
    for name, desc in MPMM_TRAITS.items():
        if name in [t.get("name") for t in traits]:
            cur = next(t for t in traits if t.get("name") == name)
            if cur.get("description") != desc:
                changes.append((f"record.traits[{name}]", str(cur.get("description"))[:40], desc[:40]))
                cur["description"] = desc
        else:
            traits.append({"name": name, "description": desc})
            changes.append((f"record.traits[{name}]", None, desc[:40]))
    # keep MPMM's own order: ASI, Creature Type, Size, Speed, Claws, Hold Breath, Natural Armor,
    # Nature's Intuition, Shell Defense, then the flavour entries the app carries (Age, etc.)
    order = list(MPMM_TRAITS) + ["Age", "Alignment", "Size", "Speed", "Languages"]
    traits.sort(key=lambda t: order.index(t["name"]) if t.get("name") in order else len(order))
    if [t.get("name") for t in traits] != [t.get("name") for t in (tortle.get("traits") or [])]:
        changes.append(("record.trait_order", "old", " -> ".join(t.get("name", "?") for t in traits)))

    # --- seed export (shadows the record for the served data) -----------------------------
    exp = export.setdefault("Tortle", {})
    if exp.get("source") != SOURCE:
        changes.append(("export.source", exp.get("source"), SOURCE))
    if exp.get("_source_slug") != SLUG:
        changes.append(("export._source_slug", exp.get("_source_slug"), SLUG))
    new_names = [t.get("name") for t in traits]
    if exp.get("traits") != new_names:
        changes.append(("export.traits", str(exp.get("traits"))[:56], str(new_names)[:56]))

    # --- page map (wins over both) --------------------------------------------------------
    want_map = {"page": PAGE, "source_str": f"{SLUG} p.{PAGE}"}
    if page_map.get("tortle") != want_map:
        changes.append(("page_map.tortle", json.dumps(page_map.get("tortle")), json.dumps(want_map)))

    for where, old, new in changes:
        print(f"  FIX  {where:<28} {str(old)[:52]!r:<54} -> {str(new)[:52]!r}")
    print(f"\n  {len(changes)} change(s)")

    # self-check: the MPMM mechanics must be in place, TTP's must be gone
    problems = []
    names = [t.get("name") for t in traits]
    if "Nature's Intuition" not in names:
        problems.append("Nature's Intuition missing")
    if "Survival Instinct" in names:
        problems.append("Survival Instinct (TTP) still present")
    claws = next((t for t in traits if t.get("name") == "Claws"), {})
    if "1d6" not in str(claws.get("description")):
        problems.append("Claws not 1d6")
    if problems:
        print("  ERROR self-check failed: " + "; ".join(problems))
        return 1
    print("  self-check ok: Nature's Intuition present, Survival Instinct gone, Claws 1d6")

    if not changes:
        print("  already on the MPMM revision")
        return 0
    if not args.apply:
        print("\n  (dry run — add --apply)")
        return 0

    BACKUP.mkdir(parents=True, exist_ok=True)
    for path in (RACES, EXPORT, PAGE_MAP):
        shutil.copy2(path, BACKUP / path.name)
    tortle["source"] = SOURCE
    tortle["_source_manual"] = SLUG
    tortle["traits"] = traits
    exp["source"] = SOURCE
    exp["_source_slug"] = SLUG
    exp["traits"] = new_names
    page_map["tortle"] = want_map
    RACES.write_text(json.dumps(races, indent=2, ensure_ascii=False) + "\n")
    EXPORT.write_text(json.dumps(export, indent=2, ensure_ascii=False) + "\n")
    PAGE_MAP.write_text(json.dumps(page_map, indent=2, ensure_ascii=False) + "\n")
    print(f"\n  applied; backup {BACKUP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
