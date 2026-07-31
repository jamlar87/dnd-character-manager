"""Regression tests for auth route behavior during service extraction."""


def test_change_password_renders_change_password_template_on_validation_error(client, auth_headers):
    response = client.post(
        "/change-password",
        data={"current_password": "wrong", "password": "x"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert "Change Password" in response.text


def test_change_password_renders_change_password_template_on_success(client, auth_headers):
    response = client.post(
        "/change-password",
        data={"current_password": "testpass", "password": "stronger-password"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert "Change Password" in response.text
    assert "changed" in response.text.lower()
