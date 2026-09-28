"""Browsable name lists read in alphabetical order — the order a reader means by that.

`.lower()` is codepoint order, so it filed every accented name after ALL the unaccented
ones ("Dáin Ironfoot" past "Dancing Flame", "Frár" past every plain F) and put a
comma-inverted personal name ("Zombie, Lord") after "Zombie Mastiff": the Monsters tab
looked unsorted while a `.lower()` comparison called it sorted. The reference NPC list was
worse — it shipped in extraction order ('Ayo Jabe', 'Dermot Wurder', 'Galsariad Ardyth'…).

The key is services/text.alpha_key and it is applied where each list is ORDERED:
routes/characters/helpers.py::_load_monster_cache (monsters tab, pickers, encounter
builder), routes/dm.py (reference traps, reference NPCs, the DM's own NPCs).
"""

import json
import re
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def asset_payload() -> dict:
    raw = (ROOT / "static" / "dm-library.js").read_text()
    start = raw.index("window.DM_LIBRARY = ") + len("window.DM_LIBRARY = ")
    end = raw.index(";\nwindow.DM_MONSTERS", start)
    return json.loads(raw[start:end])


def dm_npc_rows(html: str) -> list[tuple[str, bool]]:
    """(data-name, is_enemy) of the server-rendered NPC rows — the reference rows are
    rendered client-side from the asset, so they never appear in this HTML."""
    return [(name, "enemy" in cls)
            for cls, name in re.findall(r'<div class="npc-row([^"]*)" data-name="([^"]*)"', html)]


class TestAlphaKey:
    def test_accents_fold_into_their_letter_group(self):
        from services.text import alpha_key
        # Dái sorts before Dan, not after every plain-letter name
        assert alpha_key("Dáin Ironfoot") < alpha_key("Dancing Flame")
        assert alpha_key("Frár the Beardless") < alpha_key("Frost Giant")
        assert alpha_key("Külmking") < alpha_key("Kuo-Toa")
        assert alpha_key("Jędza Nansa") < alpha_key("Jellyfish")
        assert alpha_key("Éowyn") < alpha_key("Ezra")

    def test_an_inverted_name_files_with_its_base_form(self):
        from services.text import alpha_key
        assert alpha_key("Zombie, Lord") < alpha_key("Zombie Mastiff")
        assert alpha_key("Zombie, Lord") > alpha_key("Zombie")

    def test_case_and_spacing_do_not_decide_the_order(self):
        from services.text import alpha_key
        assert alpha_key("  acid   ant ") == alpha_key("Acid Ant")
        assert alpha_key("alice") < alpha_key("Bob")

    def test_a_nameless_row_sorts_last(self):
        from services.text import alpha_key
        assert alpha_key("") > alpha_key("Zzz")
        assert alpha_key(None) > alpha_key("Zzz")


class TestTheMonsterListsUseIt:
    def test_the_cache_order_is_alpha_key_sorted(self):
        from routes.characters import _load_monster_cache
        from services.text import alpha_key
        names = [m.get("name") or "" for m in _load_monster_cache()]
        assert len(names) > 100, "monster cache looks empty in this environment"
        assert names == sorted(names, key=alpha_key)

    def test_the_tab_asset_order_matches(self, client, seeded_db, auth_headers):
        client.get("/dm-tools", headers=auth_headers)          # regenerates the asset
        from services.text import alpha_key
        names = [m["n"] for m in asset_payload()["monsters"]]
        assert names == sorted(names, key=alpha_key), "the monsters tab is not in reader order"

    def test_an_accented_name_sits_inside_its_letter_group(self, client, seeded_db, auth_headers):
        client.get("/dm-tools", headers=auth_headers)
        from services.text import alpha_key
        names = [m["n"] for m in asset_payload()["monsters"]]
        accented = [n for n in names if any(ord(c) > 127 for c in n)]
        if not accented:
            pytest.skip("no accented monster names in this data set")
        for name in accented[:5]:
            letter = alpha_key(name)[:1]
            block = [i for i, n in enumerate(names) if alpha_key(n)[:1] == letter]
            i = names.index(name)
            # Under the old codepoint sort every accented name sat at the very end of the
            # list, well outside its own letter block.
            assert block[0] <= i <= block[-1], \
                f"{name!r} is outside the {letter.upper()} block ({i} not in {block[0]}..{block[-1]})"

    def test_the_api_serves_the_same_order(self, client, seeded_db, auth_headers):
        client.get("/dm-tools", headers=auth_headers)
        api = [m["name"] for m in client.get("/api/dm/monsters", headers=auth_headers).json()["monsters"]]
        assert api == [m["n"] for m in asset_payload()["monsters"]]


