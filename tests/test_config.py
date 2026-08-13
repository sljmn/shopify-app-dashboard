from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from app_dashboard.config import Settings, get_settings

def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("DASHBOARD_USERNAME", "ada@example.com")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "private-password")
    s = Settings()
    assert s.database_url == "postgresql://x"
    assert s.poll_interval_minutes == 15
    assert s.poll_overlap_minutes == 60
    assert s.apps_config_path == "config/apps.yml"
    assert s.dashboard_username == "ada@example.com"
    assert s.dashboard_password == "private-password"


# --- settings that had no test, so a regression to a hardcoded constant would
# --- have been invisible to the whole suite.

def _settings(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    return get_settings()


@pytest.mark.parametrize("name", ["DASHBOARD_USERNAME", "DASHBOARD_PASSWORD"])
def test_blank_dashboard_credentials_are_refused(monkeypatch, name):
    with pytest.raises(ValidationError):
        _settings(monkeypatch, **{name: ""})


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
    for name, bad in (("DASHBOARD_USERNAME", "admin"),
                      ("DASHBOARD_PASSWORD", "change-me")):
        with pytest.raises(ValidationError):
            _settings(monkeypatch, **{name: bad})


def test_watchlist_storage_and_concurrency_are_validated(monkeypatch):
    settings = _settings(
        monkeypatch,
        WATCHLIST_MEDIA_PATH="/tmp/mantle-watchlist",
        WATCHLIST_CONCURRENCY="4",
    )
    assert settings.watchlist_media_path == Path("/tmp/mantle-watchlist")
    assert settings.watchlist_concurrency == 4
    with pytest.raises(ValidationError):
        _settings(monkeypatch, WATCHLIST_MEDIA_PATH="relative/path")
    with pytest.raises(ValidationError):
        _settings(monkeypatch, WATCHLIST_CONCURRENCY="5")


def test_backblaze_configuration_must_be_complete(monkeypatch):
    with pytest.raises(ValidationError, match="must be set together"):
        _settings(monkeypatch, B2_BUCKET="only-a-bucket")


def test_wordpress_configuration_must_be_complete(monkeypatch):
    with pytest.raises(ValidationError, match="WORDPRESS_SITE_URL"):
        _settings(monkeypatch, WORDPRESS_SITE_URL="https://newcraft.dev")
