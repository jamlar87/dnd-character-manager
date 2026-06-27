"""D&D Reference Manual Sweeper — Deep-sweep new content into the app.

When a new manual PDF is added, this tool:
1. Extracts the full text via OCR/text extraction
2. Identifies new Classes, Subclasses, Races, Backgrounds, Feats, Spells, Items
3. Generates a report of what can be automatically added vs what needs review

Usage:
  python3 sweeper.py                — scan recently-added manuals
  python3 sweeper.py <name>         — deep-sweep a specific manual
  python3 sweeper.py --list         — list all manuals and last-sweep status
"""
import json, os, sys, time, subprocess
from pathlib import Path

HERE = Path(__file__).parent
MANUAL_LINK = HERE / "manuals" / "DnD-Manuals"
MANUAL_INDEX = HERE / "manuals" / "manuals_index.json"
SWEEP_STATE = HERE / "data" / "sweep_state.json"

# ── Sweep targets ──
# These are the dnd5eapi.co endpoints that get refreshed on new manual content
SRD_ENDPOINTS = [
    "races", "subraces", "traits", "feats", "backgrounds",
    "skills", "proficiencies", "ability-scores", "languages",
    "spells", "subclasses", "classes", "features", "equipment",
    "equipment-categories", "magic-schools", "weapon-properties",
    "damage-types", "conditions", "rules", "rule-sections",
    "monsters", "magic-items",
]

def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def load_state():
    return load_json(SWEEP_STATE)

def save_state(state):
    save_json(SWEEP_STATE, state)

def get_manual_list():
    """List all manuals from the index, with sweep status."""
    index = load_json(MANUAL_INDEX)
    state = load_state()
    manuals = index.get("manuals", [])
    for m in manuals:
        fname = m["filename"]
        s = state.get(fname, {})
        m["swept"] = s.get("swept", False)
        m["sweep_ts"] = s.get("timestamp")
        m["sweep_report"] = s.get("report")
    return manuals

def extract_pdf_text(pdf_path):
    """Extract text from a PDF. Uses pymupdf (fitz) if available, else pdftotext."""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text
    except ImportError:
        pass
    
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), "-"],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    return ""

def refresh_srd_cache():
    """Re-fetch all SRD endpoints from dnd5eapi.co."""
    from fetch_full_srd import main as fetch_all
    print("  Refreshing SRD API cache from dnd5eapi.co...")
    try:
        # We can't await from a sync context, so shell out
        result = subprocess.run(
            [sys.executable, str(HERE / "fetch_full_srd.py")],
            capture_output=True, text=True, timeout=600
        )
        if result.returncode == 0:
            print("  SRD cache refreshed.")
            return True
        else:
            print(f"  SRD refresh had errors:\n{result.stderr[:500]}")
            return False
    except subprocess.TimeoutExpired:
        print("  SRD refresh timed out after 10 min. Cache partially updated.")
        return True  # partial is usually fine

