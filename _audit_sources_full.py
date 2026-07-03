"""Full audit of all source and _source_manual field values across manual data files."""
import json, re, os
from collections import Counter, defaultdict

DATA_DIR = "data/manual_data"
FILES = [
    "magic_items.json", "npcs.json", "equipment.json", "spells.json",
    "feats.json", "backgrounds.json", "races.json", "subclasses.json",
    "monsters.json",
]

all_sources = Counter()
all_slugs = Counter()
file_counts = defaultdict(int)

for fn in FILES:
    path = os.path.join(DATA_DIR, fn)
    data = json.load(open(path))
    items = list(data.values()) if isinstance(data, dict) else data
    file_counts[fn] = len(items)
    
    for item in items:
        src = (item.get("source") or "").strip()
        slug = (item.get("_source_manual") or "").strip()
        
        if src and src != "Unknown":
            # Normalize whitespace
            src_norm = re.sub(r'\s+', ' ', src).strip()
            all_sources[src_norm] += 1
        if slug:
            all_slugs[slug] += 1

print("=== ALL SOURCE VALUES (sorted by frequency) ===")
for src, cnt in all_sources.most_common():
    print(f"  [{cnt:>4}] {src}")

print("\n\n=== ALL _source_manual SLUGS ===")
for slug, cnt in all_slugs.most_common():
    print(f"  [{cnt:>4}] {slug}")

print(f"\n\n=== FILE COUNTS ===")
for fn, cnt in sorted(file_counts.items()):
    print(f"  {fn}: {cnt}")
