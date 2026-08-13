"""Database schema initialization and migration helpers.

Extracted from main.py (2026-08-12). DB path / data dir / session TTL
come from main lazily at call time to avoid import-time circulars.
"""

from __future__ import annotations

import sqlite3

def init_db():
    from main import DATA_DIR, SESSION_TTL_DAYS, get_db
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            race TEXT NOT NULL,
            subrace TEXT DEFAULT '',
            class_name TEXT NOT NULL,
            subclass TEXT DEFAULT '',
            level INTEGER DEFAULT 1,
            background TEXT DEFAULT '',
            alignment TEXT DEFAULT '',
            personality TEXT DEFAULT '',
            backstory TEXT DEFAULT '',
            strength INTEGER DEFAULT 10,
            dexterity INTEGER DEFAULT 10,
            constitution INTEGER DEFAULT 10,
            intelligence INTEGER DEFAULT 10,
            wisdom INTEGER DEFAULT 10,
            charisma INTEGER DEFAULT 10,
            hp_max INTEGER DEFAULT 10,
            hp_current INTEGER DEFAULT 10,
            temp_hp INTEGER DEFAULT 0,
            ac INTEGER DEFAULT 10,
            speed INTEGER DEFAULT 30,
            proficiency_bonus INTEGER DEFAULT 2,
            hit_dice TEXT DEFAULT '1d8',
            hit_dice_used INTEGER DEFAULT 0,
            class_levels TEXT DEFAULT '{}',
            death_saves_success INTEGER DEFAULT 0,
            death_saves_fail INTEGER DEFAULT 0,
            skills TEXT DEFAULT '[]',
            tool_proficiencies TEXT DEFAULT '[]',
            weapon_proficiencies TEXT DEFAULT '[]',
            armor_proficiencies TEXT DEFAULT '[]',
            languages TEXT DEFAULT '[]',
            features TEXT DEFAULT '[]',
            inventory TEXT DEFAULT '[]',
            equipped TEXT DEFAULT '[]',
            notes TEXT DEFAULT '',
            cp INTEGER DEFAULT 0,
            gp INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS character_spells (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            character_id INTEGER NOT NULL,
            spell_name TEXT NOT NULL,
            spell_level INTEGER DEFAULT 0,
            prepared INTEGER DEFAULT 0,
            slots_max INTEGER DEFAULT 0,
            slots_used INTEGER DEFAULT 0,
            source TEXT DEFAULT '',
            FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS dm_npcs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            race TEXT NOT NULL DEFAULT 'Human',
            class_name TEXT DEFAULT '',
            subclass TEXT DEFAULT '',
            level INTEGER DEFAULT 1,
            is_enemy INTEGER DEFAULT 0,
            is_party_npc INTEGER DEFAULT 0,
            strength INTEGER DEFAULT 10,
            dexterity INTEGER DEFAULT 10,
            constitution INTEGER DEFAULT 10,
            intelligence INTEGER DEFAULT 10,
            wisdom INTEGER DEFAULT 10,
            charisma INTEGER DEFAULT 10,
            hp_max INTEGER DEFAULT 10,
            hp_current INTEGER DEFAULT 10,
            temp_hp INTEGER DEFAULT 0,
            ac INTEGER DEFAULT 10,
            speed INTEGER DEFAULT 30,
            proficiency_bonus INTEGER DEFAULT 2,
            hit_dice TEXT DEFAULT '1d8',
            hit_dice_used INTEGER DEFAULT 0,
            skills TEXT DEFAULT '[]',
            features TEXT DEFAULT '[]',
            inventory TEXT DEFAULT '[]',
            notes TEXT DEFAULT '',
            portrait_url TEXT DEFAULT '',
            alignment TEXT DEFAULT 'True Neutral',
            role TEXT DEFAULT 'NPC',
            faction TEXT DEFAULT '',
            xp_reward INTEGER DEFAULT 0,
            source TEXT DEFAULT '',
            source_book TEXT DEFAULT '',
            source_page TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS dm_encounters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            location TEXT DEFAULT '',
            environment TEXT DEFAULT '',
            difficulty TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'planned',
            xp_total INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS dm_encounter_npcs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            encounter_id INTEGER NOT NULL,
            npc_id INTEGER NOT NULL,
            initiative INTEGER DEFAULT 0,
            hp_current INTEGER DEFAULT 0,
            hp_max INTEGER DEFAULT 0,
            ac INTEGER DEFAULT 10,
            defeated INTEGER DEFAULT 0,
            spell_slots_used TEXT DEFAULT '{}',
            notes TEXT DEFAULT '',
            FOREIGN KEY (encounter_id) REFERENCES dm_encounters(id) ON DELETE CASCADE,
            FOREIGN KEY (npc_id) REFERENCES dm_npcs(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS dm_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            party_level INTEGER DEFAULT 1,
            party_size INTEGER DEFAULT 4,
            notes TEXT DEFAULT '',
            session_notes TEXT DEFAULT '',
            quests TEXT DEFAULT '[]',
            locations TEXT DEFAULT '[]',
            characters TEXT DEFAULT '[]',
            npcs TEXT DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS dm_campaign_characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL,
            status TEXT DEFAULT 'active',
            notes TEXT DEFAULT '',
            FOREIGN KEY (campaign_id) REFERENCES dm_campaigns(id) ON DELETE CASCADE,
            FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS campaign_team_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            qty INTEGER DEFAULT 1,
            description TEXT DEFAULT '',
            gp_value INTEGER DEFAULT 0,
            added_by_user_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (campaign_id) REFERENCES dm_campaigns(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL DEFAULT (datetime('now', '+30 days')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS dm_custom_traps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'mechanical',
            danger TEXT NOT NULL DEFAULT 'dangerous',
            trigger TEXT DEFAULT '',
            detection_dc INTEGER,
            detection_skill TEXT DEFAULT 'Perception',
            detection_detail TEXT DEFAULT '',
            disarm_dc INTEGER,
            disarm_method TEXT DEFAULT '',
            disarm_detail TEXT DEFAULT '',
            effect TEXT DEFAULT '',
            save_dc INTEGER,
            save_ability TEXT DEFAULT 'Dexterity',
            damage TEXT DEFAULT '',
            damage_type TEXT DEFAULT '',
            area TEXT DEFAULT '',
            description TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
    """)
    db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))")
    db.execute("INSERT OR IGNORE INTO schema_migrations (version) VALUES (1)")
    # Migration: session expiry for databases created before this field existed.
    session_cols = {r[1] for r in db.execute("PRAGMA table_info(sessions)")}
    if "expires_at" not in session_cols:
        db.execute("ALTER TABLE sessions ADD COLUMN expires_at TEXT")
        db.execute("UPDATE sessions SET expires_at = datetime(created_at, ?) WHERE expires_at IS NULL", (f"+{SESSION_TTL_DAYS} days",))
    # Migration: add personality/backstory columns if missing
    try:
        db.execute("ALTER TABLE characters ADD COLUMN personality TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        db.execute("ALTER TABLE characters ADD COLUMN backstory TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # Migration: build data columns
    for col, coltype in [("feature_data","TEXT DEFAULT '[]'"),
                          ("attacks_data","TEXT DEFAULT '[]'"),
                          ("spell_slot_data","TEXT DEFAULT '{}'"),
                          ("passive_perception","INTEGER DEFAULT 10"),
                          ("inspiration","INTEGER DEFAULT 0"),
                          ("exhaustion","INTEGER DEFAULT 0"),
                          ("portrait_url","TEXT DEFAULT ''"),
                          ("portrait_prompt","TEXT DEFAULT ''"),
                          ("save_proficiencies","TEXT DEFAULT '[]'"),
                          ("damage_resistances","TEXT DEFAULT '[]'"),
                          ("damage_immunities","TEXT DEFAULT '[]'"),
                          ("damage_vulnerabilities","TEXT DEFAULT '[]'"),
                          ("condition_immunities","TEXT DEFAULT '[]'"),
                          ("background_data","TEXT DEFAULT ''"),
                          ("spell_slots_used","TEXT DEFAULT '{}'")]:
        try:
            db.execute(f"ALTER TABLE characters ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass
    # Migration: dm_encounter_npcs new columns
    for col, coltype in [("defeated", "INTEGER DEFAULT 0"),
                          ("spell_slots_used", "TEXT DEFAULT '{}'"),
                          ("creature_data", "TEXT DEFAULT ''")]:
        try:
            db.execute(f"ALTER TABLE dm_encounter_npcs ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass
    # Migration: dm_encounters combat_state
    try:
        db.execute("ALTER TABLE dm_encounters ADD COLUMN combat_state TEXT DEFAULT '{}'")
    except sqlite3.OperationalError:
        pass
    # Migration: dm_campaigns characters column
    try:
        db.execute("ALTER TABLE dm_campaigns ADD COLUMN characters TEXT DEFAULT '[]'")
    except sqlite3.OperationalError:
        pass
    # Migration: source on character_spells for Magic Initiate / class-source spell tagging
    try:
        db.execute("ALTER TABLE character_spells ADD COLUMN source TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass

    # Migration: dm_campaigns npcs column
    try:
        db.execute("ALTER TABLE dm_campaigns ADD COLUMN npcs TEXT DEFAULT '[]'")
    except sqlite3.OperationalError:
        pass
    # Migration: class_levels for multiclass support
    try:
        db.execute("ALTER TABLE characters ADD COLUMN class_levels TEXT DEFAULT '{}'")
    except sqlite3.OperationalError:
        pass
    # Migration: cp tracker for copper pieces
    try:
        db.execute("ALTER TABLE characters ADD COLUMN cp INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    # Migration: gp tracker for gold pieces
    try:
        db.execute("ALTER TABLE characters ADD COLUMN gp INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    # Migration: attuned_items for item attunement tracking
    try:
        db.execute("ALTER TABLE characters ADD COLUMN attuned_items TEXT DEFAULT '[]'")
    except sqlite3.OperationalError:
        pass
    # Migration: dragonborn_ancestry for Dragonborn draconic ancestry choice
    try:
        db.execute("ALTER TABLE characters ADD COLUMN dragonborn_ancestry TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # Migration: ranger favored choices
    try:
        db.execute("ALTER TABLE characters ADD COLUMN favored_enemies TEXT DEFAULT '[]'")
    except sqlite3.OperationalError:
        pass
    try:
        db.execute("ALTER TABLE characters ADD COLUMN favored_terrains TEXT DEFAULT '[]'")
    except sqlite3.OperationalError:
        pass
    # Migration: monk/paladin/warlock choices
    for col, coltype in [
        ("expertise_skills", "TEXT DEFAULT '[]'"),
        ("fighting_style", "TEXT DEFAULT ''"),
        ("metamagic", "TEXT DEFAULT '[]'"),
        ("invocations", "TEXT DEFAULT '[]'"),
        ("pact_boon", "TEXT DEFAULT ''"),
        ("maneuvers", "TEXT DEFAULT '[]'"),
        ("magical_secrets", "TEXT DEFAULT '[]'"),
        ("totem_spirits", "TEXT DEFAULT '{}'"),
        ("hunters_prey", "TEXT DEFAULT ''"),
        ("infusions", "TEXT DEFAULT '[]'"),
        ("asi_history", "TEXT DEFAULT '[]'"),
        ("metamagic_history", "TEXT DEFAULT '[]'"),
        ("summons", "TEXT DEFAULT '[]'"),
        ("conditions", "TEXT DEFAULT '[]'"),
        ("journal", "TEXT DEFAULT ''"),
        ("combat_notes", "TEXT DEFAULT ''"),
    ]:
        try:
            db.execute(f"ALTER TABLE characters ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass
    # Backfill: populate class_levels from class_name + level for existing characters
    db.execute("UPDATE characters SET class_levels = json_object(class_name, level) WHERE class_levels = '{}' OR class_levels IS NULL OR class_levels = ''")
    # Migration: character_relationships for History & Relationships tab
    db.execute("""
        CREATE TABLE IF NOT EXISTS character_relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            character_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            relationship_type TEXT DEFAULT 'ally',
            description TEXT DEFAULT '',
            prompt TEXT DEFAULT '',
            npc_data TEXT DEFAULT '{}',
            ai_generated INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    # ── Indexes for performance ──
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_char_spells_char ON character_spells(character_id)",
        "CREATE INDEX IF NOT EXISTS idx_characters_user ON characters(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_campaigns_user ON dm_campaigns(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_encounters_user ON dm_encounters(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_npcs_user ON dm_npcs(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_traps_user ON dm_custom_traps(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_relationships_character ON character_relationships(character_id)",
        "CREATE INDEX IF NOT EXISTS idx_relationships_user ON character_relationships(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)",
    ]:
        try:
            db.execute(idx_sql)
        except sqlite3.OperationalError as e:
            print(f"[index] Warning: {e}")
    db.commit()
    db.close()

    # ── Migration: add source columns to dm_npcs (manual ingestion) ──
    _migrate_npc_source_columns()


def _migrate_npc_source_columns():
    """Add source tracking columns to dm_npcs if they don't exist."""
    from main import get_db
    db = get_db()
    try:
        cols = {r[1] for r in db.execute("PRAGMA table_info(dm_npcs)")}
        for col, col_type in [("source", "TEXT DEFAULT ''"),
                              ("source_book", "TEXT DEFAULT ''"),
                              ("source_page", "TEXT DEFAULT ''")]:
            if col not in cols:
                db.execute(f"ALTER TABLE dm_npcs ADD COLUMN {col} {col_type}")
                print(f"[migration] Added dm_npcs.{col}")
        db.commit()
    except Exception as e:
        print(f"[migration] dm_npcs source columns: {e}")
    finally:
        db.close()

    # Migration: is_admin column on users
    db = get_db()
    try:
        db.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # Migration: combat_notes on characters
    try:
        db.execute("ALTER TABLE characters ADD COLUMN combat_notes TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass

    # Migration: shared flags
    for table, column in [("characters", "shared"), ("dm_encounters", "shared")]:
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

    # Existing installations keep their users; no default credentials are created.
    db.commit()
    db.close()
    from services.schema import validate_schema
    check_db = get_db()
    try:
        validate_schema(check_db, raise_on_error=True)
    finally:
        check_db.close()


__all__ = ["init_db", "_migrate_npc_source_columns"]