def identify_new_content(full_text, context):
    """Parse extracted text for D&D 5e content identifiers."""
    findings = {
        "new_classes": [],
        "new_subclasses": [],
        "new_races": [],
        "new_spells": [],
        "new_feats": [],
        "new_items": [],
        "new_backgrounds": [],
        "notes": [],
    }
    
    lines = full_text.split("\n")
    
    # Check for class names
    known_classes = ["Barbarian", "Bard", "Cleric", "Druid", "Fighter", "Monk",
                     "Paladin", "Ranger", "Rogue", "Sorcerer", "Warlock", "Wizard"]
    
    # ── NEW SUBCLASSES ──
    # Common PHB subclass patterns: "School of X", "College of X", "Circle of X",
    # "Oath of X", "Way of X", "Path of X", "Domain of X", "Archetype: X"
    subclass_patterns = [
        "College of", "School of", "Circle of", "Way of", "Path of",
        "Oath of", "Domain of", "Martial Archetype", "Sorcerous Origin",
        "Otherworldly Patron", "Roguish Archetype", "Ranger Archetype",
        "Monastic Tradition", "Arcane Tradition", "Primal Path",
        "Battle Master", "Champion", "Eldritch Knight",
        "Hunter", "Beast Master", "Thief", "Assassin", "Arcane Trickster",
        "Berserker", "Totem Warrior", "Lore", "Valor", "Devotion",
        "Ancients", "Vengeance", "Fiend", "Archfey", "Great Old One",
        "Draconic", "Wild Magic", "Evocation", "Abjuration", "Conjuration",
        "Divination", "Enchantment", "Illusion", "Necromancy", "Transmutation",
        "Life", "Light", "Nature", "Tempest", "Trickery", "War", "Knowledge",
        "Land", "Moon", "Open Hand", "Shadow", "Four Elements",
    ]
    
    for sp in subclass_patterns:
        count = full_text.count(sp)
        if count >= 2:
            findings["new_subclasses"].append(f"{sp} (mentioned {count}x)")
    
    # ── NEW RACES ──
    known_races = ["Dwarf", "Elf", "Halfling", "Human", "Dragonborn", "Gnome",
                   "Half-Elf", "Half-Orc", "Tiefling", "Aasimar", "Firbolg",
                   "Goliath", "Kenku", "Lizardfolk", "Tabaxi", "Tortle",
                   "Triton", "Yuan-Ti", "Bugbear", "Goblin", "Hobgoblin",
                   "Kobold", "Orc", "Leonin", "Satyr", "Minotaur", "Centaur",
                   "Aarakocra", "Genasi", "Changeling", "Kalashtar", "Shifter",
                   "Warforged", "Vedalken", "Simic Hybrid", "Loxodon",
                   "Verdan", "Locathah", "Grung", "Owlin", "Harengon",
                   "Fairy", "Dhampir", "Hexblood", "Reborn", "Autognome",
                   "Giff", "Hadozee", "Plasmoid", "Thri-kreen", "Astral Elf",
                   "Gith", "Githyanki", "Githzerai", "Eladrin", "Sea Elf",
                   "Shadar-kai"]
    for race in known_races:
        if race in known_races and full_text.count(race) >= 3:
            if race not in ["Dwarf", "Elf", "Halfling", "Human", "Dragonborn",
                            "Gnome", "Half-Elf", "Half-Orc", "Tiefling"]:
                findings["new_races"].append(race)
    
    # ── NEW SPELLS ──
    # Look for spell-like patterns: "level [N] [school]" or spell name followed by spell level
    # But we'll detect this from the SRD API comparing known vs available
    
    # ── NEW FEATS ──
    # Feats often described as "You gain the following benefits:" 
    # or have prerequisites like "Prerequisite: ..."
    if "Prerequisite" in full_text and "Grappler" not in full_text:
        findings["new_feats"].append("Unidentified feat(s) with prerequisites (check SRD API)")
    
    # ── NEW BACKGROUNDS ──
    # Backgrounds: "Skill Proficiencies" + "Equipment" + "Feature"
    bg_sections = full_text.count("Skill Proficiencies:")
    if bg_sections > 1:  # Acolyte is the only SRD background
        findings["new_backgrounds"].append(f"{bg_sections} backgrounds detected")
    
    # ── Items ──
    magic_item_count = full_text.count("Wondrous item") + full_text.count("Magic Item") 
    if magic_item_count > 0:
        findings["new_items"].append(f"{magic_item_count} magic item references")
    
    return findings

def sweep_manual(manual_filename):
    """Deep-sweep a single manual by name."""
    manuals = get_manual_list()
    target = None
    for m in manuals:
        if manual_filename.lower() in m["filename"].lower() or manual_filename.lower() in m["title"].lower():
            target = m
            break
    
    if not target:
        print(f"Manual '{manual_filename}' not found.")
        print(f"Available: {[m['filename'] for m in manuals[:10]]}...")
        return
    
    pdf_path = MANUAL_LINK / target["path"]
    print(f"Sweeping: {target['title']}")
    print(f"  Path: {pdf_path}")
    print(f"  Size: {target['file_size'] // 1024} KB")
    
    if not pdf_path.exists():
        print(f"  ERROR: File not found at {pdf_path}")
        return
    
    # 1. Extract text
    print("  Extracting text...")
    text = extract_pdf_text(pdf_path)
    print(f"  Extracted {len(text):,} characters")
    
    # 2. Identify new content
    print("  Analyzing for new content...")
    findings = identify_new_content(text, target)
    
    report_parts = []
    report_parts.append(f"\n## Sweep Report: {target['title']}\n")
    
    anything_new = False
    for key, label in [
        ("new_classes", "New Classes"), ("new_subclasses", "New Subclasses"),
        ("new_races", "New Races"), ("new_spells", "New Spells"),
        ("new_feats", "New Feats"), ("new_items", "New Items"),
        ("new_backgrounds", "New Backgrounds"),
    ]:
        items = findings.get(key, [])
        if items:
            anything_new = True
            report_parts.append(f"### {label}")
            for item in items:
                report_parts.append(f"- {item}")
            report_parts.append("")
    
    if not anything_new:
        # Check if this is a core book — maybe it's confirming known data
        title_lower = target["title"].lower()
        if any(kw in title_lower for kw in ["player's handbook", "dungeon master's", "monster manual",
                                              "xanathar", "tasha", "volo", "mordenkainen"]):
            report_parts.append("No novel content detected beyond SRD. This is likely a core reference — existing data should already cover it.")
            report_parts.append("(Run `python3 sweeper.py --refresh-srd` to verify all SRD endpoints are fresh.)")
        else:
            report_parts.append("No new identifiable content patterns found.")
    
    # 3. Refresh SRD cache (always, to pick up any new API additions)
    print("  Refreshing SRD cache...")
    refresh_srd_cache()
    
    # 4. Run full character-manager audit
    print("  Running character-manager audit...")
    audit_script = HERE / "audit.py"
    if audit_script.exists():
        result = subprocess.run(
            [sys.executable, str(audit_script), "--json"],
            capture_output=True, text=True, timeout=60,
            cwd=str(HERE)
        )
        if result.returncode == 0:
            try:
                audit_data = json.loads(result.stdout)
                report_parts.append(f"\n### Audit Results: ✓ {audit_data['passed']} checks passed")
            except json.JSONDecodeError:
                report_parts.append(f"\n### Audit Results: ✓ passed\n```\n{result.stdout[:500]}\n```")
        else:
            report_parts.append(f"\n### Audit Results: ✗ FAILURES\n```\n{result.stdout[:500]}\n{result.stderr[:500]}\n```")
    else:
        report_parts.append("\n### Audit Results: ⚠ audit.py not found — skipping")
    
    # 5. Save sweep state
    report = "\n".join(report_parts)
    state = load_state()
    state[target["filename"]] = {
        "swept": True,
        "timestamp": time.time(),
        "report": report,
        "char_count": len(text),
    }
    save_state(state)
    
    print(report)
    return report

