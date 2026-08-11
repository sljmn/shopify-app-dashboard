from app_dashboard.auth import (
    LOGIN_CSRF_MAX_AGE,
    SESSION_MAX_AGE,
    display_name,
    issue_login_csrf,
    issue_session,
    read_session,
    valid_login_csrf,
)


def test_session_round_trip_and_tampering():
    token = issue_session("s3cret", "ada@example.com", "Ada Lovelace")
    assert read_session("s3cret", token, "ada@example.com") == "ada@example.com"
    assert read_session("different-secret", token, "ada@example.com") is None
    assert read_session("s3cret", token + "x", "ada@example.com") is None
    assert read_session("s3cret", None, "ada@example.com") is None


def test_changing_the_configured_username_revokes_existing_sessions():
    token = issue_session("s3cret", "ada@example.com")
    assert read_session("s3cret", token, "ada@example.com")
    assert read_session("s3cret", token, "grace@example.com") is None


def test_session_lasts_thirty_days():
    assert SESSION_MAX_AGE == 60 * 60 * 24 * 30


def test_login_csrf_round_trip_and_tampering():
    token = issue_login_csrf("s3cret")
    assert valid_login_csrf("s3cret", token, token)
    assert not valid_login_csrf("s3cret", token + "x", token)
    assert not valid_login_csrf("s3cret", token, token + "x")
    assert not valid_login_csrf("different-secret", token, token)
    assert not valid_login_csrf("s3cret", None, token)
    assert LOGIN_CSRF_MAX_AGE == 60 * 10


def test_display_name_is_cosmetic_and_never_an_authorization_input():
    named = issue_session("s3cret", "ada@example.com", "Ada Lovelace")
    assert display_name("s3cret", named, "ada@example.com") == "Ada Lovelace"

    unnamed = issue_session("s3cret", "ada@example.com")
    assert display_name("s3cret", unnamed, "ada@example.com") == "ada"

    forged = issue_session("attacker-secret", "ada@example.com", "Ada Lovelace")
    assert display_name("s3cret", forged, "real@example.com") == "real"
    assert read_session("s3cret", forged, "ada@example.com") is None
