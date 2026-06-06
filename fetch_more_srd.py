"""Fetch SRD magic items + features concurrently."""
import json, os, asyncio
import httpx

API = "https://www.dnd5eapi.co/api/2014"
HERE = os.path.dirname(__file__)
CACHE = os.path.join(HERE, "data", "srd_cache")
os.makedirs(CACHE, exist_ok=True)

async def fetch_index(client, endpoint: str) -> list[dict]:
    """Get the list of all items from an endpoint."""
    url = f"{API}/{endpoint}"
    resp = await client.get(url, timeout=30)
    data = resp.json()
    return data["results"]

async def fetch_detail(client, endpoint: str, index: str, sem: asyncio.Semaphore):
    """Fetch one item's full detail."""
    async with sem:
        try:
            url = f"{API}/{endpoint}/{index}"
            resp = await client.get(url, timeout=15)
            return resp.json()
        except Exception as e:
            return {"index": index, "error": str(e)}

async def fetch_all(endpoint: str, label: str, max_concurrent: int = 20):
    async with httpx.AsyncClient() as client:
        print(f"Fetching {label} index...")
        items = await fetch_index(client, endpoint)
        print(f"  {len(items)} items found, fetching details...")
        
        sem = asyncio.Semaphore(max_concurrent)
        batch_size = 50
        results = []
        for i in range(0, len(items), batch_size):
            batch = items[i:i+batch_size]
            tasks = [fetch_detail(client, endpoint, item["index"], sem) for item in batch]
            batch_results = await asyncio.gather(*tasks)
            results.extend(batch_results)
            print(f"  {label}: {min(i+batch_size, len(items))}/{len(items)}")
        
        # Save
        path = os.path.join(CACHE, f"{endpoint}.json")
        with open(path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"  Saved {len(results)} {label} to {path}")
        return results

async def main():
    await fetch_all("magic-items", "magic items", max_concurrent=30)
    await fetch_all("features", "features", max_concurrent=30)
    print("Done.")

if __name__ == "__main__":
    asyncio.run(main())
