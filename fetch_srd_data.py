"""Fetch and cache D&D 5e 2014 SRD class level data from dnd5eapi.co."""
import json, sys, os
import httpx

API_BASE = "https://www.dnd5eapi.co/api/2014"
CLASSES = ["barbarian","bard","cleric","druid","fighter","monk",
           "paladin","ranger","rogue","sorcerer","warlock","wizard"]
CACHE_DIR = os.path.join(os.path.dirname(__file__), "data", "srd_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

def fetch_class_levels(class_name: str) -> list[dict]:
    """Fetch all 20 levels for a class."""
    url = f"{API_BASE}/classes/{class_name}/levels"
    resp = httpx.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()

def fetch_class_info(class_name: str) -> dict:
    """Fetch class metadata (HD, saves, proficiencies, subclasses)."""
    url = f"{API_BASE}/classes/{class_name}"
    resp = httpx.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return {
        "index": data["index"],
        "name": data["name"],
        "hit_die": data["hit_die"],
        "saving_throws": [s["name"] for s in data.get("saving_throws", [])],
        "proficiencies": [p["name"] for p in data.get("proficiencies", [])],
        "subclasses": [s["name"] for s in data.get("subclasses", [])],
        "spellcasting_level": data.get("spellcasting", {}).get("level"),
    }

def cache_all():
    all_levels = {}
    all_meta = {}
    for cls in CLASSES:
        try:
            levels = fetch_class_levels(cls)
            meta = fetch_class_info(cls)
            all_levels[cls] = levels
            all_meta[cls] = meta
            print(f"  {cls}: {len(levels)} levels, HD d{meta['hit_die']}, "
                  f"saves={meta['saving_throws']}, subs={len(meta['subclasses'])}")
        except Exception as e:
            print(f"  {cls}: FAILED — {e}")

    with open(os.path.join(CACHE_DIR, "class_levels.json"), "w") as f:
        json.dump(all_levels, f, indent=2)
    with open(os.path.join(CACHE_DIR, "class_meta.json"), "w") as f:
        json.dump(all_meta, f, indent=2)
    print(f"\nCached {sum(len(v) for v in all_levels.values())} levels for {len(all_levels)} classes")
    print(f"Saved to {CACHE_DIR}/class_levels.json and class_meta.json")

if __name__ == "__main__":
    cache_all()
