from __future__ import annotations
from pathlib import Path
import re
import sys
import time
from .core import CACHE_DIR, HERE, OUTPUT_DIR, SKIP_EXTRACTION, sys
from .dedup import _normalize_name
from .discover import discover_manuals
from .jsonio import _load_json, _load_json_list, _save_json
from .pdfmap import _build_pdf_map
from .process import process_manual


# --force lets merge_all_extractions() overwrite files it would otherwise shrink
_MERGE_FORCE: bool = False


# ═══════════════════════════════════════════════════════════════════════════════
# Merge all extractions into app-loadable files
# ═══════════════════════════════════════════════════════════════════════════════

def merge_all_extractions():
    """Scan all per-manual extractions and merge into data/manual_data/*.json."""
    print("\n" + "="*60)
    print("Merging all manual extractions...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    merged = {
        "races": [], "spells": [], "magic_items": [], "equipment": [],
        "monsters": [], "npcs": [], "feats": [], "backgrounds": [],
        "subclasses": [], "traps": [],
    }

    seen = {cat: set() for cat in merged}         # _normalize_name (keeps parens)
    seen_core = {cat: set() for cat in merged}     # stripped of parens (for Fireball ≈ Fireball (5th-level))
    raw_names = {cat: set() for cat in merged}     # track actual names for source reporting
    sources = {cat: {} for cat in merged}

    for ext_file in sorted(CACHE_DIR.glob("*_extracted.json")):
        data = _load_json(ext_file)
        if not isinstance(data, dict) or not data.get("_completed"):
            continue

        slug = data.get("_book_slug", ext_file.stem.replace("_extracted", ""))
        for cat in merged:
            for item in data.get(cat, []):
                name = item.get("name", "")
                name_norm = _normalize_name(name)       # keeps parentheticals
                name_core = re.sub(r'\s*\([^)]*\)', '', name.lower()).strip()  # strips them
                if not name_norm:
                    continue

                # Exact normalized match (keeps parens) → real duplicate
                if name_norm in seen[cat]:
                    # False-positive guard: plain name (no paren) matching only
                    # variant entries like "Criminal" vs "Criminal (Myriad Operative)"
                    if '(' not in name:
                        # Check if a plain-core version already exists
                        if name_core in seen_core[cat]:
                            continue  # real duplicate: plain name already exists
                        # else: only variants exist → keep plain name
                    else:
                        continue  # current has parens → real duplicate

                # Tertiary check: decorated name matching a plain core entry
                # e.g. "Fireball (5th-level)" from a module matching PHB's "Fireball"
                if '(' in name and name_core in seen_core[cat]:
                    continue

                seen[cat].add(name_norm)
                seen_core[cat].add(name_core)
                raw_names[cat].add(name.lower())
                item["_source_manual"] = slug  # Record which PDF this came from
                merged[cat].append(item)
                sources[cat][name_norm] = slug

    # ── Preserve existing data for categories with no new extractions ──
    # If a category has no entries from PDF extractions but a file already
    # exists on disk, load it to prevent accidental data loss (e.g. traps
    # that were created outside the ingestion pipeline).
    for cat in list(merged.keys()):
        if not merged[cat]:
            existing_path = OUTPUT_DIR / f"{cat}.json"
            if existing_path.exists():
                existing_data = _load_json(existing_path)
                if isinstance(existing_data, list) and existing_data:
                    merged[cat] = existing_data
                    for item in existing_data:
                        name = item.get("name", "")
                        name_norm = _normalize_name(name)
                        if name_norm:
                            seen[cat].add(name_norm)
                    print(f"  Preserved {len(existing_data)} existing {cat} entries (no new extractions)")

    # ── Spell classes cross-reference: if extracted spells lack classes,
    #     backfill from SRD cache (which has per-class spell lists) ────────
    srd_spells = _load_json_list(HERE / 'data' / 'srd_cache' / 'spells.json')
    if srd_spells:
        srd_by_name: dict[str, list[str]] = {}
        for ss in srd_spells:
            sname = ss.get('name', '').lower()
            sclasses = [c.get('name', '') for c in ss.get('classes', [])]
            if sname and sclasses:
                srd_by_name[sname] = sclasses

        backfilled = 0
        for spell in merged['spells']:
            if spell.get('classes'):
                continue  # Already has classes
            key = spell.get('name', '').lower()
            if key in srd_by_name:
                spell['classes'] = srd_by_name[key]
                backfilled += 1
        if backfilled:
            print(f'  Backfilled classes for {backfilled} spell(s) from SRD')

    # ── Source normalization (inline during merge) ──────────────────────
    _normalize_merged_sources(merged)

    # ── Safety: never silently SHRINK a merged data file ────────────────
    # This rebuild only sees the extractions still present in
    # data/manual_cache/. Old ones get pruned over time, so a rebuild from a
    # partial cache DELETES every book that is no longer cached (observed:
    # monsters 1540 → 118, spells 324 → 20, magic_items 738 → 172). Refuse
    # unless --force; to fold ONE new book in, use
    # scripts/append_extraction.py <SLUG> instead.
    shrunk: dict[str, tuple[int, int]] = {}
    for cat, items in merged.items():
        path = OUTPUT_DIR / f"{cat}.json"
        if not path.exists():
            continue
        existing = _load_json(path)
        if isinstance(existing, list) and len(existing) > len(items):
            shrunk[cat] = (len(existing), len(items))
    if shrunk and not _MERGE_FORCE:
        print("\nREFUSING TO MERGE — rebuild would SHRINK existing data files:")
        for cat, (old, new) in sorted(shrunk.items()):
            print(f"  {cat}.json: {old} → {new} entries")
        print("  Cause: extractions pruned from data/manual_cache/ (or old-format caches).")
        print("  Add ONE new book safely: python3 scripts/append_extraction.py <SLUG>")
        print("  Overwrite anyway:        python3 ingest_manual.py --merge --force")
        return

    # Save merged files
    for cat, items in merged.items():
        path = OUTPUT_DIR / f"{cat}.json"
        _save_json(path, items)

    # ── Build pdf_map for downstream tooling ────────────────────────────
    pdf_map = _build_pdf_map()

    # Save metadata
    meta = {
        "merged_at": time.time(),
        "source_manuals": list(set(
            data.get("_book_slug", "")
            for ext_file in sorted(CACHE_DIR.glob("*_extracted.json"))
            if (data := _load_json(ext_file)) and data.get("_completed")
        )),
        "totals": {cat: len(items) for cat, items in merged.items()},
        "pdf_map": pdf_map,
    }
    _save_json(OUTPUT_DIR / "_meta.json", meta)

    print(f"\nMerged data written to {OUTPUT_DIR}/")
    for cat, items in sorted(merged.items()):
        if items:
            print(f"  {cat}.json: {len(items)} entries")

    # ── Post-merge fixups: clean garbled data, fill known gaps ───────────
    _apply_post_merge_fixups(OUTPUT_DIR)


