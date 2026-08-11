"""Signed dashboard sessions and login-form CSRF tokens."""

import logging
import secrets
import time

from itsdangerous import BadSignature, URLSafeTimedSerializer

logger = logging.getLogger(__name__)

SESSION_COOKIE = "dashboard_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30
LOGIN_CSRF_COOKIE = "dashboard_login_csrf"
LOGIN_CSRF_MAX_AGE = 60 * 10


def serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt="dashboard-session")


def issue_session(secret: str, email: str, name: str | None = None) -> str:
    payload = {"email": email, "at": int(time.time())}
    if name:
        payload["name"] = name
    return serializer(secret).dumps(payload)


def _load(secret: str, token: str | None) -> dict | None:
    if not token:
        return None
    try:
        return serializer(secret).loads(token, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return None
    except Exception:
        logger.info("rejecting unreadable session cookie")
        return None


def read_session(secret: str, token: str | None,
                 configured_username: str) -> str | None:
    """Return the signed-in username if it still matches server config."""
    data = _load(secret, token)
    if data is None:
        return None
    email = data.get("email")
    if not isinstance(email, str):
        return None
    return email if secrets.compare_digest(
        email.encode("utf-8"), configured_username.encode("utf-8")
    ) else None


def display_name(secret: str, token: str | None, fallback: str) -> str:
    data = _load(secret, token) or {}
    return data.get("name") or fallback.split("@", 1)[0]


def _csrf_serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt="dashboard-login-csrf")


def issue_login_csrf(secret: str) -> str:
    return _csrf_serializer(secret).dumps({"nonce": secrets.token_urlsafe(24)})


def valid_login_csrf(secret: str, form_token: str | None,
                     cookie_token: str | None) -> bool:
    if not form_token or not cookie_token:
        return False
    if not secrets.compare_digest(
        form_token.encode("utf-8"), cookie_token.encode("utf-8")
    ):
        return False
    try:
        _csrf_serializer(secret).loads(form_token, max_age=LOGIN_CSRF_MAX_AGE)
        return True
    except BadSignature:
        return False
