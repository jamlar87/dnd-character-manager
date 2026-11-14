"""Password hashing, cookie sessions and per-row ownership checks."""
from __future__ import annotations

from fastapi import HTTPException, Request

from core import config  # DB_PATH / SESSION_TTL_DAYS are read live (tests repoint them)

# Compatibility wrappers; implementation lives in services.auth.
def _hash(password: str) -> str:
    from services.auth import hash_password
    return hash_password(password)

def _verify(password: str, hash_: str) -> bool:
    from services.auth import verify_password
    return verify_password(password, hash_)


def _get_user(email: str) -> dict | None:
    from services.users import get_user
    return get_user(config.DB_PATH, email)

def _create_session(user_id: int) -> str:
    from services.sessions import create_session
    return create_session(config.DB_PATH, user_id, config.SESSION_TTL_DAYS)

def _get_user_by_token(token: str) -> dict | None:
    from services.sessions import get_user_by_token
    return get_user_by_token(config.DB_PATH, token)

def get_current_user(request: Request) -> dict | None:
    token = request.cookies.get("dnd_token")
    if not token:
        return None
    return _get_user_by_token(token)

def require_user(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user

def _is_admin(user: dict) -> bool:
    from services.users import is_admin
    return is_admin(user)

def _user_filter(user: dict, column: str = "user_id") -> tuple[str, tuple]:
    """Return (sql_clause, params) that filters by user_id, or empty if admin."""
    if _is_admin(user):
        return ("", ())
    return (f"AND {column} = ?", (user["id"],))

def _user_where(user: dict, column: str = "user_id") -> tuple[str, tuple]:
    """Return (WHERE_clause, params) that filters by user_id, or empty if admin."""
    if _is_admin(user):
        return ("", ())
    return (f"WHERE {column} = ?", (user["id"],))

def _user_dms_character(db, user: dict, char_id: int) -> bool:
    """True when `user` DM-owns a campaign that lists this character."""
    from services.campaign_access import user_dms_character
    return user_dms_character(db, user, char_id)


def _require_owned(db, user: dict, table: str, item_id: int, id_col: str = "id") -> dict | None:
    """Fetch a row by id, checking ownership unless user is admin. Returns dict or None.

    Raises HTTPException(404) implicitly if not found, but callers typically
    check for None to return their own 404. Admin bypasses user_id check.
    """
    if _is_admin(user):
        row = db.execute(f"SELECT * FROM {table} WHERE {id_col} = ?", (item_id,)).fetchone()
    else:
        row = db.execute(f"SELECT * FROM {table} WHERE {id_col} = ? AND user_id = ?", (item_id, user["id"])).fetchone()
    return dict(row) if row else None