# ═══════════════════════════════════════════════════════════════════════════════
# Status / listing
# ═══════════════════════════════════════════════════════════════════════════════

def list_manuals():
    """List all manuals with extraction status."""
    manuals = discover_manuals()
    if not manuals:
        print("No manuals found.")
        return

    print(f"{'Status':10s} {'Slug':6s} {'Manual':48s} {'Size':>8s}  Chunks")
    print("-" * 90)

    for m in manuals:
        ext_path = CACHE_DIR / f"{m['slug']}_extracted.json"
        if ext_path.exists():
            data = _load_json(ext_path)
            if data.get("_completed"):
                total = sum(len(v) for k, v in data.items()
                           if isinstance(v, list) and not k.startswith("_"))
                chapters_done = len(data.get('_chapters_completed', []))
                chapters_total = data.get('_total_chapters', '?')
                errors = len(data.get('_chapter_errors', {}))
                extra = f"{chapters_done}/{chapters_total} chapters, {total} entries"
                if errors:
                    extra += f", {errors} errors"
                status = "✅ done"
                chunks = extra
            else:
                chapters_done = len(data.get("_chapters_completed", []))
                chapters_total = data.get("_total_chapters", "?")
                errors = len(data.get("_chapter_errors", {}))
                current = data.get("_current_chapter", "")
                status = f"🔄 {chapters_done}/{chapters_total}ch"
                if current:
                    status += f" @{current[:12]}"
                if errors:
                    status += f" ⚠️{errors}"
                chunks = ""
        elif m["slug"] in SKIP_EXTRACTION:
            status = "⏭️ SKIP"
            chunks = "(SRD base)"
        else:
            status = "⬜ NEW"
            chunks = ""

        print(f"{status:10s} {m['slug']:6s} {m['title'][:46]:48s} {m['size_kb']:>5} KB  {chunks}")

    print(f"\n{len(manuals)} manuals total")
    print("Run with a manual name to ingest, or --all for all new, or --merge to rebuild merged data.")


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    global _MERGE_FORCE
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        return

    _MERGE_FORCE = "--force" in sys.argv
    cmd = sys.argv[1]

    if cmd == "--list":
        list_manuals()
        return

    if cmd == "--merge":
        merge_all_extractions()
        return

    if cmd == "--all":
        manuals = discover_manuals()
        for m in manuals:
            if m["slug"] in SKIP_EXTRACTION:
                continue
            ext_path = CACHE_DIR / f"{m['slug']}_extracted.json"
            if ext_path.exists():
                data = _load_json(ext_path)
                if data.get("_completed"):
                    print(f"Skipping {m['title']} — already extracted")
                    continue
            process_manual(m)
        merge_all_extractions()
        return

    # Process a specific manual by name/slug
    query = cmd.lower()
    manuals = discover_manuals()
    for m in manuals:
        if (query in m["title"].lower() or
            query in m["slug"].lower() or
            query in m["filename"].lower()):
            process_manual(m)
            merge_all_extractions()
            return

    print(f"Manual '{cmd}' not found. Use --list to see available manuals.")


