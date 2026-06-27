#!/usr/bin/env python3
"""Check why the LLM couldn't find descriptions for specific monsters."""

import re, json, sys
from pathlib import Path

HERE = Path(__file__).parent.parent
CACHE_DIR = HERE / "data" / "manual_cache"

def check_monster(slug: str, monster_name: str):
    text = (CACHE_DIR / f"{slug}.txt").read_text(errors='replace')
    
    for match in re.finditer(re.escape(monster_name), text, re.IGNORECASE):
        start = max(0, match.start() - 400)
        end = min(len(text), match.end() + 400)
        
        # Snap to page boundary
        pm = text.rfind("--- PAGE ", max(0, start - 200), start)
        if pm >= 0:
            start = pm
        
        pn = text.find("--- PAGE ", end, end + 200)
        if pn >= 0:
            end = pn
        
        context = text[start:end].strip()
        print(f"=== Context around '{monster_name}' in {slug}.txt ===")
        print(context[:2000])
        print(f"\n--- Total context length: {len(context)} chars ---")
        
        # Check if this looks like a stat block (lots of numbers) or flavor text
        stat_block_indicators = ['Armor Class', 'Hit Points', 'STR', 'DEX', 'CON', 'INT', 'WIS', 'CHA',
                                'AC ', 'HP ', 'Speed', 'Senses', 'Languages', 'Challenge',
                                'STR', 'DEX', 'CON', 'INT', 'WIS', 'CHA']
        indicator_count = sum(1 for ind in stat_block_indicators if ind in context[:500])
        lines_before = context[:match.start() - start].strip()
        print(f"\nStat block indicators in first 500 chars: {indicator_count}")
        print(f"Text before monster name (first 300): {lines_before[:300]}")
        
        # Check if there's any prose/paragraph text before the stat block
        paragraphs = [p.strip() for p in context.split('\n\n') if p.strip()]
        prose_before = [p for p in paragraphs if len(p) > 60 and not any(
            ind in p for ind in ['Armor Class', 'STR', 'DEX', 'CON'])]
        if prose_before:
            print(f"Potential prose/description paragraphs: {len(prose_before)}")
            for p in prose_before[:2]:
                print(f"  >> {p[:200]}")
        else:
            print("No obvious prose/description text found")
        return
    
    print(f"Monster '{monster_name}' not found in {slug}.txt")
    
    # Try searching other cached texts
    print("\nSearching ALL cached texts...")
    for f in sorted(CACHE_DIR.glob("*.txt")):
        text2 = f.read_text(errors='replace')
        if re.search(re.escape(monster_name), text2, re.IGNORECASE):
            print(f"  Found in: {f.name}")

# Test several cases
print("=" * 60)
print("1. Dancing Stone Familiar (TMFRV) — should have desc?")
check_monster("TMFRV", "Dancing Stone Familiar")

print("\n" + "=" * 60)
print("2. Hollow Man (TMFRV) — no desc")
check_monster("TMFRV", "Hollow Man")

print("\n" + "=" * 60)
print("3. Gazer (VGM) — Volo's, should have desc")
check_monster("VGM", "Gazer")

print("\n" + "=" * 60)
print("4. Ballista (DMG) — siege weapon, probably no flavor text")
check_monster("DMG", "Ballista")
