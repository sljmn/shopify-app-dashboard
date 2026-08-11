# Password Login Design

## Goal

Replace Google OAuth and browser HTTP Basic authentication with one normal
email-and-password login for the dashboard. There is no registration, password
reset, user management, or role system. Everyone who is given the shared
credentials uses the same account.

The configured username will be `sulejman@newcraft.dev`. The password remains
a deployment secret and can be changed without changing application code.

## Configuration

The application reads one `DASHBOARD_USERNAME` and one `DASHBOARD_PASSWORD`
from environment variables. Both are required. Startup validation rejects
missing values and the published example password.

The credentials stay in Dokku's configuration alongside the application's
other deployment secrets. They are never rendered into HTML, logged, or stored
in the database.

Google OAuth settings, routes, and implementation code are removed. The
`DASHBOARD_USERS` multi-user Basic Auth setting is also removed rather than
maintaining parallel authentication paths.

## Authentication Flow

An unauthenticated browser request redirects to `GET /auth/login`. The page
contains only an email field, password field, and submit button. There is no
signup or Google button.

`POST /auth/login` compares both submitted values with the configured values
using constant-time comparisons. A successful login resets the IP login limiter,
issues the existing signed dashboard session cookie, and redirects to the
dashboard. A failed login records the attempt and returns the same generic error
for an unknown email and an incorrect password.

The signed session identifies the configured username and expires after 30 days.
The cookie is `HttpOnly`, `Secure` in HTTPS deployments, and `SameSite=Lax`.
Every authenticated request verifies that the session username still equals the
currently configured username. Changing the configured username therefore
invalidates existing sessions immediately.

Logout clears the session cookie and returns to the login page. Existing writes
that require a cookie-backed session continue to use that same session.

Non-browser requests no longer receive a Basic Auth challenge. Unauthenticated
requests get the existing unauthorized response or browser redirect as
appropriate.

## Security And Errors

The existing per-client login limiter protects form submissions. Login failures
do not reveal whether the username exists. The application keeps the existing
security headers and HTTPS-aware cookie handling.

The login form carries a short-lived signed CSRF token bound to a cookie. The
POST rejects a missing, expired, or mismatched token before checking credentials.
Redirect targets, if retained, must be local application paths only.

## Tests

Focused tests cover:

- required username and password configuration;
- successful login and 30-day session acceptance;
- generic failures for wrong username and password;
- login rate limiting and reset after success;
- missing or invalid login CSRF tokens;
- redirecting signed-out browser requests to the login page;
- rejecting unauthenticated non-browser requests without a Basic challenge;
- logout and session invalidation after a configured username change;
- removal of Google OAuth routes and UI.

The full test suite runs before deployment. Production is then smoke-tested for
the login page, authenticated dashboard access, logout, and protected routes.
