#!/usr/bin/env python3
"""Re-apply cached descriptions to monsters.json after accidental overwrite."""
import json, re
from pathlib import Path

HERE = Path(__file__).parent.parent
monsters = json.loads((HERE / "data" / "manual_data" / "monsters.json").read_text())
cache = json.loads((HERE / "data" / "desc_llm_cache.json").read_text())

# Cache keys: "slug_name" → description text
applied = 0
skipped = 0

# Build a lookup: (slug, name) → index in monsters list
monster_map = {}
for i, m in enumerate(monsters):
    slug = m.get("_source_manual", "")
    name = m["name"]
    monster_map[(slug, name)] = i

for cache_key, desc in cache.items():
    if desc == "NO_DESC_FOUND" or not desc:
        continue
    
    # Parse slug_name from cache key
    # Keys look like "VGM_Gazer" or "EBT_Keeper of Hounds"
    # Some names contain underscores, so split on first underscore only
    parts = cache_key.split("_", 1)
    if len(parts) != 2:
        continue
    slug, name = parts
    
    # Find the monster
    key = (slug, name)
    if key not in monster_map:
        # Try case-insensitive match
        found = False
        for (s, n), idx in monster_map.items():
            if s == slug and n.lower() == name.lower():
                monsters[idx]["description"] = desc
                applied += 1
                found = True
                break
        if not found:
            skipped += 1
    else:
        idx = monster_map[key]
        monsters[idx]["description"] = desc
        applied += 1

print(f"Applied: {applied} descriptions from cache")
print(f"Skipped (not found in monsters.json): {skipped}")

# Save
(HERE / "data" / "manual_data" / "monsters.json").write_text(json.dumps(monsters, indent=2, ensure_ascii=False))

with_desc = sum(1 for m in monsters if m.get("description") and len(m["description"].strip()) > 20)
total = len(monsters)
print(f"Total: {total} monsters, {with_desc} with descriptions ({with_desc/total*100:.1f}%)")
