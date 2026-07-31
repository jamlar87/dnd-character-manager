"""Character progression regression matrix.

API-level characterization tests for creation, level-up, de-level,
multiclassing, HP, ASI, spell slots, subclass ownership, and malformed
state. Assert resulting DATABASE state, not just HTTP status.

Coverage map (plan: progression regression matrix):
  - Character creation state assertions
  - Single-class level transitions (1-20 path)
  - Multiclass transitions (full caster + Warlock, half + full, martial + caster)
  - De-level ownership and ASI tests
  - Spell slots and expanded list tests
  - Repeated transitions and malformed state tests
"""

import json
import sqlite3

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────

def _create(client, headers, **overrides):
    """Create a character; returns char id."""
    payload = {
        "name": "Test Progression",
        "race": "Human",
        "class_name": "Wizard",
        "level": 1,
        "abilities": {
            "strength": 14, "dexterity": 14, "constitution": 14,
            "intelligence": 14, "wisdom": 14, "charisma": 14,
        },
    }
    payload.update(overrides)
    resp = client.post("/api/character/create", json=payload, headers=headers)
    assert resp.status_code == 200, f"create failed: {resp.status_code} {resp.text[:300]}"
    data = resp.json()
    assert "id" in data
    return data["id"]


def _level_up(client, headers, char_id, target_level, **overrides):
    payload = {"target_level": target_level}
    payload.update(overrides)
    return client.post(f"/api/character/{char_id}/apply-level-up", json=payload, headers=headers)


def _de_level(client, headers, char_id, target_level, **overrides):
    payload = {"target_level": target_level}
    payload.update(overrides)
    return client.post(f"/api/character/{char_id}/de-level", json=payload, headers=headers)


def _row(db_path, char_id):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM characters WHERE id = ?", (char_id,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def _slots(row):
    """Parse spell_slot_data JSON into a dict."""
    return json.loads(row.get("spell_slot_data") or "{}")


def _class_levels(row):
    return json.loads(row.get("class_levels") or "{}")


def _spells(db_path, char_id):
    con = sqlite3.connect(str(db_path))
    try:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(
            "SELECT spell_name, spell_level, prepared, source FROM character_spells WHERE character_id = ?",
            (char_id,),
        ).fetchall()]
    finally:
        con.close()


def _assert_ok(resp):
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text[:400]}"
    body = resp.json()
    assert body.get("ok") is True, f"expected ok=True, got {body}"
    return body


# ── Creation state ─────────────────────────────────────────────────────────

class TestCreationState:
    def test_wizard_level_one_state(self, client, auth_headers, seeded_db):
        """Wizard 1: HD 6, CON 15 (+2 mod), prof +2, one class, 2 first-level slots."""
        cid = _create(client, auth_headers)
        row = _row(seeded_db["db_path"], cid)
        assert row is not None
        assert row["level"] == 1
        assert row["hp_max"] == 6 + 2          # 1d6 + CON mod
        assert row["proficiency_bonus"] == 2
        assert _class_levels(row) == {"Wizard": 1}
        assert row["hit_dice"] == "1d6"
        assert row["class_name"] == "Wizard"
        slots = _slots(row)
        assert slots.get("by_level", {}).get("1") == 2
        assert slots.get("by_level", {}).get("2", 0) == 0
        # Wizard is a prepared caster: creation auto-loads L1 wizard spells
        spells = _spells(seeded_db["db_path"], cid)
        assert len(spells) > 0

    def test_fighter_level_one_no_spells(self, client, auth_headers, seeded_db):
        """Fighter 1: HD 10, CON 15, no spell slots."""
        cid = _create(client, auth_headers, class_name="Fighter")
        row = _row(seeded_db["db_path"], cid)
        assert row["hp_max"] == 10 + 2
        assert row["hit_dice"] == "1d10"
        slots = _slots(row)
        assert slots.get("slots", 0) == 0
        assert all(v == 0 for v in slots.get("by_level", {}).values())

    def test_creation_hp_uses_con_mod(self, client, auth_headers, seeded_db):
        """CON 10 (mod 0): Wizard 1 hp = 6."""
        cid = _create(client, auth_headers, abilities={
            "strength": 10, "dexterity": 10, "constitution": 10,
            "intelligence": 14, "wisdom": 10, "charisma": 10,
        })
        row = _row(seeded_db["db_path"], cid)
        assert row["hp_max"] == 6

    def test_human_race_asi_applied(self, client, auth_headers, seeded_db):
        """Human +1 to all: base 14 STR becomes 15."""
        cid = _create(client, auth_headers)
        row = _row(seeded_db["db_path"], cid)
        assert row["strength"] == 15
        assert row["constitution"] == 15