# ── Source normalization helpers ───────────────────────────────────────

def _normalize_merged_sources(merged: dict) -> None:
    """Normalize source strings to (Manual, pg#) format during merge."""
    import re

    # Complete slug → display name map (must mirror _get_source_slug_map in main.py)
    book_title_map = {
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
        "FGFD": "Field Guide to Floral Dragons",
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
    title_lookup = {k.upper(): v for k, v in book_title_map.items()}
    
    # Garbage patterns — sources that should be dropped
    garbage_prefixes = [
        "unknown", "introductory text", "homebrew", "fragment",
        "james larsen", "text provided", "page not determinable",
        "sourcebook (page", "adventure p.?", "unknown page",
    ]
    
    page_re = re.compile(r'(?:p\.?\s*|page\s+)(\d+)', re.IGNORECASE)
    
    fixed = 0
    for _cat, items in merged.items():
        for item in items:
            src = (item.get("source") or "").strip()
            slug = (item.get("_source_manual") or "").strip()
            
            display = title_lookup.get(slug.upper()) if slug else None
            
            # Extract page number if present
            page = None
            if src:
                m = page_re.search(src)
                if m:
                    page = m.group(1)
            
            # Check if source is garbage
            is_garbage = False
            if src:
                sl = src.lower()
                is_garbage = any(sl.startswith(g) or g in sl for g in garbage_prefixes)
            
            if display and page:
                item["source"] = f"({display}, p.{page})"
                fixed += 1
            elif display and not is_garbage and src and src.startswith("("):
                pass  # Already formatted, keep as-is
            elif display:
                item["source"] = f"({display})"
                fixed += 1
            elif is_garbage or not src:
                item["source"] = ""
                fixed += 1
            # else: no slug, no page, keep as-is but flag
    
    if fixed:
        print(f"  Normalized {fixed} source string(s) to (Manual, pg#) format")


def _apply_post_merge_fixups(output_dir: Path) -> None:
    """Apply data quality fixups to merged files after each merge.
    
    These fill known gaps (missing costs, garbled entries, race ASIs, etc.)
    that the LLM extraction can't reliably produce from PDF text.
    Edit the fixup data below as new gaps are discovered.
    """
    
    # ── Equipment: remove garbled entries, fill known costs ──────────
    equip_path = output_dir / "equipment.json"
    if equip_path.exists():
        equipment = _load_json(equip_path)
        if isinstance(equipment, list):
            # Remove garbled entries (weapon properties extracted as items)
            garbled_patterns = ["ammunition (range", "unarmed strike"]
            before = len(equipment)
            equipment = [e for e in equipment 
                        if not any(p in e.get("name", "").lower() for p in garbled_patterns)]
            removed = before - len(equipment)
            
            # Known equipment costs (not reliably extractable from PDFs)
            known_costs = {
                # DMG siege weapons
                "ballista": "50 gp", "cannon": "500 gp", "cauldron, suspended": "150 gp",
                "mangonel": "100 gp", "ram": "100 gp", "siege tower": "1500 gp", "trebuchet": "500 gp",
                "bomb": "150 gp", "gunpowder": "100 gp", "dynamite": "150 gp",
                "grenade, fragmentation": "200 gp", "grenade, smoke": "100 gp",
                # PHB armor
                "padded": "5 gp", "leather": "10 gp", "studded leather": "45 gp",
                "arcane focus": "5 gp", "druidic focus": "5 gp", "holy symbol": "5 gp",
                "potion of healing": "50 gp", "bottle": "2 gp",
                # SCAG instruments
                "birdpipes": "12 gp", "glaur": "3 gp", "hand drum": "6 gp",
                "longhorn": "6 gp", "songhorn": "6 gp", "tantan": "6 gp",
                "thelarr": "3 gp", "tocken": "6 gp", "wargong": "3 gp",
                "yarting": "3 gp", "zulkoon": "3 gp",
            }
            cost_filled = 0
            for e in equipment:
                name = e.get("name", "").lower()
                if (not e.get("cost") or e["cost"] == "") and name in known_costs:
                    e["cost"] = known_costs[name]
                    cost_filled += 1
                elif not e.get("cost") or e["cost"] == "":
                    e["cost"] = "\u2014"  # em dash for "not purchasable"
            
            _save_json(equip_path, equipment)
            if removed or cost_filled:
                print(f"  Equipment fixups: {removed} garbled removed, {cost_filled} costs filled")
    
    # ── Spells: fix known corrupt names ─────────────────────────────
    spells_path = output_dir / "spells.json"
    if spells_path.exists():
        spells = _load_json(spells_path)
        if isinstance(spells, list):
            fixed = 0
            for s in spells:
                if s.get("name", "").lower() == "jlaming sphere":
                    s["name"] = "Flaming Sphere"
                    s["school"] = "evocation"
                    fixed += 1
            if fixed:
                _save_json(spells_path, spells)
                print(f"  Spell fixups: {fixed} corrupt name(s) corrected")
    
    # ── Races: fill known missing ASI/traits ─────────────────────────
    races_path = output_dir / "races.json"
    if races_path.exists():
        races = _load_json(races_path)
        if isinstance(races, list):
            fixed = 0
            for r in races:
                name = r.get("name", "")
                if name == "Windrunner Elf":
                    if not r.get("asi") or len(r.get("asi", {})) == 0:
                        r["asi"] = {"dexterity": 2, "wisdom": 1}
                        fixed += 1
                    if not r.get("traits") or len(r.get("traits", [])) == 0:
                        r["traits"] = [{"name": "Fleet of Foot"}, {"name": "Mask of the Wild"}]
                if name == "Tlincalli":
                    if not r.get("traits") or len(r.get("traits", [])) == 0:
                        r["traits"] = [{"name": "Natural Armor"}]
                        fixed += 1
            if fixed:
                _save_json(races_path, races)
                print(f"  Race fixups: {fixed} gap(s) filled")
    
    # ── Feats: fix OCR-garbled PHB descriptions ──────────────────────
    feats_path = output_dir / "feats.json"
    if feats_path.exists():
        feats = _load_json(feats_path)
        if isinstance(feats, list):
            _feat_fixes = {
                "SHARPSHOOTER": {
                    "name": "Sharpshooter", "prerequisite": "",
                    "description": (
                        "You have mastered ranged weapons and can make shots that "
                        "others find impossible. You gain the following benefits:\n"
                        "• Attacking at long range doesn't impose disadvantage on "
                        "your ranged weapon attack rolls.\n"
                        "• Your ranged weapon attacks ignore half cover and "
                        "three-quarters cover.\n"
                        "• Before you make an attack with a ranged weapon that you "
                        "are proficient with, you can choose to take a -5 penalty "
                        "to the attack roll. If the attack hits, you add +10 to "
                        "the attack's damage."
                    ),
                    "source": "PHB 2014 p.170", "_source_manual": "PHB",
                },
                "SPELL SNIPER": {
                    "name": "Spell Sniper",
                    "prerequisite": "The ability to cast at least one spell",
                    "description": (
                        "You have learned techniques to enhance your attacks with "
                        "certain kinds of spells, gaining the following benefits:\n"
                        "• When you cast a spell that requires you to make an "
                        "attack roll, the spell's range is doubled.\n"
                        "• Your ranged spell attacks ignore half cover and "
                        "three-quarters cover.\n"
                        "• You learn one cantrip that requires an attack roll. "
                        "Choose the cantrip from the bard, cleric, druid, sorcerer, "
                        "warlock, or wizard spell list. Your spellcasting ability "
                        "for this cantrip depends on the spell list you chose from: "
                        "Charisma for bard, sorcerer, or warlock; Wisdom for cleric "
                        "or druid; or Intelligence for wizard."
                    ),
                    "source": "PHB 2014 p.170", "_source_manual": "PHB",
                },
                "Crossbow Expert": {
                    "name": "Crossbow Expert", "prerequisite": "",
                    "description": (
                        "Thanks to extensive practice with the crossbow, you gain "
                        "the following benefits:\n"
                        "• You ignore the loading quality of crossbows with which "
                        "you are proficient.\n"
                        "• Being within 5 feet of a hostile creature doesn't impose "
                        "disadvantage on your ranged attack rolls.\n"
                        "• When you use the Attack action and attack with a "
                        "one-handed weapon, you can use a bonus action to attack "
                        "with a hand crossbow you are holding."
                    ),
                    "source": "PHB 2014 p.166", "_source_manual": "PHB",
                },
            }
            fixed = 0
            for i, f in enumerate(feats):
                name = f.get("name", "")
                # Match both OCR (ALL CAPS) and clean (Title Case) versions
                fix = _feat_fixes.get(name) or _feat_fixes.get(name.upper())
                if fix:
                    feats[i] = fix
                    fixed += 1
            if fixed:
                _save_json(feats_path, feats)
                print(f"  Feat fixups: {fixed} OCR-garbled entry(s) replaced")

    # ── Magic items: fill missing rarities ──────────────────────────
    items_path = output_dir / "magic_items.json"
    if items_path.exists():
        items = _load_json(items_path)
        if isinstance(items, list):
            fixed = 0
            for i in items:
                if not i.get("rarity") or i["rarity"] == "":
                    i["rarity"] = "varies"
                    fixed += 1
            if fixed:
                _save_json(items_path, items)
                print(f"  Magic item fixups: {fixed} rarity gap(s) filled")

    # ── Cross-reference validation ──────────────────────────────────
    # Check subclass→class references, spell→class references for known class names
    _xref_known_classes = {
        "Barbarian","Bard","Cleric","Druid","Fighter","Monk","Paladin",
        "Ranger","Rogue","Sorcerer","Warlock","Wizard",
        # AiME / homebrew classes
        "Scholar","Slayer","Warden","Troubadour","Marauder",
        "Spirit Dancer","Skald","Treasure Hunter",
    }
    _xref_issues = 0
    # Subclasses: check parent class exists
    _sc_path = output_dir / "subclasses.json"
    if _sc_path.exists():
        for _sc in _load_json(_sc_path):
            _paren = _sc.get("class", "")
            if _paren and _paren not in _xref_known_classes:
                _xref_issues += 1
                if _xref_issues <= 5:
                    print(f"  ⚠ Cross-ref: subclass '{_sc.get('name','?')}' references unknown class '{_paren}'")
    # Spells: check class references are valid
    _spell_path = output_dir / "spells.json"
    if _spell_path.exists():
        for _s in _load_json(_spell_path):
            for _c in _s.get("classes", []):
                if isinstance(_c, dict):
                    _c = _c.get("name", "")
                if _c and _c not in _xref_known_classes:
                    _xref_issues += 1
                    if _xref_issues <= 5:
                        print(f"  ⚠ Cross-ref: spell '{_s.get('name','?')}' references unknown class '{_c}'")
    if _xref_issues:
        print(f"  ({_xref_issues} total cross-reference issues flagged)")