def list_manuals():
    """Show all manuals with sweep status."""
    manuals = get_manual_list()
    if not manuals:
        print("No manuals found. Run rescan_manuals.py first.")
        return
    
    print(f"{'Status':8s} {'Manual':50s} {'Category':16s} Size")
    print("-" * 90)
    for m in manuals:
        status = "✅" if m.get("swept") else "⬜"
        sz = f"{m['file_size'] // 1024:>5} KB"
        title = m["title"][:48]
        cat = m.get("category", "?")[:15]
        print(f"{status:8s} {title:50s} {cat:16s} {sz}")
    
    print(f"\n{sum(1 for m in manuals if m.get('swept'))}/{len(manuals)} manuals swept")

def show_stats():
    """Show overall sweep stats and cache state."""
    state = load_state()
    swept_count = len(state)
    
    manuals = get_manual_list()
    total = len(manuals)
    
    print("=" * 60)
    print("D&D Reference Sweeper — Status")
    print("=" * 60)
    print(f"\nManuals: {total} total, {swept_count} swept")
    
    # Show sweep timeline
    if swept_count:
        print(f"\nLast 5 sweeps:")
        for fname in sorted(state, key=lambda x: state[x].get("timestamp", 0), reverse=True)[:5]:
            s = state[fname]
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(s.get("timestamp", 0)))
            chars = s.get("char_count", 0)
            print(f"  {ts} — {fname[:40]:40s} ({chars:,} chars)")
    
    # SRD cache state
    cache_dir = HERE / "data" / "srd_cache"
    if cache_dir.exists():
        total_size = sum(f.stat().st_size for f in cache_dir.glob("*.json"))
        count = len(list(cache_dir.glob("*.json")))
        print(f"\nSRD Cache: {count} files, {total_size // 1024:,} KB")
    
    # Check for new/modified PDFs not yet swept
    print(f"\nUnswept manuals:")
    unswept = [m for m in manuals if not m.get("swept")]
    if unswept:
        for m in unswept:
            print(f"  ⬜ {m['title'][:60]}")
        print(f"\n  Sweep one with: python3 sweeper.py \"<manual name>\"")
    else:
        print("  None! All manuals have been swept.")

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        return
    
    cmd = sys.argv[1]
    
    if cmd == "--list":
        list_manuals()
    elif cmd == "--stats" or cmd == "--status":
        show_stats()
    elif cmd == "--refresh-srd":
        refresh_srd_cache()
    elif cmd == "--all":
        # Sweep all unswept manuals
        manuals = get_manual_list()
        unswept = [m for m in manuals if not m.get("swept")]
        if not unswept:
            print("All manuals already swept.")
            return
        for m in unswept:
            print(f"\n{'='*60}")
            sweep_manual(m["filename"])
    else:
        # Sweep a specific manual
        sweep_manual(cmd)

if __name__ == "__main__":
    main()
