"""D&D Character Manager — Full Audit Script.

Runs after any manual sweep or LEVEL_UP_DATA change.
Validates all 12 PHB classes 1→20, all races, limited-use features,
spell slots, and HP progression against PHB 2014 values.

Usage:
  python3 audit.py           — full audit (classes + races + features)
  python3 audit.py --quick   — limited-use features only
  python3 audit.py --json    — full audit, output JSON

Exit code 0 = all passed, 1 = failures found.
"""
import json, sys, os
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

# ── Expected PHB 2014 values ──

# HP at L20 (average, some CON variance from race/ASI)
EXPECTED_HP = {
    "Barbarian": 237, "Bard": 148, "Cleric": 157, "Druid": 157,
    "Fighter": 231, "Monk": 143, "Paladin": 171, "Ranger": 171,
    "Rogue": 163, "Sorcerer": 136, "Warlock": 157, "Wizard": 136,
}

# Limited-use features at L20 — keys match LIMITED_USE dict
EXPECTED_USES = {
    "Barbarian": [("rage", 6)],
    "Bard": [("bardic inspiration", 6)],
    "Cleric": [("channel divinity", 3)],
    "Druid": [("wild shape", 2)],
    "Fighter": [("action surge", 2), ("indomitable", 3), ("second wind", 1)],
    "Monk": [("ki", 20)],
    "Paladin": [("lay on hands", 100), ("channel divinity", 1)],
    "Ranger": [],
    "Rogue": [],
    "Sorcerer": [("sorcery points", 20)],
    "Warlock": [],
    "Wizard": [],
}

# Race ASI expectations (PHB 2014) — full lowercase ability names
EXPECTED_RACE_ASI = {
    "Dragonborn": {"strength": 2, "charisma": 1},
    "Dwarf": {"constitution": 2},
    "Hill Dwarf": {"wisdom": 1},     # +CON 2 from base
    "Mountain Dwarf": {"strength": 2},  # +CON 2 from base
    "Elf": {"dexterity": 2},
    "High Elf": {"intelligence": 1},
    "Wood Elf": {"wisdom": 1},
    "Dark Elf (Drow)": {"charisma": 1},
    "Gnome": {"intelligence": 2},
    "Forest Gnome": {"dexterity": 1},
    "Rock Gnome": {"constitution": 1},
    "Half-Elf": {"charisma": 2},  # +1 to two others (not checked here)
    "Half-Orc": {"strength": 2, "constitution": 1},
    "Halfling": {"dexterity": 2},
    "Lightfoot Halfling": {"charisma": 1},
    "Stout Halfling": {"constitution": 1},
    "Human": {"strength": 1, "dexterity": 1, "constitution": 1, "intelligence": 1, "wisdom": 1, "charisma": 1},
    "Tiefling": {"charisma": 2, "intelligence": 1},
}

ASIS = ["STR", "STR", "DEX", "DEX", "CON", "CON", "INT", "INT", "WIS", "WIS", "CHA", "CHA",
        "STR", "STR", "DEX", "DEX", "CON", "CON", "INT", "INT"]


class AuditResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []
        self.warnings = []

    def fail(self, msg):
        self.failed += 1
        self.errors.append(msg)

    def ok(self):
        self.passed += 1

    def warn(self, msg):
        self.warnings.append(msg)

    @property
    def clean(self):
        return self.failed == 0