class TestTheNpcListUsesIt:
    def test_the_reference_rows_are_alpha_key_sorted(self, client, seeded_db, auth_headers):
        client.get("/dm-tools", headers=auth_headers)
        from services.text import alpha_key
        names = [n["n"] for n in asset_payload()["manualNpcs"]]
        assert len(names) > 50, "reference NPC rows look empty in this environment"
        assert names == sorted(names, key=alpha_key), "the NPCs tab is not in reader order"

    def test_sorting_the_rows_did_not_break_the_id_lookup(self, client, seeded_db, auth_headers):
        """A manual NPC's id is its index into the RAW npcs.json (id -1 → row 0) — the
        panel sorts a copy, so every row must still resolve to its own record."""
        client.get("/dm-tools", headers=auth_headers)
        from services.text import alpha_key
        rows = asset_payload()["manualNpcs"]
        ids = [r["id"] for r in rows]
        assert len(set(ids)) == len(ids), "the sort duplicated a row id"
        for row in (rows[0], rows[len(rows) // 2], rows[-1]):
            detail = client.get(f"/api/dm/npc/{row['id']}", headers=auth_headers)
            assert detail.status_code == 200, f"id {row['id']} no longer resolves"
            assert detail.json()["name"] == row["n"], \
                f"id {row['id']} resolves to {detail.json()['name']!r}, panel shows {row['n']!r}"

    def test_the_dms_own_npcs_are_alphabetical_inside_their_group(self, client, seeded_db, auth_headers):
        from services.text import alpha_key
        con = sqlite3.connect(str(seeded_db["db_path"]))
        for name, is_enemy in (("Zed the Loud", 0), ("alice the quiet", 0),
                               ("Éowyn of Rohan", 1), ("Bob the Brute", 1)):
            con.execute("INSERT INTO dm_npcs (user_id, name, is_enemy) VALUES (1, ?, ?)",
                        (name, is_enemy))
        con.commit()
        con.close()
        rows = dm_npc_rows(client.get("/dm-tools", headers=auth_headers).text)
        assert rows, "no NPC rows rendered at all"
        # Enemies keep their group (the badges say so) and come first; inside each group
        # the order is alphabetical, so "alice" files under A instead of after every
        # capitalised name.
        groups = [rows[:sum(1 for _, e in rows if e)], rows[sum(1 for _, e in rows if e):]]
        assert groups[0] and all(e for _, e in groups[0]), "enemies must stay first"
        for group in groups:
            names = [n for n, _ in group]
            assert names == sorted(names, key=alpha_key), f"group out of order: {names}"
        # data-name is lowercased by the template, so compare lowered names
        ordered = [n for n, _ in rows]
        assert ordered.index("alice the quiet") < ordered.index("zed the loud")
        # "Éowyn" files under E — after Bob, before Zed. A codepoint sort put her last of all.
        assert ordered.index("bob the brute") < ordered.index("éowyn of rohan") < ordered.index("zed the loud")


class TestTheTrapLibraryUsesIt:
    def test_the_trap_rows_are_alpha_key_sorted(self, client, seeded_db, auth_headers):
        client.get("/dm-tools", headers=auth_headers)
        from services.text import alpha_key
        names = [t["n"] for t in asset_payload()["traps"]]
        assert names == sorted(names, key=alpha_key)


def test_no_plain_lower_sort_is_left_on_a_browsable_name_list():
    """The defect class: `.lower()` used as a sort key for a list a human scans."""
    offenders = []
    for rel in ("routes/characters/helpers.py", "routes/dm.py"):
        for lineno, line in enumerate((ROOT / rel).read_text().splitlines(), 1):
            if re.search(r'sort\(key=lambda .*name.*\.lower\(\)', line):
                offenders.append(f"{rel}:{lineno}")
    assert offenders == [], f"use services.text.alpha_key: {offenders}"
