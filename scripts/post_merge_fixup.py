#!/usr/bin/env python3
"""Post-merge fixup: repair known LLM/OCR extraction artifacts in merged data.

Run after `ingest_manual.py --merge` or `batch_ingest.py --merge` to fix
garbled entries that the extraction pipeline can't clean on its own.
Idempotent — safe to run multiple times.

Add new fixes below as extraction quality issues are discovered.
"""

import json
import sys
from pathlib import Path

MANUAL_DATA = Path(__file__).resolve().parent.parent / "data" / "manual_data"

# ── OCR-Garbled Feat Fixes ──────────────────────────────────────────
# PHB feats extracted from a PDF with poor OCR layer. These replace the
# garbled text with clean PHB verbatim descriptions.

FEAT_FIXES = {
    "SHARPSHOOTER": {
        "name": "Sharpshooter",
        "prerequisite": "",
        "description": (
            "You have mastered ranged weapons and can make shots that others "
            "find impossible. You gain the following benefits:\n"
            "• Attacking at long range doesn't impose disadvantage on your "
            "ranged weapon attack rolls.\n"
            "• Your ranged weapon attacks ignore half cover and three-quarters "
            "cover.\n"
            "• Before you make an attack with a ranged weapon that you are "
            "proficient with, you can choose to take a -5 penalty to the "
            "attack roll. If the attack hits, you add +10 to the attack's damage."
        ),
        "source": "PHB 2014 p.170",
        "_source_manual": "PHB",
    },
    "SPELL SNIPER": {
        "name": "Spell Sniper",
        "prerequisite": "The ability to cast at least one spell",
        "description": (
            "You have learned techniques to enhance your attacks with certain "
            "kinds of spells, gaining the following benefits:\n"
            "• When you cast a spell that requires you to make an attack roll, "
            "the spell's range is doubled.\n"
            "• Your ranged spell attacks ignore half cover and three-quarters "
            "cover.\n"
            "• You learn one cantrip that requires an attack roll. Choose the "
            "cantrip from the bard, cleric, druid, sorcerer, warlock, or "
            "wizard spell list. Your spellcasting ability for this cantrip "
            "depends on the spell list you chose from: Charisma for bard, "
            "sorcerer, or warlock; Wisdom for cleric or druid; or "
            "Intelligence for wizard."
        ),
        "source": "PHB 2014 p.170",
        "_source_manual": "PHB",
    },
    "Crossbow Expert": {
        "name": "Crossbow Expert",
        "prerequisite": "",
        "description": (
            "Thanks to extensive practice with the crossbow, you gain the "
            "following benefits:\n"
            "• You ignore the loading quality of crossbows with which you are "
            "proficient.\n"
            "• Being within 5 feet of a hostile creature doesn't impose "
            "disadvantage on your ranged attack rolls.\n"
            "• When you use the Attack action and attack with a one-handed "
            "weapon, you can use a bonus action to attack with a hand crossbow "
            "you are holding."
        ),
        "source": "PHB 2014 p.166",
        "_source_manual": "PHB",
    },
}


def fix_feats(feats_path: Path) -> int:
    """Replace OCR-garbled feat entries with clean versions."""
    if not feats_path.exists():
        print(f"  (feats.json not found at {feats_path}, skipping)")
        return 0

    feats = json.loads(feats_path.read_text())
    fixed = 0

    for i, feat in enumerate(feats):
        name = feat.get("name", "")
        if name in FEAT_FIXES:
            feats[i] = FEAT_FIXES[name]
            fixed += 1
            print(f"  ✓ Fixed feat: {name}")

    if fixed:
        feats_path.write_text(json.dumps(feats, indent=2, ensure_ascii=False))
        print(f"  Feat fixes: {fixed} entry(s) repaired")
    else:
        print(f"  Feats: all clean (no OCR fixes needed)")

    return fixed


# ── Extension point ──────────────────────────────────────────────────
# Add more fix_*() functions here as new extraction quality issues are
# discovered. Follow the pattern:
#   1. Detect the bad entries
#   2. Replace with clean data
#   3. Print what was fixed
#   4. Return count of fixes


def main() -> int:
    print("[post_merge_fixup]")
    total = 0

    total += fix_feats(MANUAL_DATA / "feats.json")

    # Future fixup sections go here:
    # total += fix_equipment_garbled(MANUAL_DATA / "equipment.json")
    # total += fix_spell_sources(MANUAL_DATA / "spells.json")

    print(f"[post_merge_fixup] Done — {total} fix(es) applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