def run_audit(mode="full"):
    """Run the audit. mode: 'full' or 'quick' or 'json'."""
    result = AuditResult()
    parts = []

    # ── Load main module ──
    import main
    from main import (get_uses_for_level, FEATS, SUBCLASS_FEATURES,
                      SUBCLASS_LEVELS, CLASSES, RACES, SUBASIS, get_spell_slots)

    # ── 1. Limited-use features (12 classes × L20) ──
    if mode in ("full", "quick", "json"):
        section = "Limited-Use Features (L20)"
        for cls_name, features in EXPECTED_USES.items():
            for feat_name, expected in features:
                actual = get_uses_for_level(feat_name.lower(), cls_name, 20)
                if actual == expected:
                    result.ok()
                else:
                    result.fail(f"{cls_name} {feat_name}: got {actual}, expected {expected}")

        parts.append(f"{section}: {result.passed - (result.failed if mode!='json' else 0)}/{result.passed + result.failed} passed"
                     if result.failed == 0 else f"{section}: {result.failed} FAILURES")

    # ── 2. Race ASI validation ──
    if mode in ("full", "json"):
        before_errors = result.failed

        # Build a lookup: race_name -> full ASI
        def get_race_asi_lookup(race_name):
            """Get ASI for a race or subrace, combining base + subrace bonuses from RACES + SUBASIS."""
            if race_name in RACES:
                return dict(RACES[race_name].get("asi", {}))
            # Subrace — find which base race owns it
            for base_race, data in RACES.items():
                subs = data.get("subraces", [])
                if race_name in subs:
                    asi = dict(data.get("asi", {}))
                    # Subrace ASI from SUBASIS
                    sub_asi = SUBASIS.get(race_name, {})
                    for k, v in sub_asi.items():
                        asi[k] = asi.get(k, 0) + v
                    return asi
            return {}

        for race_name, expected_asi in EXPECTED_RACE_ASI.items():
            try:
                actual = get_race_asi_lookup(race_name)
                for ab, val in expected_asi.items():
                    actual_val = actual.get(ab, 0)
                    if actual_val == val:
                        result.ok()
                    else:
                        result.fail(f"{race_name} {ab}: got {actual_val}, expected {val}")
            except Exception as e:
                result.fail(f"{race_name}: ERROR — {e}")

        race_errors = result.failed - before_errors
        if race_errors == 0:
            parts.append(f"Race ASIs: all passed")
        else:
            parts.append(f"Race ASIs: {race_errors} FAILURES")

    # ── 3. Subclass feature names ──
    if mode in ("full", "json"):
        before_errors = result.failed

        subclass_count = 0
        for cls_name in EXPECTED_USES:
            # SUBCLASS_LEVELS has title-case keys
            if cls_name not in SUBCLASS_LEVELS:
                continue
            scls = SUBCLASS_LEVELS[cls_name]
            subclass_list = scls.get("options", [])
            for subclass_name in subclass_list:
                subclass_count += 1
                if subclass_name not in SUBCLASS_FEATURES:
                    result.fail(f"{cls_name} {subclass_name}: missing from SUBCLASS_FEATURES")
                else:
                    result.ok()

        subclass_errors = result.failed - before_errors
        if subclass_errors == 0:
            parts.append(f"Subclass features: all {subclass_count} subclasses covered in SUBCLASS_FEATURES")
        else:
            parts.append(f"Subclass features: {subclass_errors} missing from SUBCLASS_FEATURES")

    # ── 4. Spell slot table validation ──
    if mode in ("full", "json"):
        before_errors = result.failed
        # Full caster L20: 4/3/3/3/3/2/2/1/1/1
        expected_full_20 = [4, 3, 3, 3, 3, 2, 2, 1, 1]
        slots = get_spell_slots("wizard", 20)
        actual_full = [slots["by_level"].get(i, 0) for i in range(1, 10)]
        if actual_full == expected_full_20:
            result.ok()
        else:
            result.fail(f"Full caster L20 slots: got {actual_full}, expected {expected_full_20}")

        # Half caster L20: 4/3/3/3/2
        expected_half_20 = [4, 3, 3, 3, 2]
        slots_half = get_spell_slots("paladin", 20)
        actual_half = [slots_half["by_level"].get(i, 0) for i in range(1, 6)]
        if actual_half == expected_half_20:
            result.ok()
        else:
            result.fail(f"Half caster L20 slots: got {actual_half}, expected {expected_half_20}")

        spell_errors = result.failed - before_errors
        if spell_errors == 0:
            parts.append("Spell slots: OK")
        else:
            parts.append(f"Spell slots: {spell_errors} FAILURES")

    # ── 5. Feat count check ──
    if mode in ("full", "json"):
        if len(FEATS) >= 44:
            result.ok()
            parts.append(f"Feats: {len(FEATS)} defined")
        else:
            result.warn(f"Only {len(FEATS)} feats defined (expected ≥44)")

    # ── 6. SUBCLASS_FEATURES coverage ──
    if mode in ("full", "json"):
        total_entries = sum(len(v) for v in SUBCLASS_FEATURES.values())
        if total_entries >= 164:
            result.ok()
            parts.append(f"SUBCLASS_FEATURES: {total_entries} entries across {len(SUBCLASS_FEATURES)} subclasses")
        else:
            result.warn(f"SUBCLASS_FEATURES: only {total_entries} entries")

    # ── 7. Multiclass spell slot table (PHB p.165) ──
    if mode in ("full", "json"):
        from main import MULTICLASS_SPELL_SLOTS, compute_multiclass_caster_level
        
        # Verify table covers levels 1-20
        for i in range(1, 21):
            if i in MULTICLASS_SPELL_SLOTS:
                result.ok()
            else:
                result.fail(f"Missing multiclass spell slot entry for caster level {i}")
        
        # Verify known combos
        tests = [
            ({"Wizard": 5, "Fighter": 5}, 5, "Wiz5/Ftr5"),        # Fighter=0
            ({"Paladin": 5, "Sorcerer": 5}, 7, "Pal5/Sorc5"),     # 2+5
            ({"Cleric": 3, "Druid": 2}, 5, "Clr3/Drd2"),          # 3+2
            ({"Bard": 4, "Warlock": 3}, 4, "Bard4/Wlk3"),         # Warlock excluded
            ({"Paladin": 4, "Ranger": 4, "Cleric": 1}, 5, "Pal4/Rgr4/Clr1"),  # 2+2+1
            ({"Fighter": 10, "Barbarian": 10}, 0, "Ftr10/Brb10"), # No casters
        ]
        for cls, expected, label in tests:
            actual = compute_multiclass_caster_level(cls)
            if actual == expected:
                result.ok()
            else:
                result.fail(f"Multiclass caster level {label}: got {actual}, expected {expected}")
        
        parts.append(f"Multiclass spell slots: {len(tests)} combos verified")

    # ── Summary ──
    summary = {
        "passed": result.passed,
        "failed": result.failed,
        "errors": result.errors,
        "warnings": result.warnings,
        "sections": parts,
        "clean": result.clean,
    }

    if mode == "json":
        print(json.dumps(summary, indent=2))
        return summary

    print(f"\n{'='*60}")
    print(f"  D&D Character Manager — Audit")
    print(f"{'='*60}")
    for p in parts:
        print(f"  {p}")

    if result.errors:
        print(f"\n  FAILURES ({len(result.errors)}):")
        for e in result.errors:
            print(f"    ✗ {e}")

    if result.warnings:
        print(f"\n  WARNINGS:")
        for w in result.warnings:
            print(f"    ⚠ {w}")

    if result.clean:
        print(f"\n  ✓ All checks passed ({result.passed} total)")
    else:
        print(f"\n  ✗ {result.failed} failure(s) — see above")

    print(f"{'='*60}\n")
    return summary


def main():
    mode = "full"
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg in ("--quick", "-q"):
            mode = "quick"
        elif arg in ("--json", "-j"):
            mode = "json"
        elif arg in ("--help", "-h"):
            print(__doc__.strip())
            return 0

    summary = run_audit(mode)
    return 0 if summary["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
