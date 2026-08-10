import os
from datetime import date

import pytest
from pydantic import ValidationError

from app_dashboard.config import Settings, get_settings

def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("DASHBOARD_USERS", "ada:pw,grace:pw2")
    s = Settings()
    assert s.database_url == "postgresql://x"
    assert s.poll_interval_minutes == 15
    assert s.poll_overlap_minutes == 60
    assert s.apps_config_path == "config/apps.yml"
    assert s.dashboard_users_map == {"ada": "pw", "grace": "pw2"}


# --- settings that had no test, so a regression to a hardcoded constant would
# --- have been invisible to the whole suite.

def _settings(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    return get_settings()


def test_a_password_containing_a_comma_is_refused(monkeypatch):
    """"admin:pa,ssword" parsed as {"admin": "pa"} and logging in with "pa"
    worked. An operator who generated a random password got a 2-character one."""
    with pytest.raises(ValidationError):
        _settings(monkeypatch, DASHBOARD_USERS="admin:pa,ssword")


def test_date_settings_are_read_not_hardcoded(monkeypatch):
    s = _settings(monkeypatch, ANNOTATIONS_EARLIEST="2015-06-01",
                  REASON_MANDATORY_FROM="2027-01-01", GA4_EARLIEST_DATA="2019-03-04")
    assert s.annotations_earliest == date(2015, 6, 1)
    assert s.reason_mandatory_from == date(2027, 1, 1)
    assert s.ga4_earliest_data == date(2019, 3, 4)


def test_an_empty_digest_timezone_becomes_utc(monkeypatch):
    """Empty is falsy, so APScheduler would fall back to the machine's local
    timezone and the digest would fire at an hour nobody chose."""
    assert _settings(monkeypatch, DIGEST_TIMEZONE="").digest_timezone == "UTC"


def test_a_published_example_credential_is_refused(monkeypatch):
    """Basic auth bypasses GOOGLE_ALLOWED_DOMAINS, and this repository is
    public, so any credential appearing in its documentation is the first thing
    anyone tries against a deployment."""
    for bad in ("admin:change-me", "user:pass", "admin:admin",
                "real:secret,admin:change-me"):
        with pytest.raises(ValidationError):
            _settings(monkeypatch, DASHBOARD_USERS=bad)