# ── Single-class level transitions ─────────────────────────────────────────

class TestSingleClassTransitions:
    def test_wizard_1_to_2_hp_and_slots(self, client, auth_headers, seeded_db):
        """Wizard 1→2: +avg hp (3+1+CON2 = 6), slots 1st: 2→3, prof still +2."""
        cid = _create(client, auth_headers)
        body = _assert_ok(_level_up(client, auth_headers, cid, 2))
        assert body["new_level"] == 2
        row = _row(seeded_db["db_path"], cid)
        assert row["level"] == 2
        assert row["hp_max"] == (6 + 2) + 6
        assert row["proficiency_bonus"] == 2
        assert _class_levels(row) == {"Wizard": 2}
        slots = _slots(row)
        assert slots.get("by_level", {}).get("1") == 3

    def test_wizard_3_gains_second_level_slots(self, client, auth_headers, seeded_db):
        cid = _create(client, auth_headers)
        _assert_ok(_level_up(client, auth_headers, cid, 3))
        slots = _slots(_row(seeded_db["db_path"], cid))
        assert slots.get("by_level", {}).get("2") >= 2

    def test_wizard_5_prof_and_third_level_slots(self, client, auth_headers, seeded_db):
        """Level 5: proficiency +3, third-level slots available."""
        cid = _create(client, auth_headers)
        _assert_ok(_level_up(client, auth_headers, cid, 5))
        row = _row(seeded_db["db_path"], cid)
        assert row["proficiency_bonus"] == 3
        slots = _slots(row)
        assert slots.get("by_level", {}).get("3") >= 2

    def test_asi_at_level_4_updates_ability(self, client, auth_headers, seeded_db):
        """ASI at 4: STR 15 → 17, recorded in asi_history."""
        cid = _create(client, auth_headers)
        _assert_ok(_level_up(client, auth_headers, cid, 4, asi_choices={"4": {"Strength": 2}}))
        row = _row(seeded_db["db_path"], cid)
        assert row["strength"] == 17
        asi_hist = json.loads(row.get("asi_history") or "[]")
        assert any(e.get("level") == 4 and e.get("type") == "asi" for e in asi_hist)

    def test_feat_choice_at_level_4_recorded(self, client, auth_headers, seeded_db):
        cid = _create(client, auth_headers)
        _assert_ok(_level_up(client, auth_headers, cid, 4,
                             asi_choices={"4": "feat:alert"}, feat_asi_choices={"4": "Strength"}))
        row = _row(seeded_db["db_path"], cid)
        asi_hist = json.loads(row.get("asi_history") or "[]")
        assert any(e.get("level") == 4 and e.get("type") == "feat" and e.get("feat") == "alert"
                   for e in asi_hist)

    def test_subclass_set_on_level_up(self, client, auth_headers, seeded_db):
        """Wizard 2: Arcane Tradition (School of Evocation)."""
        cid = _create(client, auth_headers)
        _assert_ok(_level_up(client, auth_headers, cid, 2, subclass="School of Evocation"))
        row = _row(seeded_db["db_path"], cid)
        assert row["subclass"] == "School of Evocation"
        # Subclass features present in feature_data
        feat_data = json.loads(row.get("feature_data") or "[]")
        names = {f.get("name", "") for f in feat_data if isinstance(f, dict)}
        assert any("Evocation" in n for n in names) or any("Sculpt Spells" in n for n in names)

    def test_retroactive_con_hp_on_asi(self, client, auth_headers, seeded_db):
        """CON 15→17 at level 4 ASI: mod 2→3, retroactive +1 HP per prior level."""
        cid = _create(client, auth_headers)
        _assert_ok(_level_up(client, auth_headers, cid, 4, asi_choices={"4": {"Constitution": 2}}))
        row = _row(seeded_db["db_path"], cid)
        # Base 1→4: 8 + 6 + 6 + 6 = 26; retro CON delta = +3 (levels 1-3) → 29
        # With per-level retro (PHB): L4 HP uses CON 17 (7), prior 3 levels get +1 each → 30
        assert row["hp_max"] == 30

    def test_hp_never_drops_on_invalid_asi(self, client, auth_headers, seeded_db):
        """ASI that would exceed 20 is rejected with 400; state unchanged."""
        cid = _create(client, auth_headers)  # STR 15
        resp = _level_up(client, auth_headers, cid, 4, asi_choices={"4": {"Strength": 10}})
        assert resp.status_code == 400
        row = _row(seeded_db["db_path"], cid)
        assert row["strength"] == 15
        assert row["level"] == 1  # nothing applied


