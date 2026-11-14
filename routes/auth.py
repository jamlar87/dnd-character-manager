"""Auth routes — register, login, logout, password reset."""

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
import ipaddress
import math
import sqlite3
import os

from core.security import _verify, _get_user, _create_session, get_current_user
from core.http import _render
from services import login_guard
from services.sessions import invalidate_user_sessions
from services.users import create_user, update_password

router = APIRouter()

# Brute-force budget. Two independent windows gate a login: one per account
# (so password guessing against a single email is capped) and one per client
# address (so spraying many emails from one host is capped too). Constants, not
# literals, so tests can shrink the window.
LOGIN_WINDOW_S = 15 * 60
LOGIN_ACCOUNT_LIMIT = 5
LOGIN_IP_LIMIT = 20
REGISTER_WINDOW_S = 15 * 60
REGISTER_IP_LIMIT = 20


def _db_path():
    from core.config import DB_PATH
    return DB_PATH


def _drop_dead_session_cookie(request: Request, resp):
    """Delete a `dnd_token` cookie whose session no longer exists.

    A dead cookie is worse than no cookie: the security middleware sees a
    `dnd_token`, so it demands `x-csrf-token` on every POST — and a native login
    form cannot send that header. That combination locked people out of the
    login page itself until they cleared cookies by hand. Drop it on sight.
    """
    if request.cookies.get("dnd_token") and not get_current_user(request):
        resp.delete_cookie("dnd_token", path="/")
    return resp


def _is_local_peer(host: str) -> bool:
    """True when the socket peer is loopback/private, i.e. a proxy in front of us.

    In that case the peer address is the reverse proxy (Caddy, Cloudflare's
    tunnel connector), not the visitor, so the forwarded header is the only
    usable identity. Anything unparsable ("testclient", junk) is treated as a
    real remote peer and used verbatim.
    """
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private or addr.is_link_local


def _client_ip(request: Request) -> str:
    """Best-effort client address for the per-IP budget.

    Behind Cloudflare the socket peer is loopback/private, so the real visitor
    arrives in `cf-connecting-ip`. Direct connections use the peer address.
    Missing/unusable values coalesce to "unknown" rather than failing open with
    a per-empty-string bucket shared by everyone.
    """
    peer = (request.client.host if request.client else "") or ""
    forwarded = (request.headers.get("cf-connecting-ip") or "").split(",")[0].strip()
    if peer and _is_local_peer(peer):
        return forwarded or peer
    return peer or forwarded or "unknown"


def _retry_message(wait_s: float) -> str:
    minutes = max(1, math.ceil(wait_s / 60.0))
    return f"Too many attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}."


def _throttled(request: Request, template: str, wait_s: float) -> HTMLResponse:
    """The 429 page: same form, same template, explicit status and Retry-After."""
    resp = _drop_dead_session_cookie(
        request, _render(template, request=request, error=_retry_message(wait_s))
    )
    resp.status_code = 429
    resp.headers["Retry-After"] = str(max(1, math.ceil(wait_s)))
    return resp


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return _drop_dead_session_cookie(request, _render("login.html", request=request))


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    account = email.lower().strip()
    account_key = f"login:account:{account}"
    ip_key = f"login:ip:{_client_ip(request)}"

    # Both budgets are checked before any database or bcrypt work: a throttled
    # attempt must be cheaper than a real one, or the throttle is itself a lever.
    wait = max(
        login_guard.check(account_key, LOGIN_ACCOUNT_LIMIT, LOGIN_WINDOW_S),
        login_guard.check(ip_key, LOGIN_IP_LIMIT, LOGIN_WINDOW_S),
    )
    if wait > 0:
        return _throttled(request, "login.html", wait)

    user = _get_user(account)
    if not user or not _verify(password, user["password_hash"]):
        # Unrecorded account names count too: otherwise a 200-vs-429 difference
        # would tell an attacker which emails exist. The message stays generic.
        login_guard.record_failure(account_key, LOGIN_ACCOUNT_LIMIT, LOGIN_WINDOW_S)
        login_guard.record_failure(ip_key, LOGIN_IP_LIMIT, LOGIN_WINDOW_S)
        return _drop_dead_session_cookie(
            request,
            _render("login.html", request=request, error="Invalid email or password"),
        )
    # A real login wipes the slate for this account and this address, so a user
    # who mistyped a few times then got it right is not still on the edge.
    login_guard.clear(account_key)
    login_guard.clear(ip_key)
    token = _create_session(user["id"])
    resp = RedirectResponse("/dashboard", 303)
    resp.set_cookie("dnd_token", token, httponly=True, secure=os.environ.get("APP_ENV", "development").lower() in {"production", "prod"}, max_age=60 * 60 * 24 * 30, samesite="lax", path="/")
    return resp


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return _render("register.html", request=request)


@router.post("/register")
def register(request: Request, email: str = Form(...), password: str = Form(...)):
    # Registration is capped per client address only: every attempt counts
    # (success or rejection), because bulk account creation is the abuse mode
    # here, not password guessing. There is no per-account budget to spend.
    ip_key = f"register:ip:{_client_ip(request)}"
    wait = login_guard.check(ip_key, REGISTER_IP_LIMIT, REGISTER_WINDOW_S)
    if wait > 0:
        return _throttled(request, "register.html", wait)
    login_guard.record_failure(ip_key, REGISTER_IP_LIMIT, REGISTER_WINDOW_S)

    if email.lower().strip() == "admin":
        return _render("register.html", request=request, error="That email is unavailable")
    if len(password) < 6:
        return _render("register.html", request=request, error="Password must be at least 6 characters")
    try:
        user_id = create_user(_db_path(), email.lower().strip(), password)
        token = _create_session(user_id)
        resp = RedirectResponse("/dashboard", 303)
        resp.set_cookie("dnd_token", token, httponly=True, secure=os.environ.get("APP_ENV", "development").lower() in {"production", "prod"}, max_age=60 * 60 * 24 * 30, samesite="lax", path="/")
        return resp
    except sqlite3.IntegrityError:
        return _render("register.html", request=request, error="Email already registered")


@router.get("/logout")
def logout(request: Request):
    user = get_current_user(request)
    if user:
        invalidate_user_sessions(_db_path(), user["id"])
    resp = RedirectResponse("/", 303)
    resp.delete_cookie("dnd_token")
    return resp


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request):
    return _render("reset_password.html", request=request)


@router.get("/change-password", response_class=HTMLResponse)
def change_password_page(request: Request):
    if not get_current_user(request):
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return _render("change_password.html", request=request)


@router.post("/change-password", response_class=HTMLResponse)
def change_password(request: Request, current_password: str = Form(...), password: str = Form(...)):
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
def reset_password(request: Request, email: str = Form(...), password: str = Form(...)):
    # Password reset by email is intentionally disabled until a verified,
    # single-use, expiring token delivery flow is configured.
    return _render(
        "reset_password.html",
        request=request,
        email=email,
        error="Password reset is unavailable. Contact an administrator.",
    )
