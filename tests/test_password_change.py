"""Authenticated password-change regression tests."""


def test_authenticated_user_can_view_change_password_page(client, seeded_db, auth_headers):
    response = client.get("/change-password", headers=auth_headers)
    assert response.status_code == 200
    assert "Change Password" in response.text


def test_authenticated_user_can_change_password(client, seeded_db, auth_headers):
    response = client.post(
        "/change-password",
        data={"current_password": "testpass", "password": "stronger-password"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert "changed" in response.text.lower()

    from main import _get_user, _verify
    user = _get_user("test@test.com")
    assert _verify("stronger-password", user["password_hash"])


def test_password_change_requires_current_password(client, auth_headers):
    response = client.post(
        "/change-password",
        data={"current_password": "wrong", "password": "stronger-password"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert "incorrect" in response.text.lower()


def test_reset_page_does_not_offer_unauthenticated_password_form(client):
    response = client.get("/reset-password")
    assert response.status_code == 200
    assert "unavailable" in response.text.lower()
    assert 'name="password"' not in response.text


def test_password_change_invalidates_old_session(client, seeded_db, auth_headers):
    response = client.post(
        "/change-password",
        data={"current_password": "testpass", "password": "stronger-password"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    from main import _get_user_by_token
    assert _get_user_by_token(seeded_db["user_token"]) is None
