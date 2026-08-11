# Password Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Google OAuth and HTTP Basic with one configurable email/password login backed by a 30-day signed session.

**Architecture:** `Settings` owns the single deployment credential. `auth.py` owns signed session and login-CSRF tokens, while `web.py` owns the form routes, rate limiting, redirects, and cookie lifecycle. Existing route dependencies continue to authorize through the same session cookie, so dashboard code outside authentication remains unchanged.

**Tech Stack:** Python 3.13, FastAPI/Starlette, Jinja2, Pydantic Settings, itsdangerous, pytest

---

### Task 1: Single-account configuration

**Files:**
- Modify: `src/app_dashboard/config.py`
- Modify: `.env.example`
- Modify: `tests/conftest.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Replace the config tests with failing single-account tests**

Assert that `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD` load as plain strings,
that a blank value is rejected, and that published examples such as
`change-me` are rejected.

- [ ] **Step 2: Run the config tests and verify they fail**

Run: `uv run pytest tests/test_config.py -q`

Expected: failures because `Settings` still requires `DASHBOARD_USERS` and
exposes `dashboard_users_map`.

- [ ] **Step 3: Implement the single-account settings**

Replace `dashboard_users` and its parser with:

```python
dashboard_username: str
dashboard_password: str

@field_validator("dashboard_username", "dashboard_password")
@classmethod
def _credential_is_usable(cls, raw: str, info) -> str:
    value = raw.strip() if info.field_name == "dashboard_username" else raw
    if not value or value in PUBLISHED_CREDENTIALS:
        raise ValueError(f"{info.field_name.upper()} must be set to a private value")
    return value
```

Use separate published username/password sets so a real email address is not
rejected merely because an example password exists. Update fixtures and sample
environment variables to use `tester@example.com` and the existing suite-only
password.

- [ ] **Step 4: Run affected tests**

Run: `uv run pytest tests/test_config.py tests/test_pipeline.py -q`

Expected: all pass.

### Task 2: Session and login-CSRF primitives

**Files:**
- Modify: `src/app_dashboard/auth.py`
- Modify: `tests/test_auth.py`

- [ ] **Step 1: Write failing tests for the new token contract**

Cover a session accepted only when its email equals the configured username,
rejection after the configured username changes, expiration through the
serializer, a 30-day `SESSION_MAX_AGE`, and login-CSRF issue/verification.

- [ ] **Step 2: Run the auth tests and verify they fail**

Run: `uv run pytest tests/test_auth.py -q`

Expected: imports or assertions fail because OAuth helpers still exist and
login-CSRF helpers do not.

- [ ] **Step 3: Reduce `auth.py` to password-session responsibilities**

Keep `issue_session`, `read_session`, and `display_name`; change
`SESSION_MAX_AGE` to `60 * 60 * 24 * 30`; validate `read_session` against one
configured username. Add a separate serializer salt for login CSRF:

```python
LOGIN_CSRF_COOKIE = "dashboard_login_csrf"
LOGIN_CSRF_MAX_AGE = 60 * 10

def issue_login_csrf(secret: str) -> str:
    return URLSafeTimedSerializer(secret, salt="dashboard-login-csrf").dumps(
        {"nonce": secrets.token_urlsafe(24)}
    )

def valid_login_csrf(secret: str, form_token: str | None,
                     cookie_token: str | None) -> bool:
    if not form_token or not cookie_token or not secrets.compare_digest(
        form_token.encode(), cookie_token.encode()
    ):
        return False
    try:
        URLSafeTimedSerializer(secret, salt="dashboard-login-csrf").loads(
            form_token, max_age=LOGIN_CSRF_MAX_AGE
        )
        return True
    except BadSignature:
        return False
```

Delete Google URLs, allowlist parsing, OAuth state, authorization URL, and token
exchange code.

- [ ] **Step 4: Run auth tests**

Run: `uv run pytest tests/test_auth.py -q`

Expected: all pass.

### Task 3: Form login and route protection

**Files:**
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/login.html`
- Modify: `src/app_dashboard/templates/gate.html`
- Modify: `src/app_dashboard/templates/base.html`
- Modify: `tests/test_web.py`
- Modify: `tests/test_security.py`

- [ ] **Step 1: Replace Basic/OAuth web tests with failing form tests**

Tests obtain the login page, extract `csrf_token`, retain its cookie, then POST
URL-encoded `username`, `password`, and `csrf_token`. Cover success, generic
failure, missing/invalid CSRF, limiter behavior, 30-day cookie, redirect of
anonymous pages, logout, and 404s for removed Google routes. Replace direct
Basic-auth requests in unrelated tests with a helper that installs a valid
session cookie.

- [ ] **Step 2: Run focused web/security tests and verify they fail**

Run: `uv run pytest tests/test_web.py tests/test_security.py -q`

Expected: new login POST and CSRF assertions fail.

- [ ] **Step 3: Implement the form flow**

Remove `HTTPBasic`, Google imports, `sso_enabled`, and OAuth routes. Make
`verify_creds` read only the session cookie and redirect browser page requests
to `/auth/login`; API-style requests receive a plain 401 without
`WWW-Authenticate`.

`GET /auth/login` issues a login-CSRF token, renders it as a hidden input, and
sets the matching `HttpOnly`, `SameSite=Lax` cookie. `POST /auth/login` uses the
existing capped URL-encoded parser, verifies CSRF, applies the existing rate
limiter, constant-time compares both credential fields, and on success sets the
30-day session cookie and deletes the CSRF cookie. On failure it re-renders the
form with `Incorrect email or password` and HTTP 401.

Update `login.html` to contain labeled email/password inputs and one submit
button. Keep the current unaffiliated disclosure and background artwork. Point
logout to the session-clearing route and remove copy that mentions Google or
Basic Auth.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/test_web.py tests/test_security.py -q`

Expected: all pass.

### Task 4: Documentation, full verification, and deployment

**Files:**
- Modify: `README.md`
- Modify: `docs/configuration.md`
- Modify: `docs/deploy.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Update operator documentation**

Replace `DASHBOARD_USERS`, Google OAuth, and allowed-domain instructions with
`DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD`. Document that changing the
username invalidates sessions and rotating `SESSION_SECRET` logs everyone out.

- [ ] **Step 2: Prove old auth references are gone**

Run:

```bash
rg -n "DASHBOARD_USERS|GOOGLE_CLIENT|GOOGLE_ALLOWED|auth/google|HTTP Basic" \
  src tests README.md docs .env.example
```

Expected: no product/docs references outside historical design plans.

- [ ] **Step 3: Run the complete suite**

Run: `uv run pytest -q`

Expected: all pass.

- [ ] **Step 4: Commit the implementation**

```bash
git add src tests .env.example README.md docs
git commit -m "Replace dashboard SSO with shared password login"
```

- [ ] **Step 5: Configure and deploy to Dokku**

Keep the current generated production password until the owner supplies the new
one, but change its username to `sulejman@newcraft.dev`. Remove obsolete Google
and `DASHBOARD_USERS` variables, deploy `HEAD` to the `mantle` app, and smoke
test login, dashboard access, protected redirects, and logout over HTTPS.
