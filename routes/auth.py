"""Auth routes — register, login, logout, password reset."""

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
import sqlite3

from main import get_db, _hash, _verify, _get_user, _create_session, _render, get_current_user

router = APIRouter()


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
    resp.set_cookie("dnd_token", token, httponly=True, max_age=60 * 60 * 24 * 30, samesite="lax")
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
    db = get_db()
    try:
        db.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)",
                   (email.lower().strip(), _hash(password)))
        db.commit()
        user = _get_user(email.lower().strip())
        token = _create_session(user["id"])
        resp = RedirectResponse("/dashboard", 303)
        resp.set_cookie("dnd_token", token, httponly=True, max_age=60 * 60 * 24 * 30, samesite="lax")
        return resp
    except sqlite3.IntegrityError:
        return _render("register.html", request=request, error="Email already registered")
    finally:
        db.close()


@router.get("/logout")
async def logout(request: Request):
    user = get_current_user(request)
    if user:
        db = get_db()
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
        db.commit()
        db.close()
    resp = RedirectResponse("/", 303)
    resp.delete_cookie("dnd_token")
    return resp


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request):
    return _render("reset_password.html", request=request)


@router.post("/reset-password")
async def reset_password(request: Request, email: str = Form(...), password: str = Form(...)):
    if len(password) < 6:
        return _render("reset_password.html", request=request,
                       email=email, error="Password must be at least 6 characters")
    user = _get_user(email.lower().strip())
    if not user:
        return _render("reset_password.html", request=request,
                       email=email, error="No account found with that email")
    db = get_db()
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
               (_hash(password), user["id"]))
    db.commit()
    db.close()
    db = get_db()
    db.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    db.commit()
    db.close()
    return _render("reset_password.html", request=request,
                   success="Password reset! You can now log in.")
