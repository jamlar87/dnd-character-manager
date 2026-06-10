#!/usr/bin/env python3
"""Fix GGR magic items: remove OCR duplicates, split Guild Signet into 10 individual signets."""
import json
from pathlib import Path

MANUAL = Path("/home/james/dnd-character-manager/data/manual_data")

with open(MANUAL / "magic_items.json") as f:
    items = json.load(f)

# GGR guild names
guilds = [
    "Azorius", "Boros", "Dimir", "Golgari", "Gruul",
    "Izzet", "Orzhov", "Rakdos", "Selesnya", "Simic"
]

# Track what we remove and add
removed = []
added = []

# Filter out problematic GGR items
new_items = []
for item in items:
    name = item.get("name", "")
    src = item.get("_source_manual", "")
    
    if src != "GGR":
        new_items.append(item)
        continue
    
    name_lower = name.lower()
    
    # Remove OCR dups
    if name_lower == "sunforger":
        removed.append(f"dup: {name} (keeping 'Sun Forger')")
        continue
    if name_lower == "sword of the pa runs":
        removed.append(f"dup: {name} (keeping 'Sword of the Paruns')")
        continue
    
    # Split generic "Guild Signet" into 10 individual signets
    if name_lower == "guild signet":
        removed.append(f"split: {name} → 10 individual signets")
        for guild in guilds:
            new_item = dict(item)  # shallow copy
            new_item["name"] = f"{guild} Guild Signet"
            new_item["description"] = f"A {guild} guild signet, representing membership in the {guild} guild of Ravnica. A guild signet is a magic ring that allows you to cast spells associated with your guild."
            added.append(new_item["name"])
            new_items.append(new_item)
        continue
    
    # Split generic "Guild Keyrune" — we already have 9 individual keyrunes
    # but keep this if there's no overlap
    if name_lower == "guild keyrune":
        # Check if we already have individual keyrunes
        existing_keyrunes = {i["name"].lower() for i in items if "keyrune" in i.get("name","").lower() and i.get("_source_manual") == "GGR"}
        if len(existing_keyrunes) >= 9:
            removed.append(f"dup: {name} (individual keyrunes already exist)")
            continue
        else:
            new_items.append(item)
        continue
    
    new_items.append(item)

print(f"Removed: {len(removed)}")
for r in removed:
    print(f"  - {r}")
print(f"Added: {len(added)}")
for a in added:
    print(f"  + {a}")
print(f"\nOld count: {len(items)}")
print(f"New count: {len(new_items)}")

# Write back
with open(MANUAL / "magic_items.json", "w") as f:
    json.dump(new_items, f, indent=2)

print("\nDone — magic_items.json updated")
