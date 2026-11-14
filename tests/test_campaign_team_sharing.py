"""Stage 0 regressions for campaign team items (loot pool) and share-to-team.

Each test pins one defect from docs/plans/2026-11-14-full-refactor.md §Stage 0:

* 0.1 IDOR — a non-owner could move items out of another user's character.
* 0.2 Inventory inflation — `qty: -5` created items.
* 0.3 Money destroyed — `int(50 * 0.01) == 0` silently dropped sub-gold rewards.
* 0.4 Unbounded / unescaped team item name.
* 0.8 Corrupt roster JSON swallowed, demoting every member to "not in campaign".
"""

import json
import sqlite3

import pytest


def _conn(seeded_db):
    db = sqlite3.connect(seeded_db["db_path"])
    db.row_factory = sqlite3.Row
    return db


def _make_character(db, user_id, name, inventory="[]", gp=0, cp=0):
    db.execute(
        "INSERT INTO characters (user_id, name, race, class_name, inventory, gp, cp) "
        "VALUES (?,?,?,?,?,?,?)",
        (user_id, name, "Human", "Fighter", inventory, gp, cp),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _make_campaign(db, dm_user_id, chars, name="Test Campaign"):
    db.execute(
        "INSERT INTO dm_campaigns (user_id, name, characters) VALUES (?,?,?)",
        (dm_user_id, name, json.dumps(chars) if not isinstance(chars, str) else chars),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _add_pool_item(db, camp_id, name, qty=1, gp_value=0):
    db.execute(
        "INSERT INTO campaign_team_items (campaign_id, name, qty, gp_value) VALUES (?,?,?,?)",
        (camp_id, name, qty, gp_value),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


# ── 0.1 IDOR ─────────────────────────────────────────────────────────────────────────

def test_cannot_share_another_users_inventory_to_team(client, seeded_db, auth_headers):
    """User 1 (the caller) must not be able to empty user 2's backpack by character id."""
    db = _conn(seeded_db)
    victim_id = _make_character(db, 2, "Victim", json.dumps([{"name": "Longsword", "qty": 1}]))
    camp_id = _make_campaign(db, 1, [{"id": victim_id, "name": "Victim"}])
    db.commit()
    db.close()

    resp = client.post(
        f"/api/character/{victim_id}/share-to-team",
        json={"name": "Longsword", "qty": 1},
        headers=auth_headers,
    )
    assert resp.status_code == 403, resp.text

    db = _conn(seeded_db)
    inv = json.loads(db.execute(
        "SELECT inventory FROM characters WHERE id=?", (victim_id,)).fetchone()[0])
    pool = db.execute(
        "SELECT COUNT(*) FROM campaign_team_items WHERE campaign_id=?", (camp_id,)).fetchone()[0]
    db.close()
    assert inv == [{"name": "Longsword", "qty": 1}], "victim inventory was modified"
    assert pool == 0, "items were moved into a campaign pool by a non-owner"


# ── 0.2 Inventory inflation ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_qty", [-5, 0, "abc", None])
def test_share_to_team_rejects_non_positive_or_nonnumeric_qty(client, seeded_db, auth_headers, bad_qty):
    db = _conn(seeded_db)
    char_id = _make_character(db, 1, "Owner", json.dumps([{"name": "Rope", "qty": 2}]))
    camp_id = _make_campaign(db, 1, [{"id": char_id, "name": "Owner"}])
    db.commit()
    db.close()

    payload = {"name": "Rope"}
    if bad_qty is not None:
        payload["qty"] = bad_qty
    resp = client.post(f"/api/character/{char_id}/share-to-team",
                       json=payload, headers=auth_headers)
    if bad_qty is None:
        # Missing qty is the documented default of 1, not an error.
        assert resp.status_code == 200, resp.text
        return
    assert resp.status_code == 400, resp.text

    db = _conn(seeded_db)
    inv = json.loads(db.execute(
        "SELECT inventory FROM characters WHERE id=?", (char_id,)).fetchone()[0])
    pool = db.execute(
        "SELECT COALESCE(SUM(qty),0) FROM campaign_team_items WHERE campaign_id=?", (camp_id,)
    ).fetchone()[0]
    db.close()
    assert inv == [{"name": "Rope", "qty": 2}], "qty<=0 changed the inventory"
    assert pool == 0, "qty<=0 created pool items"


def test_share_to_team_cannot_share_more_than_held(client, seeded_db, auth_headers):
    db = _conn(seeded_db)
    char_id = _make_character(db, 1, "Owner", json.dumps([{"name": "Torch", "qty": 2}]))
    campaign_id = _make_campaign(db, 1, [{"id": char_id, "name": "Owner"}])
    db.commit()
    db.close()

    resp = client.post(f"/api/character/{char_id}/share-to-team",
                       json={"name": "Torch", "qty": 50}, headers=auth_headers)
    assert resp.status_code == 200, resp.text

    db = _conn(seeded_db)
    inv = json.loads(db.execute(
        "SELECT inventory FROM characters WHERE id=?", (char_id,)).fetchone()[0])
    shared = db.execute(
        "SELECT qty FROM campaign_team_items WHERE campaign_id=?", (campaign_id,)).fetchone()[0]
    db.close()
    assert [i for i in inv if i.get("name") == "Torch"] == [], "stack should be fully moved"
    assert shared == 2, "shared more than the character held"


# ── 0.3 Currency precision ──────────────────────────────────────────────────────────

def test_claiming_cp_reward_keeps_copper(client, seeded_db, auth_headers):
    """A '50 cp' pool line used to add 0 gp and vanish."""
    db = _conn(seeded_db)
    char_id = _make_character(db, 1, "Claimer")
    camp_id = _make_campaign(db, 1, [{"id": char_id, "name": "Claimer"}])
    item_id = _add_pool_item(db, camp_id, "50 cp", qty=1)
    db.commit()
    db.close()

    resp = client.post(f"/api/campaign/{camp_id}/team-items/{item_id}/claim",
                       json={"character_id": char_id}, headers=auth_headers)
    assert resp.status_code == 200, resp.text

    db = _conn(seeded_db)
    row = db.execute("SELECT gp, cp FROM characters WHERE id=?", (char_id,)).fetchone()
    db.close()
    assert (row["gp"], row["cp"]) == (0, 50), "copper reward was truncated away"


def test_claiming_sp_reward_carries_into_gold(client, seeded_db, auth_headers):
    db = _conn(seeded_db)
    char_id = _make_character(db, 1, "Claimer", gp=0, cp=75)
    camp_id = _make_campaign(db, 1, [{"id": char_id, "name": "Claimer"}])
    item_id = _add_pool_item(db, camp_id, "30 sp", qty=1)
    db.commit()
    db.close()

    resp = client.post(f"/api/campaign/{camp_id}/team-items/{item_id}/claim",
                       json={"character_id": char_id}, headers=auth_headers)
    assert resp.status_code == 200, resp.text

    db = _conn(seeded_db)
    row = db.execute("SELECT gp, cp FROM characters WHERE id=?", (char_id,)).fetchone()
    db.close()
    # 75 cp + 300 cp = 3 gp 75 cp
    assert (row["gp"], row["cp"]) == (3, 75)


# ── 0.4 Item name length at write ───────────────────────────────────────────────────

def test_team_item_name_length_is_capped(client, seeded_db, auth_headers):
    db = _conn(seeded_db)
    camp_id = _make_campaign(db, 1, [])
    db.commit()
    db.close()

    resp = client.post(f"/api/campaign/{camp_id}/team-items",
                       json={"name": "A" * 5000, "qty": 1}, headers=auth_headers)
    assert resp.status_code == 400, resp.text

    db = _conn(seeded_db)
    assert db.execute("SELECT COUNT(*) FROM campaign_team_items").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize("bad", [{"qty": "abc"}, {"qty": -3}, {"gp_value": "lots"}])
def test_add_team_item_rejects_bad_numbers_with_400_not_500(client, seeded_db, auth_headers, bad):
    db = _conn(seeded_db)
    camp_id = _make_campaign(db, 1, [])
    db.commit()
    db.close()

    resp = client.post(f"/api/campaign/{camp_id}/team-items",
                       json={"name": "Potion", **bad}, headers=auth_headers)
    assert resp.status_code == 400, resp.text


# ── 0.8 Corrupt roster must fail loudly ─────────────────────────────────────────────

def test_corrupt_roster_json_returns_500_instead_of_silent_403(client, seeded_db, auth_headers):
    """Truncated roster JSON used to be swallowed, so members saw phantom 403s."""
    db = _conn(seeded_db)
    camp_id = _make_campaign(db, 1, '[{"id": 1,')
    db.commit()
    db.close()

    resp = client.get(f"/api/campaign/{camp_id}/team-items", headers=auth_headers)
    assert resp.status_code == 500, resp.text
    assert "corrupt" in resp.text.lower()
