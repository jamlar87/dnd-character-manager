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
