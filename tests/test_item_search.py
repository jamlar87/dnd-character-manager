"""Tests for the DM Tools item search + team-pool flow (2026-08-12).

Search ranking contract: exact name > name prefix > name substring > field
match (type/rarity/source/description). The picker UI (searchItemPicker +
showItemDetail + addItemFromDetail) relies on /api/items/search accepting
q/type/rarity and /api/items/describe returning the full payload.
"""


def test_item_search_ranks_exact_and_prefix_first(client, auth_headers):
    r = client.get("/api/items/search?q=flame+tongue", headers=auth_headers)
    assert r.status_code == 200
    d = r.json()
    names = [x["name"] for x in d["results"]]
    assert names and names[0] == "Flame Tongue"
    # second result matched on full description text (may be truncated in brief)
    assert len(d["results"]) >= 2


def test_item_search_prefix_ordering(client, auth_headers):
    r = client.get("/api/items/search?q=long", headers=auth_headers)
    d = r.json()
    names = [x["name"] for x in d["results"]]
    assert names[0].lower().startswith("long")
    # first result must be a name-prefix match, not a field match
    assert all("long" in n.lower() for n in names[:5])


def test_item_search_filters_by_type_and_rarity(client, auth_headers):
    r = client.get("/api/items/search?type=potion", headers=auth_headers)
    d = r.json()
    assert d["results"]
    assert all("Potion" in x["type"] for x in d["results"])

    r = client.get("/api/items/search?rarity=legendary", headers=auth_headers)
    d = r.json()
    assert d["results"]
    assert all("legendary" in x["rarity"].lower() for x in d["results"])

    # combined
    r = client.get("/api/items/search?type=ring&rarity=rare", headers=auth_headers)
    d = r.json()
    assert all("Ring" in x["type"] and "rare" in x["rarity"].lower() for x in d["results"])


def test_item_describe_returns_full_payload(client, auth_headers):
    r = client.get("/api/items/describe?name=flame tongue", headers=auth_headers)
    assert r.status_code == 200
    it = r.json()
    assert it["name"] == "Flame Tongue"
    assert it["type"] == "Magic Weapon"
    assert it["rarity"] == "Rare"
    assert it["description"]
    assert "source" in it


def test_item_search_empty_returns_all_with_metadata(client, auth_headers):
    r = client.get("/api/items/search", headers=auth_headers)
    d = r.json()
    assert d["total"] >= 600
    assert d["results"]
    first = d["results"][0]
    # richer brief payload fields used by the picker rows
    for field in ("name", "type", "rarity", "source", "description"):
        assert field in first
