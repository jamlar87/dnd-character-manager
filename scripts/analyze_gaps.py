#!/usr/bin/env python3
"""Analyze monster description gaps — what sources are plate, what monsters do we have PDFs for?"""
import json, re
from pathlib import Path
from collections import Counter

HERE = Path(__file__).parent.parent
monsters = json.loads((HERE / "data" / "manual_data" / "monsters.json").read_text())
cache_dir = HERE / "data" / "manual_cache"

# Group missing-desc monsters by source text
no_desc = [m for m in monsters if not m.get('description') or len(m['description'].strip()) <= 20]

# Check which cached PDFs actually contain each monster name
cache_slugs = sorted([f.stem for f in cache_dir.glob("*.txt")])
print("Cached PDF texts available:", cache_slugs)

# For a sample of missing monsters, check which cached texts contain their name
# Focus on the worst sources
print("\n=== Checking sample monsters against ALL cached PDFs ===")
sample_sources = Counter(m.get('source','') for m in no_desc)
for src, count in sample_sources.most_common(20):
    examples = [m for m in no_desc if m.get('source','')==src][:3]
    for m in examples:
        name = m['name']
        slug = m.get('_source_manual','')
        hits = []
        for cs in cache_slugs:
            text = (cache_dir / f"{cs}.txt").read_text(errors='replace')
            if re.search(re.escape(name), text, re.IGNORECASE):
                hits.append(cs)
        if hits:
            print(f"  '{name}' (slug={slug}) → found in: {hits}")
        else:
            print(f"  '{name}' (slug={slug}) → NOT FOUND in any cached PDF")
    print()

# Also check: the "James Larsen" watermark — any PDFs contain that string?
print("=== Searching for 'James Larsen' watermark in cached texts ===")
for cs in cache_slugs:
    text = (cache_dir / f"{cs}.txt").read_text(errors='replace')
    if 'James Larsen' in text or '51905805' in text:
        idx = text.find('James Larsen')
        ctx = text[max(0,idx-50):idx+150]
        print(f"  {cs}: {ctx[:200]}")
print()

print(f"\nTotal no_desc monsters: {len(no_desc)}")
