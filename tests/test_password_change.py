"""Authenticated password-change regression tests."""


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
