#!/usr/bin/env python3
"""Migrate NPC data to unified SRD library.

Extracts unique monsters from encounter data and populates dm_npcs table
with proper SRD monster entries.
"""
import json
import sqlite3
from pathlib import Path

DB_PATH = Path("/home/james/dnd-character-manager/data/characters.db")
SRD_MONSTERS_PATH = Path("/home/james/dnd-character-manager/data/srd_cache/monsters.json")
MANUAL_MONSTERS_PATH = Path("/home/james/dnd-character-manager/data/manual_data/monsters.json")

# SRD monster index by name
_srd_index: dict[str, dict] = {}


def normalize_name(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9 ]", "", (name or "").lower()).strip()


def load_srd_index():
    """Load SRD and manual monster indexes."""
    global _srd_index
    
    for path in [SRD_MONSTERS_PATH, MANUAL_MONSTERS_PATH]:
        if path.exists():
            with open(path) as f:
                monsters = json.load(f)
                for m in monsters:
                    name = m.get("name", "")
                    if name:
                        key = normalize_name(name)
                        if key not in _srd_index:
                            _srd_index[key] = m


def extract_unique_monsters_from_encounters(con: sqlite3.Connection) -> set[str]:
    """Extract unique monster names from encounter creature_data fields.
    
    Skips PC NPCs (is_enemy=0 or class_name present) - only returns true monsters.
    """
    names = set()
    
    for row in con.execute("SELECT npc_id, creature_data FROM dm_encounter_npcs"):
        npc_id, creature_data = row
        if not creature_data:
            continue
        try:
            data = json.loads(creature_data)
            if data and data.get("name"):
                # Skip PC NPCs: those with class_name set or is_enemy=0
                if data.get("class_name") or data.get("is_enemy") == 0:
                    continue
                names.add(data["name"])
        except json.JSONDecodeError:
            pass
    
    return names


def _parse_speed(speed_val) -> int:
    """Extract numeric speed from various formats: 30, '30 ft.', {'walk': 30}, {'walk': '40 ft.'}."""
    import re
    
    if isinstance(speed_val, int):
        return speed_val
    if isinstance(speed_val, str):
        m = re.search(r"(\d+)", speed_val)
        return int(m.group(1)) if m else 30
    if isinstance(speed_val, dict):
        walk_val = speed_val.get("walk", 30)
        if isinstance(walk_val, int):
            return walk_val
        if isinstance(walk_val, str):
            m = re.search(r"(\d+)", walk_val)
            return int(m.group(1)) if m else 30
    return 30


def create_or_update_monster(monster_name: str, monster_data: dict) -> dict:
    """Create monster record for dm_npcs table."""
    ac_raw = monster_data.get("armor_class")
    if isinstance(ac_raw, int):
        ac_value = ac_raw
    elif isinstance(ac_raw, list) and len(ac_raw) > 0:
        ac_value = int(ac_raw[0].get("value", 10))
    else:
        ac_value = 10
    
    speed_data = monster_data.get("speed", {})
    raw_speed = speed_data if isinstance(speed_data, dict) else speed_data
    
    # Extract abilities
    abilities = monster_data.get("abilities", {})
    
    return {
        "user_id": 0,  # 0 = shared SRD library
        "name": monster_name,
        "race": monster_data.get("type", "Medium Humanoid"),
        "class_name": "",  # Monsters don't have class
        "subclass": "",
        "level": 0,
        "is_enemy": 1,
        "is_party_npc": 0,
        "strength": abilities.get("strength", 10),
        "dexterity": abilities.get("dexterity", 10),
        "constitution": abilities.get("constitution", 10),
        "intelligence": abilities.get("intelligence", 10),
        "wisdom": abilities.get("wisdom", 10),
        "charisma": abilities.get("charisma", 10),
        "hp_max": monster_data.get("hp", 7),
        "hp_current": monster_data.get("hp", 7),
        "temp_hp": 0,
        "ac": int(ac_value),
        "speed": _parse_speed(speed_data),
        "proficiency_bonus": monster_data.get("proficiency_bonus", 2),
        "hit_dice": monster_data.get("hit_dice", "1d8"),
        "hit_dice_used": 0,
        "skills": "[]",
        "features": "[]",
        "inventory": "[]",
        "notes": monster_data.get("desc", "")[:500] if monster_data.get("desc") else "",
        "portrait_url": monster_data.get("image", ""),
        "alignment": monster_data.get("alignment", "Neutral"),
        "role": "Monster",
        "faction": "",
        "xp_reward": monster_data.get("xp", 0),
        "source": "SRD",
        "source_book": "SRD",
        "source_page": "",
    }


def run_migration():
    """Run the migration."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    
    # Load SRD index
    load_srd_index()
    print(f"Loaded {len(_srd_index)} SRD/manual monsters")
    
    # Get unique monster names from encounters
    unique_names = extract_unique_monsters_from_encounters(con)
    print(f"Found {len(unique_names)} unique monsters in encounters")
    
    # Clear existing SRD monsters (keep only sentinel and user NPCs)
    cur.execute("UPDATE dm_npcs SET source = 'cleared' WHERE source = 'SRD' AND id > 0")
    cleared_count = cur.rowcount
    print(f"Cleared {cleared_count} old SRD entries")
    
    # Insert SRD monsters
    inserted = 0
    for name in sorted(unique_names):
        # Find in SRD index
        key = normalize_name(name)
        monster_data = _srd_index.get(key) or _srd_index.get(name.lower())
        
        if not monster_data:
            print(f"  WARNING: '{name}' not found in SRD index")
            continue
        
        record = create_or_update_monster(name, monster_data)
        
        # Check if already exists
        existing = cur.execute("SELECT id FROM dm_npcs WHERE name = ?", (name,)).fetchone()
        
        if existing:
            # Update with SRD data
            cols = ", ".join(f"{k}=?" for k in record.keys())
            vals = list(record.values()) + [existing["id"]]
            cur.execute(f"UPDATE dm_npcs SET {cols} WHERE id = ?", vals)
        else:
            # Insert new
            cols = ", ".join(record.keys())
            placeholders = ", ".join("?" * len(record))
            cur.execute(f"INSERT INTO dm_npcs ({cols}) VALUES ({placeholders})", list(record.values()))
        
        inserted += 1
    
    print(f"Inserted/updated {inserted} SRD monsters")
    
    # Create mapping table for encounter -> monster_id lookup
    cur.execute("CREATE TABLE IF NOT EXISTS srd_monster_lookup (name TEXT UNIQUE, npc_id INTEGER)")
    cur.execute("DELETE FROM srd_monster_lookup")
    
    for row in cur.execute("SELECT id, name FROM dm_npcs WHERE source = 'SRD'"):
        name = row["name"]
        npc_id = row["id"]
        cur.execute("INSERT INTO srd_monster_lookup (name, npc_id) VALUES (?, ?)", (name, npc_id))
    
    con.commit()
    con.close()
    print("Migration complete!")


if __name__ == "__main__":
    run_migration()