"""Regression tests for the merged ITEM_INDEX integrity (2026-08-12).

The DM Tools item lookup table renders ITEM_INDEX (SRD magic items +
equipment + 735 manual magic items, 1545 total post-merge). Every entry
must be: name-searchable, describable, and carry a normalized rarity so the
picker's rarity filter works. Manual items previously bypassed rarity
normalization in the ITEM_INDEX rebuild path (fabled/unique/scaling strings
leaked through).
"""

from services.data_loader import _normalize_item_rarity


def test_normalize_item_rarity_maps_exotic_labels():
    cases = {
        "Fabled": "legendary",
        "fabled": "legendary",
        "unique": "artifact",
        "Uncommon": "uncommon",
        "Rare": "rare",
        "very rare": "very rare",
        "legendary": "legendary",
        "artifact": "artifact",
        "common": "common",
        "varies": "varies",
        "unknown": "unknown",
        "": "unknown",
        "none (non-magical)": "common",
        "faint conjuration": "common",
        "moderate": "uncommon",
        "minor magical property": "common",
    }
    for raw, expected in cases.items():
        assert _normalize_item_rarity(raw) == expected, f"{raw!r} -> {_normalize_item_rarity(raw)}"


def test_normalize_item_rarity_scaling_descriptors():
    assert _normalize_item_rarity("uncommon (+1), rare (+2), or very rare (+3)") == "varies"
    assert _normalize_item_rarity("rare (+1), very rare (+2)") == "varies"
    assert _normalize_item_rarity("varies with version") == "varies"


def test_merged_index_all_rarities_standard(client):
    """Every non-empty rarity in the merged index is a canonical 5e value."""
    import main
    import routes.characters.all  # trigger manual merge
    from main import ITEM_INDEX
    std = {"common", "uncommon", "rare", "very rare", "legendary", "artifact", "varies", "unknown"}
    weird = [(v["name"], v.get("rarity")) for v in ITEM_INDEX.values()
             if v.get("rarity") and str(v.get("rarity")).lower() not in std]
    assert not weird, f"non-standard rarities: {weird[:10]}"


def test_merged_index_all_items_describable(client, auth_headers):
    """Every item in the merged index resolves through the describe endpoint."""
    import main
    import routes.characters.all
    from main import ITEM_INDEX
    from urllib.parse import quote
    fails = []
    for v in ITEM_INDEX.values():
        r = client.get("/api/items/describe?name=" + quote(v["name"]), headers=auth_headers)
        if r.status_code != 200 or not r.json().get("name"):
            fails.append(v["name"])
    assert not fails, f"undescribable items: {fails[:10]}"


def test_merged_index_exact_name_search_returns_item_first(client, auth_headers):
    """Every item is findable by its exact name as the top search result."""
    import main
    import routes.characters.all
    from main import ITEM_INDEX
    from urllib.parse import quote
    misses = []
    for v in ITEM_INDEX.values():
        r = client.get("/api/items/search?q=" + quote(v["name"]), headers=auth_headers)
        d = r.json()
        if not d["results"] or d["results"][0]["name"].lower() != v["name"].lower():
            misses.append(v["name"])
    assert not misses, f"unsearchable items: {misses[:10]}"