# ── Multiclass transitions ─────────────────────────────────────────────────

class TestMulticlass:
    def test_fighter_then_wizard_multiclass(self, client, auth_headers, seeded_db):
        """Fighter 1 → +Wizard 1: class_levels both; Fighter contributes 0 to
        combined caster level (PHB p.165), so caster level 1 → 2 first slots."""
        cid = _create(client, auth_headers, class_name="Fighter")
        body = _assert_ok(_level_up(client, auth_headers, cid, 2, class_to_level="Wizard"))
        row = _row(seeded_db["db_path"], cid)
        assert _class_levels(row) == {"Fighter": 1, "Wizard": 1}
        assert row["level"] == 2
        slots = _slots(row)
        assert slots.get("multiclass") is True
        assert slots.get("by_level", {}).get("1") == 2  # caster level 1 → 2 first
        assert row["hp_max"] == (10 + 2) + (6 // 2 + 1 + 2)  # fighter + wizard avg

    def test_multiclass_prereq_rejected(self, client, auth_headers, seeded_db):
        """Wizard multiclass needs INT 13. Low-INT fighter is rejected."""
        cid = _create(client, auth_headers, class_name="Fighter", abilities={
            "strength": 14, "dexterity": 14, "constitution": 14,
            "intelligence": 8, "wisdom": 10, "charisma": 10,
        })
        resp = _level_up(client, auth_headers, cid, 2, class_to_level="Wizard")
        assert resp.status_code == 400
        assert _class_levels(_row(seeded_db["db_path"], cid)) == {"Fighter": 1}

    def test_warlock_pact_slots_separate(self, client, auth_headers, seeded_db):
        """Warlock 1 + Wizard 1: pact slots = 1 (Warlock 1), spell slots separate."""
        cid = _create(client, auth_headers, class_name="Warlock")
        _assert_ok(_level_up(client, auth_headers, cid, 2, class_to_level="Wizard"))
        row = _row(seeded_db["db_path"], cid)
        slots = _slots(row)
        assert slots.get("multiclass") is True
        assert slots.get("pact_slots", {}).get("slots") == 1
        assert slots.get("pact_slots", {}).get("slot_level") == 1
        # Wizard 1 (pact excluded from combined table) → 2 first-level slots
        assert slots.get("by_level", {}).get("1") >= 2

    def test_paladin_sorcerer_half_plus_full(self, client, auth_headers, seeded_db):
        """Paladin 2 (half, round down) + Sorcerer 1: combined caster level = 2."""
        cid = _create(client, auth_headers, class_name="Paladin")
        _assert_ok(_level_up(client, auth_headers, cid, 2, class_to_level="Paladin"))
        _assert_ok(_level_up(client, auth_headers, cid, 3, class_to_level="Sorcerer"))
        row = _row(seeded_db["db_path"], cid)
        assert _class_levels(row) == {"Paladin": 2, "Sorcerer": 1}
        slots = _slots(row)
        assert slots.get("by_level", {}).get("1") == 3  # caster level 2 → 3 first-level

    def test_hit_dice_multiclass_string(self, client, auth_headers, seeded_db):
        cid = _create(client, auth_headers, class_name="Fighter")
        _assert_ok(_level_up(client, auth_headers, cid, 2, class_to_level="Wizard"))
        row = _row(seeded_db["db_path"], cid)
        assert row["hit_dice"] == "1d10 + 1d6"


# ── De-level ownership and ASI ─────────────────────────────────────────────

class TestDeLevel:
    def test_de_level_clears_subclass_gained_in_lost_levels(self, client, auth_headers, seeded_db):
        """Wizard 2 (Evocation) → 1: subclass cleared (Arcane Tradition at 2)."""
        cid = _create(client, auth_headers)
        _assert_ok(_level_up(client, auth_headers, cid, 2, subclass="School of Evocation"))
        assert _row(seeded_db["db_path"], cid)["subclass"] == "School of Evocation"
        _assert_ok(_de_level(client, auth_headers, cid, 1))
        row = _row(seeded_db["db_path"], cid)
        assert row["subclass"] == ""
        assert row["level"] == 1

    def test_de_level_reverts_asi(self, client, auth_headers, seeded_db):
        """L4 with STR ASI → de-level to 3: STR 17→15, asi_history entry removed."""
        cid = _create(client, auth_headers)
        _assert_ok(_level_up(client, auth_headers, cid, 4, asi_choices={"4": {"Strength": 2}}))
        assert _row(seeded_db["db_path"], cid)["strength"] == 17
        _assert_ok(_de_level(client, auth_headers, cid, 3, abilities={"Strength": 15}))
        row = _row(seeded_db["db_path"], cid)
        assert row["strength"] == 15
        assert json.loads(row.get("asi_history") or "[]") == []

    def test_de_level_multiclass_removes_correct_class(self, client, auth_headers, seeded_db):
        """Fighter1/Wizard2 → de-level Wizard: Wizard drops, Fighter stays."""
        cid = _create(client, auth_headers, class_name="Fighter")
        _assert_ok(_level_up(client, auth_headers, cid, 2, class_to_level="Wizard"))
        _assert_ok(_level_up(client, auth_headers, cid, 3, class_to_level="Wizard"))
        assert _class_levels(_row(seeded_db["db_path"], cid)) == {"Fighter": 1, "Wizard": 2}
        _assert_ok(_de_level(client, auth_headers, cid, 2, class_to_level="Wizard"))
        row = _row(seeded_db["db_path"], cid)
        assert _class_levels(row) == {"Fighter": 1, "Wizard": 1}
        assert row["level"] == 2

    def test_de_level_keeps_subclass_of_other_class(self, client, auth_headers, seeded_db):
        """Fighter3(Champion)+Rogue1 → de-level Rogue: Champion kept (belongs to Fighter)."""
        cid = _create(client, auth_headers, class_name="Fighter")
        _assert_ok(_level_up(client, auth_headers, cid, 3, class_to_level="Fighter", subclass="Champion"))
        _assert_ok(_level_up(client, auth_headers, cid, 4, class_to_level="Rogue"))
        assert _row(seeded_db["db_path"], cid)["subclass"] == "Champion"
        _assert_ok(_de_level(client, auth_headers, cid, 3, class_to_level="Rogue"))
        row = _row(seeded_db["db_path"], cid)
        assert row["subclass"] == "Champion"
        assert _class_levels(row) == {"Fighter": 3}

    def test_de_level_floor_at_one(self, client, auth_headers, seeded_db):
        cid = _create(client, auth_headers)
        resp = _de_level(client, auth_headers, cid, 0)
        assert resp.status_code in (200, 400)
        if resp.status_code == 200:
            assert _row(seeded_db["db_path"], cid)["level"] >= 1


# ── Spell slots and expanded lists ─────────────────────────────────────────

class TestSpellWiring:
    def test_expanded_spell_list_auto_inserted(self, client, auth_headers, seeded_db):
        """Warlock 1 (The Fiend): patron spells inserted with Expanded Spell List source."""
        cid = _create(client, auth_headers, class_name="Warlock", subclass="The Fiend")
        spells = _spells(seeded_db["db_path"], cid)
        expanded = [s for s in spells if s["source"] == "Expanded Spell List"]
        assert len(expanded) > 0, f"no expanded spells: {spells}"
        names = {s["spell_name"] for s in expanded}
        assert "Burning Hands" in names or "Command" in names

    def test_domain_spells_auto_prepared(self, client, auth_headers, seeded_db):
        """Cleric 1 (Life Domain): domain spells marked prepared."""
        cid = _create(client, auth_headers, class_name="Cleric", subclass="Life Domain")
        spells = _spells(seeded_db["db_path"], cid)
        prepared = {s["spell_name"] for s in spells if s["prepared"] == 1}
        assert "Bless" in prepared or "Cure Wounds" in prepared

    def test_level_up_adds_higher_slot_spells_for_prepared_caster(self, client, auth_headers, seeded_db):
        """Wizard 1→3: L2 spells auto-added to spellbook."""
        cid = _create(client, auth_headers)
        before = {s["spell_name"] for s in _spells(seeded_db["db_path"], cid)}
        _assert_ok(_level_up(client, auth_headers, cid, 3))
        after = {s["spell_name"] for s in _spells(seeded_db["db_path"], cid)}
        assert len(after) > len(before)


# ── Repeated transitions and malformed state ───────────────────────────────

class TestRepeatedAndMalformed:
    def test_repeated_level_up_down_cycle_hp_stable(self, client, auth_headers, seeded_db):
        """Level 1→5, de-level 5→1, re-level 1→5: HP matches fresh 5th-level build."""
        cid = _create(client, auth_headers)
        _assert_ok(_level_up(client, auth_headers, cid, 5))
        fresh5 = _create(client, auth_headers, name="Fresh Five")
        _assert_ok(_level_up(client, auth_headers, fresh5, 5))
        target_hp = _row(seeded_db["db_path"], fresh5)["hp_max"]
        _assert_ok(_de_level(client, auth_headers, cid, 1))
        assert _row(seeded_db["db_path"], cid)["level"] == 1
        _assert_ok(_level_up(client, auth_headers, cid, 5))
        row = _row(seeded_db["db_path"], cid)
        assert row["hp_max"] == target_hp, f"{row['hp_max']} != {target_hp}"

    def test_malformed_class_levels_falls_back(self, client, auth_headers, seeded_db):
        """Garbage class_levels JSON must not 500 — parse falls back to class_name/level."""
        cid = _create(client, auth_headers)
        con = sqlite3.connect(str(seeded_db["db_path"]))
        con.execute("UPDATE characters SET class_levels = 'not json' WHERE id = ?", (cid,))
        con.commit()
        con.close()
        resp = _level_up(client, auth_headers, cid, 2)
        _assert_ok(resp)
        row = _row(seeded_db["db_path"], cid)
        assert _class_levels(row) == {"Wizard": 2}

    def test_empty_class_levels_falls_back(self, client, auth_headers, seeded_db):
        cid = _create(client, auth_headers)
        con = sqlite3.connect(str(seeded_db["db_path"]))
        con.execute("UPDATE characters SET class_levels = '{}' WHERE id = ?", (cid,))
        con.commit()
        con.close()
        _assert_ok(_level_up(client, auth_headers, cid, 2))
        assert _class_levels(_row(seeded_db["db_path"], cid)) == {"Wizard": 2}

    def test_target_level_clamped_to_20(self, client, auth_headers, seeded_db):
        cid = _create(client, auth_headers)
        resp = _level_up(client, auth_headers, cid, 99)
        # Pydantic rejects target_level > 20 with 422 before the handler clamps
        assert resp.status_code == 422
        assert _row(seeded_db["db_path"], cid)["level"] == 1

    def test_de_level_target_above_current_rejected(self, client, auth_headers, seeded_db):
        """target_level >= current level should not increase the character."""
        cid = _create(client, auth_headers)
        _assert_ok(_level_up(client, auth_headers, cid, 3))
        resp = _de_level(client, auth_headers, cid, 5)
        assert resp.status_code in (200, 400)
        assert _row(seeded_db["db_path"], cid)["level"] <= 3

    def test_spells_preserved_across_level_cycle(self, client, auth_headers, seeded_db):
        """Level 1→5→3: spell count at 3 ≥ count at fresh 3rd-level wizard."""
        cid = _create(client, auth_headers)
        _assert_ok(_level_up(client, auth_headers, cid, 5))
        _assert_ok(_de_level(client, auth_headers, cid, 3))
        count_after = len(_spells(seeded_db["db_path"], cid))
        fresh3 = _create(client, auth_headers, name="Fresh Three")
        _assert_ok(_level_up(client, auth_headers, fresh3, 3))
        count_fresh = len(_spells(seeded_db["db_path"], fresh3))
        assert count_after >= count_fresh
