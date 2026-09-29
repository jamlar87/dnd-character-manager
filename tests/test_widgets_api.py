"""Table widgets: counters and timers on an encounter or a campaign.

Atlas VTT keeps a counter and a timer next to the initiative order so the DM never leaves the
fight to update them. Same idea here, stored as plain data on the row that owns it — and the
ownership rule is the app's usual one, because a widget list is user data like any other.
"""

from __future__ import annotations

import json
import sqlite3

import pytest


def _insert(db_path, sql, values):
    db = sqlite3.connect(db_path)
    cur = db.execute(sql, values)
    row_id = cur.lastrowid
    db.commit()
    db.close()
    return row_id


def _widgets_in(db_path, table, row_id):
    db = sqlite3.connect(db_path)
    raw = db.execute(f"SELECT widgets FROM {table} WHERE id=?", (row_id,)).fetchone()[0]
    db.close()
    return json.loads(raw or "[]")


def test_an_encounter_starts_with_no_widgets(client, seeded_db, auth_headers):
    enc = _insert(seeded_db["db_path"],
                  "INSERT INTO dm_encounters (user_id, name) VALUES (?, ?)", (1, "Probe fight"))
    r = client.get(f"/api/dm/encounter/{enc}/widgets", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["widgets"] == []


def test_widgets_round_trip(client, seeded_db, auth_headers):
    enc = _insert(seeded_db["db_path"],
                  "INSERT INTO dm_encounters (user_id, name) VALUES (?, ?)", (1, "Round trip"))
    payload = {"widgets": [
        {"id": "w1", "name": "Fear", "icon": "😱", "type": "counter", "value": 3},
        {"id": "w2", "name": "Round timer", "icon": "⏱", "type": "timer", "seconds": 90},
    ]}
    r = client.post(f"/api/dm/encounter/{enc}/widgets", json=payload, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 2

    back = client.get(f"/api/dm/encounter/{enc}/widgets", headers=auth_headers).json()["widgets"]
    assert [w["name"] for w in back] == ["Fear", "Round timer"]
    assert back[0]["value"] == 3 and back[1]["seconds"] == 90
    assert back[1]["type"] == "timer"
    assert _widgets_in(seeded_db["db_path"], "dm_encounters", enc)[0]["name"] == "Fear"


def test_junk_is_cleaned_instead_of_stored(client, seeded_db, auth_headers):
    """A widget renders on every fight, so one bad entry would be re-sent forever."""
    enc = _insert(seeded_db["db_path"],
                  "INSERT INTO dm_encounters (user_id, name) VALUES (?, ?)", (1, "Junk"))
    payload = {"widgets": [
        {"name": "X" * 40, "type": "nonsense", "value": 5000, "seconds": -10},
        "not-an-object",
        {"name": "Timer", "type": "timer", "seconds": 10 ** 9},
    ]}
    r = client.post(f"/api/dm/encounter/{enc}/widgets", json=payload, headers=auth_headers)
    assert r.status_code == 200, r.text
    stored = _widgets_in(seeded_db["db_path"], "dm_encounters", enc)
    assert len(stored) == 2, "the string entry must be dropped, not coerced"
    assert len(stored[0]["name"]) == 24, "a runaway name is truncated"
    assert stored[0]["type"] == "counter", "an unknown type falls back to counter"
    assert stored[0]["value"] == 999, "counters clamp at 999"
    assert stored[1]["seconds"] == 24 * 3600, "timers clamp at a day"


def test_the_widget_list_is_capped(client, seeded_db, auth_headers):
    enc = _insert(seeded_db["db_path"],
                  "INSERT INTO dm_encounters (user_id, name) VALUES (?, ?)", (1, "Many"))
    payload = {"widgets": [{"name": f"W{i}"} for i in range(40)]}
    client.post(f"/api/dm/encounter/{enc}/widgets", json=payload, headers=auth_headers)
    assert len(_widgets_in(seeded_db["db_path"], "dm_encounters", enc)) == 24


def test_a_widget_list_is_not_writable_across_users(client, seeded_db, auth_headers):
    """The usual scope rule: 404 for a non-owner, and the row must be untouched."""
    victim = _insert(seeded_db["db_path"],
                     "INSERT INTO dm_encounters (user_id, name, widgets) VALUES (?, ?, ?)",
                     (2, "Not yours", json.dumps([{"name": "Theirs", "value": 7}])))
    r = client.post(f"/api/dm/encounter/{victim}/widgets",
                    json={"widgets": [{"name": "Mine"}]}, headers=auth_headers)
    assert r.status_code == 404, r.text
    assert _widgets_in(seeded_db["db_path"], "dm_encounters", victim)[0]["name"] == "Theirs"
    assert client.get(f"/api/dm/encounter/{victim}/widgets", headers=auth_headers).status_code == 404


def test_the_campaign_pair_works_the_same_way(client, seeded_db, auth_headers):
    camp = _insert(seeded_db["db_path"],
                   "INSERT INTO dm_campaigns (user_id, name) VALUES (?, ?)", (1, "Probe campaign"))
    assert client.get(f"/api/dm/campaign/{camp}/widgets", headers=auth_headers).json()["widgets"] == []
    r = client.post(f"/api/dm/campaign/{camp}/widgets",
                    json={"widgets": [{"name": "Doom clock", "type": "timer", "seconds": 60}]},
                    headers=auth_headers)
    assert r.status_code == 200, r.text
    back = client.get(f"/api/dm/campaign/{camp}/widgets", headers=auth_headers).json()["widgets"]
    assert back[0]["name"] == "Doom clock" and back[0]["seconds"] == 60


def test_campaign_widgets_are_scoped_too(client, seeded_db, auth_headers):
    victim = _insert(seeded_db["db_path"],
                     "INSERT INTO dm_campaigns (user_id, name) VALUES (?, ?)", (2, "Theirs"))
    assert client.post(f"/api/dm/campaign/{victim}/widgets",
                       json={"widgets": [{"name": "Mine"}]}, headers=auth_headers).status_code == 404


@pytest.mark.parametrize("table,path", [("dm_encounters", "encounter"), ("dm_campaigns", "campaign")])
def test_an_unknown_row_404s(client, seeded_db, auth_headers, table, path):
    r = client.get(f"/api/dm/{path}/999999/widgets", headers=auth_headers)
    assert r.status_code == 404
