"""CSRF regression tests."""


def test_unsafe_cookie_request_requires_csrf_token(client, auth_headers):
    response = client.post(
        "/api/dm/encounter/create",
        json={"name": "csrf-test"},
        headers={"Cookie": auth_headers["Cookie"]},
    )
    assert response.status_code == 403
    assert "csrf" in response.json()["error"].lower()


def test_matching_csrf_cookie_and_header_allows_request(client, auth_headers):
    client.get("/", headers=auth_headers)
    token = client.cookies.get("csrf_token")
    assert token
    response = client.post(
        "/api/dm/encounter/create",
        json={"name": "csrf-valid"},
        headers={"Cookie": f"{auth_headers['Cookie']}; csrf_token={token}", "X-CSRF-Token": token},
    )
    assert response.status_code == 200


def test_origin_mismatch_still_rejected_with_valid_csrf(client, auth_headers):
    client.get("/", headers=auth_headers)
    token = client.cookies.get("csrf_token")
    response = client.post(
        "/api/dm/encounter/create",
        json={"name": "csrf-origin"},
        headers={"Cookie": f"{auth_headers['Cookie']}; csrf_token={token}", "X-CSRF-Token": token, "Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_change_password_form_post_is_not_csrf_blocked(client, auth_headers):
    """The change-password page is a native <form method="post">, and a browser cannot attach
    an X-CSRF-Token header to a native submit. If the path is not exempt, every logged-in user
    gets 403 and the page is unusable — this was the real behaviour before the fix.

    A wrong current_password is used deliberately: it exercises the whole route (session
    lookup, form parse, password check) and renders the error page with 200, so a 403 here can
    only come from the CSRF middleware.
    """
    response = client.post(
        "/change-password",
        data={"current_password": "definitely-wrong", "password": "newpassword123"},
        headers={"Cookie": auth_headers["Cookie"]},
        follow_redirects=False,
    )
    assert response.status_code != 403, f"CSRF middleware blocked a native form post: {response.text[:200]}"


def test_change_password_still_requires_a_session(client):
    """The exemption must not turn the route into an open write path: cookie-less callers are
    redirected to login by require_user, never served the form."""
    response = client.post(
        "/change-password",
        data={"current_password": "x", "password": "newpassword123"},
        follow_redirects=False,
    )
    assert response.status_code in (303, 302, 401, 403)
