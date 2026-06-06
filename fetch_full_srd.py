"""Fetch ALL 2014 SRD data from dnd5eapi.co and cache locally.

Fetches every endpoint the API offers, so the character manager and
campaign expert both have a complete local reference library.

Endpoints fetched:
  races, subraces, traits, feats, backgrounds
  skills, proficiencies, ability-scores, languages, alignments
  equipment, equipment-categories, magic-schools, weapon-properties
  damage-types, conditions, rules, rule-sections
  spells, subclasses, classes, features, magic-items
  monsters  (huge — ~700+ entries, tries to be gentle)
"""
import json, os, asyncio, sys
from pathlib import Path
import httpx

API = "https://www.dnd5eapi.co/api/2014"
HERE = Path(__file__).parent
CHAR_CACHE = HERE / "data" / "srd_cache"
CE_CACHE = HERE.parent / "dnd-campaign-expert" / "engine" / "data" / "srd_cache"

os.makedirs(CHAR_CACHE, exist_ok=True)
os.makedirs(CE_CACHE, exist_ok=True)

# ── Priority endpoints (directly used by character manager) ──
PRIORITY = [
    ("spells",       "Spells"),
    ("races",        "Races"),
    ("subraces",     "Subraces"),
    ("traits",       "Traits"),
    ("feats",        "Feats"),
    ("backgrounds",  "Backgrounds"),
    ("skills",       "Skills"),
    ("proficiencies","Proficiencies"),
    ("ability-scores","Ability Scores"),
    ("languages",    "Languages"),
    ("alignments",   "Alignments"),
]

# ── Secondary endpoints (useful but less critical) ──
SECONDARY = [
    ("equipment","Equipment"),
    ("equipment-categories","Equipment Categories"),
    ("magic-schools","Magic Schools"),
    ("weapon-properties","Weapon Properties"),
    ("damage-types","Damage Types"),
    ("conditions","Conditions"),
    ("rules","Rules"),
    ("rule-sections","Rule Sections"),
    ("monsters","Monsters"),
    ("subclasses","Subclasses"),
    ("classes","Classes"),
    ("features","Features"),
    ("magic-items","Magic Items"),
]

async def fetch_index(client, endpoint: str) -> list[dict]:
    url = f"{API}/{endpoint}"
    resp = await client.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", [])

async def fetch_detail(client, endpoint: str, index: str, sem: asyncio.Semaphore):
    async with sem:
        for attempt in range(3):
            try:
                url = f"{API}/{endpoint}/{index}"
                resp = await client.get(url, timeout=15)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if attempt == 2:
                    return {"index": index, "error": str(e)}
                await asyncio.sleep(1)

async def fetch_endpoint(client, endpoint: str, label: str, max_concurrent: int) -> list[dict]:
    print(f"[{label}] Fetching index...")
    items = await fetch_index(client, endpoint)
    if not items:
        print(f"[{label}] 0 items — skipping")
        return []
    print(f"[{label}] {len(items)} items, fetching details...")

    sem = asyncio.Semaphore(max_concurrent)
    batch_size = 30 if endpoint == "monsters" else 50
    results = []
    for i in range(0, len(items), batch_size):
        batch = items[i:i+batch_size]
        tasks = [fetch_detail(client, endpoint, item["index"], sem) for item in batch]
        batch_results = await asyncio.gather(*tasks)
        results.extend(batch_results)
        done = min(i + batch_size, len(items))
        print(f"[{label}] {done}/{len(items)}")

    # Filter out error entries
    good = [r for r in results if "error" not in r]
    errors = [r for r in results if "error" in r]
    if errors:
        print(f"[{label}] {len(errors)} fetch errors (e.g. {errors[0].get('index')})")

    # Save to both cache dirs
    for cache_dir in (CHAR_CACHE, CE_CACHE):
        path = cache_dir / f"{endpoint}.json"
        with open(path, "w") as f:
            json.dump(good, f, indent=2)
        print(f"[{label}] Saved {len(good)} items → {path}")

    return good

async def main():
    print("=" * 60)
    print("D&D 5e 2014 SRD — Full Data Fetch")
    print(f"Source: {API}")
    print(f"Char cache: {CHAR_CACHE}")
    print(f"CE cache:  {CE_CACHE}")
    print("=" * 60)
    print()

    async with httpx.AsyncClient() as client:
        # Phase 1: Priority endpoints
        print("── Phase 1: Priority ──")
        for endpoint, label in PRIORITY:
            try:
                await fetch_endpoint(client, endpoint, label, max_concurrent=25)
            except Exception as e:
                print(f"[{label}] CRASHED: {e}")

        # Phase 2: Secondary endpoints
        print("\n── Phase 2: Secondary ──")
        for endpoint, label in SECONDARY:
            try:
                mc = 10 if endpoint == "monsters" else 25
                await fetch_endpoint(client, endpoint, label, max_concurrent=mc)
            except Exception as e:
                print(f"[{label}] CRASHED: {e}")

    print("\n" + "=" * 60)
    print("Complete!")
    # Show cache summary
    print(f"\nCache contents ({CHAR_CACHE}):")
    total_size = 0
    for p in sorted(CHAR_CACHE.iterdir()):
        sz = p.stat().st_size
        total_size += sz
        try:
            with open(p) as f:
                n = len(json.load(f))
        except:
            n = "?"
        print(f"  {p.name:30s} {n:>5} items  {sz//1024:>5} KB")
    print(f"\n  Total: {total_size // 1024} KB")

if __name__ == "__main__":
    asyncio.run(main())
