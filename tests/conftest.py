"""Shared fixtures for D&D Character Manager tests.

main.py does heavy init at import time (loads JSON, builds indexes).
We import it once at module scope so all tests share the initialized state.
"""

import os, sys, json, sqlite3, tempfile, pytest
from pathlib import Path

# Point data dir to a test copy so we don't pollute the live DB
HERE = Path(__file__).parent.parent
os.environ.setdefault("DND_DATA_DIR", str(HERE / "data"))

# Import main — triggers all the SRD loading, data enrichment, etc.
# This takes ~0.5s and runs once per test session.
import main as app_module
from main import app, get_db, init_db, parse_class_levels, CLASSES, RACES
from main import SUBCLASS_FEATURES, LIMITED_USE, FEAT_BY_NAME

from fastapi.testclient import TestClient


@pytest.fixture(scope="function")
def test_db():
    """Create a temporary test database with schema initialized.
    Yields (db_path, get_db function) for each test."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    db_path = Path(db_path)
    
    # Monkey-patch the app's DB_PATH to use our temp file
    old_db_path = app_module.DB_PATH
    app_module.DB_PATH = db_path
    
    # Init schema
    init_db()
    
    yield db_path
    
    # Cleanup
    app_module.DB_PATH = old_db_path
    db_path.unlink(missing_ok=True)


@pytest.fixture
def client():
    """FastAPI TestClient bound to the app."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def seeded_db(test_db):
    """Return a test_db with a seeded user + session token."""
    db_path = test_db
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    
    # Create test user
    from main import _hash
    con.execute(
        "INSERT INTO users (email, password_hash, is_admin) VALUES (?, ?, ?)",
        ("test@test.com", _hash("testpass"), 0)
    )
    con.execute(
        "INSERT INTO users (email, password_hash, is_admin) VALUES (?, ?, ?)",
        ("admin@test.com", _hash("admin"), 1)
    )
    
    # Create sessions
    import secrets
    user_token = secrets.token_hex(32)
    admin_token = secrets.token_hex(32)
    con.execute("INSERT INTO sessions (user_id, token) VALUES (?, ?)",
                (1, user_token))
    con.execute("INSERT INTO sessions (user_id, token) VALUES (?, ?)",
                (2, admin_token))
    con.commit()
    con.close()
    
    return {"db_path": db_path, "user_token": user_token, "admin_token": admin_token}


@pytest.fixture
def auth_headers(seeded_db):
    """Headers with auth cookie for a normal user."""
    return {"Cookie": f"dnd_token={seeded_db['user_token']}"}


@pytest.fixture
def admin_headers(seeded_db):
    """Headers with auth cookie for an admin user."""
    return {"Cookie": f"dnd_token={seeded_db['admin_token']}"}
