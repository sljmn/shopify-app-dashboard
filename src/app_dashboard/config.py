from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Credentials that appear in this public repository are never valid deployment
# secrets. Refusing them turns a copied example into a startup error.
PUBLISHED_USERNAMES = {"admin", "user", "u", "you@example.com"}
PUBLISHED_PASSWORDS = {"change-me", "pass", "p", "admin"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- required: nothing here has a safe default -------------------------
    database_url: str
    dashboard_username: str
    dashboard_password: str
    # Used for links, origin checks, and deciding whether cookies require HTTPS.
    # Required without a default so a deployment cannot inherit the publisher's
    # hostname.
    public_base_url: str

    # Versioned catalog of Partner organizations and apps. App identity,
    # Partner credentials, pricing, usage, and GA4 all live there.
    apps_config_path: str = "config/apps.yml"

    # --- optional integrations ---------------------------------------------
    slack_webhook_url: str | None = None
    # Signs the session cookie. Rotating it logs everyone out. create_app
    # refuses to serve a non-local deployment while this is the published
    # default, so leaving it alone is a startup failure, not a silent weakness.
    session_secret: str = "dev-only-not-a-secret"
    # The day your GA4 property started collecting. Backfills clamp to it, so a
    # date earlier than the real one just asks GA4 for empty days.
    ga4_earliest_data: date = date(2020, 1, 1)

    # --- operational -------------------------------------------------------
    poll_interval_minutes: int = 15
    poll_overlap_minutes: int = 60
    # Weekly Slack digest, as a cron day-of-week and hour in this timezone.
    digest_day_of_week: str = "mon"
    digest_hour: int = 9
    # Empty would fall through to the scheduler's system-local timezone, so the
    # digest would fire at an hour nobody chose. Normalised in the validator.
    digest_timezone: str = "UTC"
    # The header your proxy puts the real client address in. Rate limiting keys
    # on it. PREFER A SINGLE-VALUE HEADER your proxy overwrites: Fly-Client-IP,
    # CF-Connecting-IP, X-Real-IP. X-Forwarded-For works but is a list that
    # proxies append to, so only the rightmost entry is trustworthy, which is
    # what client_key reads. Empty means trust the socket peer, which is right
    # only with no proxy in front.
    trusted_client_ip_header: str = ""
    # No annotation may be dated before this. Set it to roughly when your app
    # launched; a chart marker dated 1970 is a typo, not history.
    annotations_earliest: date = date(2020, 1, 1)
    # Shopify made the uninstall reason question mandatory during 2026, so
    # coverage before and after is not comparable and reports say so. Verify
    # against your own feed before trusting the boundary.
    reason_mandatory_from: date = date(2026, 4, 29)
    # Public competitor media is content-addressed on a persistent volume.
    watchlist_media_path: Path = Path("/data/mantle-watchlist")
    watchlist_concurrency: int = 2

    # --- validation ---------------------------------------------------------
    # These run at construction, which for this app means at import, because
    # web.py builds the ASGI app at module level. So a bad value is a process
    # that refuses to start rather than a dashboard that quietly reports the
    # wrong number hours later.

    @field_validator("dashboard_username")
    @classmethod
    def _username_is_private(cls, raw: str) -> str:
        value = raw.strip()
        if not value or value.lower() in PUBLISHED_USERNAMES:
            raise ValueError("DASHBOARD_USERNAME must be set to a private value")
        return value

    @field_validator("dashboard_password")
    @classmethod
    def _password_is_private(cls, raw: str) -> str:
        if not raw or raw in PUBLISHED_PASSWORDS:
            raise ValueError("DASHBOARD_PASSWORD must be set to a private value")
        return raw

    @field_validator("digest_timezone")
    @classmethod
    def _timezone_is_named(cls, raw: str) -> str:
        return raw.strip() or "UTC"

    @field_validator("watchlist_media_path")
    @classmethod
    def _watchlist_path_is_absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("WATCHLIST_MEDIA_PATH must be absolute")
        return value

    @field_validator("watchlist_concurrency")
    @classmethod
    def _watchlist_concurrency_is_bounded(cls, value: int) -> int:
        if not 1 <= value <= 4:
            raise ValueError("WATCHLIST_CONCURRENCY must be between 1 and 4")
        return value

    @property
    def dashboard_name(self) -> str:
        return "Shopify Apps Analytics"

    @property
    def slug(self) -> str:
        return "shopify-apps"


@lru_cache
def get_settings() -> Settings:
    return Settings()
