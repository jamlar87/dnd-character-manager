"""Template rendering tests — verify various character types render without 500s.

These use the running server (or TestClient) to hit the character sheet
endpoint for different class/race/multiclass combinations.
"""

import pytest
import json


class TestSheetRenders:
    """Character sheet must return 200 for valid character ids."""

    def test_simple_fighter(self, client, auth_headers, seeded_db):
        """Create a simple Fighter and verify sheet loads."""
        # Create a basic character via direct DB insert
        import sqlite3
        db_path = seeded_db["db_path"]
        con = sqlite3.connect(str(db_path))
        con.execute("""
            INSERT INTO characters (user_id, name, race, class_name, level, strength, dexterity,
                constitution, intelligence, wisdom, charisma, hp_max, hp_current, ac)
            VALUES (1, 'Test Fighter', 'Human', 'Fighter', 1, 15, 13, 14, 10, 12, 8, 12, 12, 16)
        """)
        char_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Add basic feature_data
        con.execute(
            "UPDATE characters SET features='[]', feature_data='[]' WHERE id=?",
            (char_id,)
        )
        con.commit()
        con.close()

        resp = client.get(f"/character/{char_id}", headers=auth_headers)
        assert resp.status_code == 200, f"Fighter sheet failed: {resp.status_code}"
        assert "Test Fighter" in resp.text

    def test_bard_single(self, client, auth_headers, seeded_db):
        """Create a single-class Bard and verify sheet."""
        import sqlite3
        db_path = seeded_db["db_path"]
        con = sqlite3.connect(str(db_path))
        con.execute("""
            INSERT INTO characters (user_id, name, race, class_name, subclass, level, strength,
                dexterity, constitution, intelligence, wisdom, charisma, hp_max, hp_current, ac,
                class_levels)
            VALUES (1, 'Test Bard', 'Elf', 'Bard', 'College of Lore', 5,
                    8, 14, 12, 13, 10, 16, 32, 32, 14,
                    '{"Bard": 5}')
        """)
        char_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute(
            "UPDATE characters SET features='[]', feature_data='[]' WHERE id=?",
            (char_id,)
        )
        con.commit()
        con.close()

        resp = client.get(f"/character/{char_id}", headers=auth_headers)
        assert resp.status_code == 200, f"Bard sheet failed: {resp.status_code}"

    def test_multiclass_paladin_bard(self, client, auth_headers, seeded_db):
        """Create a Paladin/Bard multiclass like Tybald and verify sheet."""
        import sqlite3
        db_path = seeded_db["db_path"]
        con = sqlite3.connect(str(db_path))
        con.execute("""
            INSERT INTO characters (user_id, name, race, subrace, class_name, subclass, level,
                strength, dexterity, constitution, intelligence, wisdom, charisma,
                hp_max, hp_current, ac, class_levels, features, feature_data,
                asi_history, fighting_style)
            VALUES (1, 'Test Tybald', 'Human', 'Variant Human', 'Bard', 'Oath of Vengeance', 8,
                    16, 14, 16, 9, 14, 16,
                    74, 74, 12,
                    '{"Paladin": 6, "Bard": 2}',
                    '["L1: Divine Sense","L1: Lay on Hands","L4: Ability Score Improvement"]',
                    '[{"name":"Divine Sense","level":"L1"},{"name":"Lay on Hands","level":"L1"},{"name":"Ability Score Improvement","level":"L4"}]',
                    '[{"type":"feat","level":4,"feat":"war_caster"}]',
                    '')
        """)
        char_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.commit()
        con.close()

        resp = client.get(f"/character/{char_id}", headers=auth_headers)
        assert resp.status_code == 200, f"Multiclass sheet failed: {resp.status_code}"
        # Verify key features render
        assert "Ability Score Improvement" in resp.text
        assert "War Caster" in resp.text
        assert "Divine Sense" in resp.text
        # Source badges should be present
        assert "PHB 2014" in resp.text
