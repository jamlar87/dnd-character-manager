#!/usr/bin/env python3
"""Rebuild the srd_monster_lookup table from existing SRD monsters in dm_npcs.

This script only updates the lookup table - it does NOT modify any SRD monster data.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path("/home/james/dnd-character-manager/data/characters.db")

def rebuild_srd_lookup():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    
    # Create table if not exists
    cur.execute("""
        CREATE TABLE IF NOT EXISTS srd_monster_lookup (
            name TEXT UNIQUE,
            npc_id INTEGER
        )
    """)
    
    # Clear existing entries
    cur.execute("DELETE FROM srd_monster_lookup")
    cleared_count = cur.rowcount
    print(f"Cleared {cleared_count} existing lookup entries")
    
    # Get all SRD monsters and their IDs
    srtd_monsters = list(cur.execute(
        "SELECT id, name FROM dm_npcs WHERE source = 'SRD' ORDER BY name"
    ))
    
    print(f"Found {len(srtd_monsters)} SRD monsters in dm_npcs")
    
    # Insert into lookup table
    for row in srtd_monsters:
        cur.execute(
            "INSERT INTO srd_monster_lookup (name, npc_id) VALUES (?, ?)",
            (row["name"], row["id"])
        )
        print(f"  {row['name']} -> npc_id {row['id']}")
    
    con.commit()
    
    # Verify
    total = cur.execute("SELECT COUNT(*) as cnt FROM srd_monster_lookup").fetchone()["cnt"]
    print(f"\nTotal entries in srd_monster_lookup: {total}")
    
    con.close()
    print("Done!")

if __name__ == "__main__":
    rebuild_srd_lookup()