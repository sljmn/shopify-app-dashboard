from datetime import date
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Credentials that appear in this repository's own documentation. Refused
# outright: Basic auth bypasses the SSO allowlist, so one of these is a full
# account on a public-facing deployment.
PUBLISHED_CREDENTIALS = {"admin:change-me", "user:pass", "u:p", "admin:admin"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- required: nothing here has a safe default -------------------------
    database_url: str
    # "user:pass,user2:pass2" -- one credential pair per dashboard user
    dashboard_users: str
    # Redirect URIs must match Google's registration byte for byte, so this is
    # configured rather than derived from the request: behind a TLS-terminating
    # proxy the request scheme can read as http and silently break the callback.
    # Required, and deliberately without a default: a default here would point
    # every deployment at whoever published it.
    public_base_url: str
    # Comma-separated domains and individual addresses. Enforced by us, not by
    # Google: the OAuth client is External, so Google authenticates any account
    # and this is the gate. Required for the same reason as public_base_url --
    # inheriting somebody else's allowlist is a standing back door.
    google_allowed_domains: str

    # Versioned catalog of Partner organizations and apps. App identity,
    # Partner credentials, pricing, usage, and GA4 all live there.
    apps_config_path: str = "config/apps.yml"

    # --- optional integrations ---------------------------------------------
    slack_webhook_url: str | None = None
    google_client_id: str | None = None
    google_client_secret: str | None = None
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

    # --- validation ---------------------------------------------------------
    # These run at construction, which for this app means at import, because
    # web.py builds the ASGI app at module level. So a bad value is a process
    # that refuses to start rather than a dashboard that quietly reports the
    # wrong number hours later.

    @field_validator("dashboard_users")
    @classmethod
    def _every_pair_has_a_colon(cls, raw: str) -> str:
        """Catch a password containing a comma.

        The format is "user:pass,user2:pass2", so a comma inside a password
        silently truncates it: "admin:pa,ssword" parses as {"admin": "pa"} and
        logging in with "pa" succeeds. An operator who generated a random
        password would get a two-character one and never know. A fragment with
        no colon in it is that accident, every time.
        """
        for part in (p.strip() for p in raw.split(",")):
            if part and ":" not in part:
                raise ValueError(
                    f"DASHBOARD_USERS has a fragment with no colon: {part!r}. "
                    "Entries are user:pass separated by commas, so a password "
                    "containing a comma is silently truncated. Generate one "
                    "without: python -c \"import secrets; "
                    "print(secrets.token_urlsafe(24))\""
                )
        # Basic auth bypasses the Google domain allowlist by design, so a
        # published placeholder here is a full account. This repository is
        # public, which makes any example credential the first thing anyone
        # tries against a deployment.
        if any(p.strip() in PUBLISHED_CREDENTIALS for p in raw.split(",")):
            raise ValueError(
                "DASHBOARD_USERS is still an example credential from this "
                "repository. It grants full access and bypasses "
                "GOOGLE_ALLOWED_DOMAINS. Generate one: python -c \"import "
                "secrets; print(secrets.token_urlsafe(24))\""
            )
        return raw

    # --- derived ------------------------------------------------------------

    @property
    def dashboard_users_map(self) -> dict[str, str]:
        pairs = (p.split(":", 1) for p in self.dashboard_users.split(",") if ":" in p)
        return {u.strip(): pw for u, pw in pairs}

    @field_validator("digest_timezone")
    @classmethod
    def _timezone_is_named(cls, raw: str) -> str:
        return raw.strip() or "UTC"

    @property
    def dashboard_name(self) -> str:
        return "Shopify Apps Analytics"

    @property
    def slug(self) -> str:
        return "shopify-apps"


@lru_cache
def get_settings() -> Settings:
    return Settings()
