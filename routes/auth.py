"""Auth routes — register, login, logout, password reset."""

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
import sqlite3
import os

from main import _verify, _get_user, _create_session, _render, get_current_user
from services.sessions import invalidate_user_sessions
from services.users import create_user, update_password

router = APIRouter()


def _db_path():
    import main
    return main.DB_PATH


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return _render("login.html", request=request)


@router.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    user = _get_user(email.lower().strip())
    if not user or not _verify(password, user["password_hash"]):
        return _render("login.html", request=request, error="Invalid email or password")
    token = _create_session(user["id"])
    resp = RedirectResponse("/dashboard", 303)
    resp.set_cookie("dnd_token", token, httponly=True, secure=os.environ.get("APP_ENV", "development").lower() in {"production", "prod"}, max_age=60 * 60 * 24 * 30, samesite="lax", path="/")
    return resp


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return _render("register.html", request=request)


@router.post("/register")
async def register(request: Request, email: str = Form(...), password: str = Form(...)):
    if email.lower().strip() == "admin":
        return _render("register.html", request=request, error="That email is unavailable")
    if len(password) < 6:
        return _render("register.html", request=request, error="Password must be at least 6 characters")
    try:
        user_id = create_user(_db_path(), email.lower().strip(), password)
        user = _get_user(email.lower().strip())
        token = _create_session(user_id)
        resp = RedirectResponse("/dashboard", 303)
        resp.set_cookie("dnd_token", token, httponly=True, secure=os.environ.get("APP_ENV", "development").lower() in {"production", "prod"}, max_age=60 * 60 * 24 * 30, samesite="lax", path="/")
        return resp
    except sqlite3.IntegrityError:
        return _render("register.html", request=request, error="Email already registered")


@router.get("/logout")
async def logout(request: Request):
    user = get_current_user(request)
    if user:
        invalidate_user_sessions(_db_path(), user["id"])
    resp = RedirectResponse("/", 303)
    resp.delete_cookie("dnd_token")
    return resp


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request):
    return _render("reset_password.html", request=request)


@router.get("/change-password", response_class=HTMLResponse)
async def change_password_page(request: Request):
    if not get_current_user(request):
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return _render("change_password.html", request=request)


@router.post("/change-password", response_class=HTMLResponse)
async def change_password(request: Request, current_password: str = Form(...), password: str = Form(...)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    if len(password) < 6:
        return _render("change_password.html", request=request, error="Password must be at least 6 characters")
    if not _verify(current_password, user["password_hash"]):
        return _render("change_password.html", request=request, error="Current password is incorrect")
    update_password(_db_path(), user["id"], password)
    invalidate_user_sessions(_db_path(), user["id"])
    return _render("change_password.html", request=request, success="Password changed. Please log in again.")


@router.post("/reset-password")
async def reset_password(request: Request, email: str = Form(...), password: str = Form(...)):
    # Password reset by email is intentionally disabled until a verified,
    # single-use, expiring token delivery flow is configured.
    return _render(
        "reset_password.html",
        request=request,
        email=email,
        error="Password reset is unavailable. Contact an administrator.",
    )
