import re
from html import unescape

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app_dashboard.auth import (
    LOGIN_CSRF_COOKIE,
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    issue_session,
)
from app_dashboard.web import create_app

# 32+ chars: create_app refuses a short secret on a non-local host.
SESSION_SECRET = "test-session-secret-long-enough-to-pass"


@pytest.fixture(autouse=True)
def ppa_env(monkeypatch, db, test_app):
    monkeypatch.setenv("PARTNER_API_TOKEN", "x")
    monkeypatch.setenv("PARTNER_ORG_ID", "1")
    monkeypatch.setenv("PARTNER_APP_ID", "2")
    monkeypatch.setenv("DASHBOARD_USERNAME", "tester@example.com")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "suite-only-credential")
    monkeypatch.setenv("NO_SCHEDULER", "1")
    monkeypatch.setenv("SESSION_SECRET", SESSION_SECRET)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://dash.test")
    # Distinctive on purpose: the leak assertions below check that a guarded
    # page's name does not appear where it should not, and a name that never
    # renders anywhere would make those assertions vacuously true.
    monkeypatch.setenv("APP_NAME", "Zarquon Widgets")
    monkeypatch.setenv("TOKEN_1", "test-partner-token")
    owned_tables = (
        "raw_app_events", "app_events", "charges", "subscriptions", "shops",
        "transactions", "sync_state", "usage_events", "ga4_daily", "annotations",
        "tracking_events", "active_subscriptions",
    )
    for table in owned_tables:
        db.execute(f"alter table {table} alter column app_id set default {test_app.id}")
    yield
    cleanup = db
    if cleanup.closed:
        from app_dashboard.db import connect
        cleanup = connect()
    for table in owned_tables:
        cleanup.execute(f"alter table {table} alter column app_id drop default")
    if cleanup is not db:
        cleanup.close()


def keep_open(conn):
    """Routes close the connection they were handed; the shared test connection
    has to survive more than one request per test."""
    class NoClose:
        def __getattr__(self, name):
            return getattr(conn, name)

        def close(self):
            pass
    return NoClose()


def dashboard_client(app, *, authenticated=True):
    client = TestClient(app, base_url="https://dash.test")
    if authenticated:
        client.cookies.set(
            SESSION_COOKIE,
            issue_session(SESSION_SECRET, "tester@example.com", "Test User"),
        )
    return client


def submit_login(client, username="tester@example.com",
                 password="suite-only-credential"):
    page = client.get("/auth/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    return client.post(
        "/auth/login",
        data={"username": username, "password": password, "csrf_token": token},
        follow_redirects=False,
    )


class FakeManualSync:
    def __init__(self, state="idle", error=None):
        self.calls = []
        self.state = state
        self.error = error

    def start(self, apps, *, mode):
        if self.error:
            raise self.error
        self.calls.append(([app.slug for app in apps], mode))
        self.state = "running"

    def status(self):
        return {
            "state": self.state,
            "mode": None,
            "scope": [],
            "completed_steps": 0,
            "total_steps": 0,
            "current_app": None,
            "current_source": None,
            "started_at": None,
            "finished_at": None,
            "errors": [],
        }


def test_healthz_open(db):
    app = create_app(conn_factory=lambda: db)
    c = dashboard_client(app)
    assert c.get("/healthz").status_code == 200


def test_manual_sync_follows_selected_app_or_all_apps(db, app_factory, monkeypatch):
    other = app_factory(slug="other-app", name="Other App")
    monkeypatch.setenv("TOKEN_2", "second-partner-token")
    sync = FakeManualSync()
    c = dashboard_client(create_app(
        conn_factory=lambda: keep_open(db), manual_sync_coordinator=sync
    ))
    auth = ("tester", "suite-only-credential")

    selected = c.post(
        "/sync", data={"mode": "fresh", "app": other.slug},
        headers={"origin": "https://dash.test"},
        follow_redirects=False,
    )
    assert selected.status_code == 303
    assert selected.headers["location"] == "/?app=other-app"
    assert sync.calls == [(["other-app"], "fresh")]

    sync.state = "idle"
    all_apps = c.post(
        "/sync", data={"mode": "all"},
        headers={"origin": "https://dash.test"},
        follow_redirects=False,
    )
    assert all_apps.status_code == 303
    assert set(sync.calls[-1][0]) == {"test-app", "other-app"}
    assert sync.calls[-1][1] == "all"


def test_manual_sync_routes_validate_boundary(db):
    from app_dashboard.manual_sync import SyncAlreadyRunning

    auth = ("tester", "suite-only-credential")
    sync = FakeManualSync()
    c = dashboard_client(create_app(
        conn_factory=lambda: keep_open(db), manual_sync_coordinator=sync
    ))

    assert c.post(
        "/sync", data={"mode": "fresh"},
        headers={"origin": "https://evil.test"},
    ).status_code == 403
    assert c.post(
        "/sync", data={"mode": "wrong"},
        headers={"origin": "https://dash.test"},
    ).status_code == 400
    assert c.post(
        "/sync", data={"mode": "fresh", "app": "missing"},
        headers={"origin": "https://dash.test"},
    ).status_code == 404

    sync.error = SyncAlreadyRunning()
    assert c.post(
        "/sync", data={"mode": "fresh"},
        headers={"origin": "https://dash.test"},
    ).status_code == 409
    status = c.get("/sync/status")
    assert status.status_code == 200
    assert status.json()["state"] == "idle"


def test_overview_renders_scoped_manual_sync_controls(db):
    sync = FakeManualSync(state="running")
    c = dashboard_client(create_app(
        conn_factory=lambda: keep_open(db), manual_sync_coordinator=sync
    ))
    auth = ("tester", "suite-only-credential")

    overview = c.get("/?app=test-app")
    assert overview.status_code == 200
    assert "Fetch data" in overview.text
    assert "Fetch fresh data" in overview.text
    assert "Fetch all data again" in overview.text
    assert 'name="app" value="test-app"' in overview.text
    assert 'id="sync-progress"' in overview.text
    assert "data-confirm-full" in overview.text
    assert "trackManualSync" in overview.text
    assert c.get("/customers?app=test-app").text.count("Fetch data") == 0


def test_selector_lists_every_app_and_unknown_slugs_404(
    db, app_factory, monkeypatch
):
    other = app_factory(slug="other-app", name="Other App")
    monkeypatch.setenv("TOKEN_2", "second-partner-token")
    c = dashboard_client(create_app(conn_factory=lambda: keep_open(db)))

    combined = c.get("/")
    assert combined.status_code == 200
    assert "All apps" in combined.text
    assert "Test App" in combined.text and other.name in combined.text
    assert 'data-app-picker' in combined.text
    assert 'data-app-picker-search' in combined.text
    assert 'data-app-value="other-app"' in combined.text
    assert 'data-app-search-text="other app other-app"' in combined.text
    assert 'class="app-selector-fallback"' in combined.text
    assert ".app-picker-option[hidden] { display: none; }" in combined.text
    assert "max-width: none;" in combined.text
    assert "new FormData(form)" in combined.text
    assert c.get(
        "/?app=other-app"
    ).status_code == 200
    assert c.get(
        "/?app=does-not-exist"
    ).status_code == 404


def test_form_control_assets_and_date_hooks_are_local(db):
    c = dashboard_client(create_app(conn_factory=lambda: keep_open(db)))

    activity = c.get("/activity")
    overview = c.get("/?app=test-app")

    assert '/static/vendor/flatpickr/flatpickr.min.css' in activity.text
    assert '/static/vendor/flatpickr/flatpickr.min.js' in activity.text
    assert 'name="on"' in activity.text and "data-datepicker" in activity.text
    assert 'name="on_date"' in overview.text and "data-datepicker" in overview.text
    assert '.date-control input:not([type="hidden"])' in activity.text
    assert c.get("/static/vendor/flatpickr/flatpickr.min.css").status_code == 200
    assert c.get("/static/vendor/flatpickr/flatpickr.min.js").status_code == 200


@pytest.mark.parametrize("path", ["/", "/customers", "/trials", "/reports/funnel",
                                  "/reports/retention", "/reports/traffic"])
def test_pages_bounce_anonymous_browsers_to_login(db, path):
    app = create_app(conn_factory=lambda: db)
    c = dashboard_client(app, authenticated=False)
    r = c.get(path, headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/auth/login"
    # The body of a redirect must not leak the page it was guarding. The
    # wordmark and <title> carry the app name on every rendered page, so its
    # absence is what says no page was rendered into this body.
    assert "Zarquon Widgets" not in r.text


def test_pages_render_for_signed_session(db):
    # Real factory: each route opens and closes its own connection, so a shared
    # one would be closed out from under the second request.
    from app_dashboard.db import connect
    app = create_app(conn_factory=connect)
    c = dashboard_client(app)
    assert c.get("/").status_code == 200
    assert c.get("/customers").status_code == 200


@pytest.mark.parametrize(("username", "password"), [
    ("wrong@example.com", "suite-only-credential"),
    ("tester@example.com", "wrong"),
])
def test_wrong_credentials_return_the_same_generic_error(db, username, password):
    app = create_app(conn_factory=lambda: db)
    c = dashboard_client(app, authenticated=False)
    r = submit_login(c, username=username, password=password)
    assert r.status_code == 401
    assert "Incorrect email or password" in r.text
    assert "suite-only-credential" not in r.text


def test_unauthenticated_api_request_has_no_basic_challenge(db):
    app = create_app(conn_factory=lambda: db)
    c = dashboard_client(app, authenticated=False)
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 401
    assert "www-authenticate" not in r.headers


def test_login_creates_a_thirty_day_session(db):
    c = dashboard_client(create_app(conn_factory=lambda: db), authenticated=False)
    r = submit_login(c)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    cookie = r.headers["set-cookie"]
    assert f"Max-Age={SESSION_MAX_AGE}" in cookie
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Secure" in cookie
    assert c.get("/").status_code == 200


def test_login_rejects_missing_or_mismatched_csrf(db):
    c = dashboard_client(create_app(conn_factory=lambda: db), authenticated=False)
    missing = c.post("/auth/login", data={
        "username": "tester@example.com", "password": "suite-only-credential",
    })
    assert missing.status_code == 400

    page = c.get("/auth/login")
    assert LOGIN_CSRF_COOKIE in page.headers["set-cookie"]
    forged = c.post("/auth/login", data={
        "username": "tester@example.com", "password": "suite-only-credential",
        "csrf_token": "forged",
    })
    assert forged.status_code == 400


def test_signed_session_cookie_authenticates(db):
    app = create_app(conn_factory=lambda: db)
    c = dashboard_client(app)
    c.cookies.set(SESSION_COOKIE,
                  issue_session(SESSION_SECRET, "tester@example.com", "Test User"))
    r = c.get("/")
    assert r.status_code == 200
    # The header greets you by name; the address it was derived from stays off
    # the page, where it was eating a third of the nav bar.
    assert "Test User" in r.text
    assert "tester@example.com" not in r.text


@pytest.mark.parametrize("cookie", [
    "not-a-real-token",
    issue_session("some-other-secret", "tester@example.com"),
    issue_session(SESSION_SECRET, "someone-else@example.com"),
])
def test_forged_or_out_of_domain_cookies_do_not_authenticate(db, cookie):
    app = create_app(conn_factory=lambda: db)
    c = dashboard_client(app)
    c.cookies.set(SESSION_COOKIE, cookie)
    r = c.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/auth/login"


def test_logout_clears_the_session_cookie(db):
    app = create_app(conn_factory=lambda: db)
    c = dashboard_client(app)
    r = c.get("/auth/logout", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/auth/login"
    # Assert on the Set-Cookie the browser actually receives; the test client's
    # cookie jar keys on domain and would not match a jar entry set by hand.
    cleared = r.headers["set-cookie"]
    assert cleared.startswith(f'{SESSION_COOKIE}=""')
    assert "Max-Age=0" in cleared


def test_google_auth_routes_are_removed(db):
    app = create_app(conn_factory=lambda: db)
    c = dashboard_client(app, authenticated=False)
    assert c.get("/auth/google").status_code == 404
    assert c.get("/auth/callback").status_code == 404


def test_report_pages_render(db):
    # real factory: report routes open AND close their own connection each hit
    from app_dashboard.db import connect
    app = create_app(conn_factory=connect)
    c = dashboard_client(app)
    for name, marker in (("funnel", "Lifecycle funnel"), ("traffic", "Select one app"),
                         ("retention", "Retention")):
        r = c.get(f"/reports/{name}")
        assert r.status_code == 200
        assert marker in r.text


def test_customers_route_closes_the_connection_it_opened(db):
    # conn_factory in production is app_dashboard.db.connect: a fresh connection per
    # call. The route must close what it opens or every dashboard hit leaks
    # a Postgres connection. Wrap (don't replace) `db` so the shared test
    # connection stays open for the rest of the suite/fixture teardown.
    class TrackClose:
        def __init__(self, inner):
            self._inner = inner
            self.closed = False

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            self.closed = True

    tracked = TrackClose(db)
    app = create_app(conn_factory=lambda: tracked)
    c = dashboard_client(app)
    r = c.get("/customers")
    assert r.status_code == 200
    assert tracked.closed is True


def test_customers_pages_at_50_and_keeps_filters_on_the_next_link(db):
    rows = ",".join(
        f"('gid{i:03d}','Shop {i:03d}','US','installed')" for i in range(120)
    )
    db.execute(
        f"insert into shops(shop_gid,shop_name,country,install_state) values {rows}"
    )
    db.commit()
    app = create_app(conn_factory=lambda: keep_open(db))
    c = dashboard_client(app)

    first = c.get("/customers", params={"country": "US"})
    assert first.status_code == 200
    assert "1&ndash;50 of 120" in first.text
    assert "country=US&amp;page=2" in first.text

    last = c.get("/customers", params={"country": "US", "page": 3})
    assert "101&ndash;120 of 120" in last.text
    # No next link on the final page.
    assert "page=4" not in last.text


def test_customers_page_number_is_clamped_to_the_real_range(db):
    db.execute("insert into shops(shop_gid,shop_name,install_state) "
               "values ('g1','Only Shop','installed')")
    db.commit()
    app = create_app(conn_factory=lambda: keep_open(db))
    c = dashboard_client(app)
    for page in ("999", "0", "-4"):
        r = c.get("/customers", params={"page": page})
        assert r.status_code == 200
        assert "Only Shop" in r.text


def test_customers_filters_render_results(db):
    db.execute(
        "insert into shops(shop_gid,shop_name,industry,country,email,install_state) "
        "values ('ai1','Test Shop','Apparel','US','a@x.com','installed')"
    )
    db.commit()
    app = create_app(conn_factory=lambda: db)
    c = dashboard_client(app)
    r = c.get("/customers", params={"industry": "Apparel"})
    assert r.status_code == 200
    assert "Test Shop" in r.text


def test_trials_page_and_customer_detail_show_current_trial(db, test_app):
    db.execute(
        "insert into shops "
        "(shop_gid, shop_name, shop_domain, install_state) "
        "values ('trial-shop', 'Trial Merchant', 'trial.myshopify.com', 'installed')"
    )
    db.execute(
        "insert into subscriptions "
        "(id, shop_gid, monthly_amount, converted_at) "
        "values ('trial-sub', 'trial-shop', 29, now())"
    )
    db.execute(
        "insert into active_subscriptions "
        "(shop_gid, legacy_subscription_id, billing_period, trial_ends_at, "
        " cancel_at_end_of_cycle, item_description, observed_at) "
        "values ('trial-shop', 'trial-sub', 'EVERY_30_DAYS', "
        " now() + interval '5 days', true, 'Growth', now())"
    )
    db.execute(
        "insert into sync_state (source, last_synced_at) "
        "values ('partner_active_subscriptions', now())"
    )
    db.commit()

    c = dashboard_client(create_app(conn_factory=lambda: keep_open(db)))
    page = c.get(
        "/trials?app=test-app"
    )
    assert page.status_code == 200
    assert "Trial Merchant" in page.text
    assert "Growth" in page.text
    assert "$29.00" in page.text
    assert "Cancelling" in page.text
    assert 'href="https://trial.myshopify.com"' in page.text
    assert 'href="/customers/trial-shop?app=test-app"' in page.text

    detail = c.get(
        "/customers/trial-shop?app=test-app",
    )
    assert detail.status_code == 200
    assert "Trial" in detail.text
    assert "$29.00 MRR after conversion" in detail.text
    assert "cancellation scheduled" in detail.text


def test_login_is_a_page_that_explains_the_dashboard(db):
    app = create_app(conn_factory=lambda: db)
    c = dashboard_client(app, authenticated=False)
    r = c.get("/auth/login", follow_redirects=False)
    assert r.status_code == 200
    assert "For internal use only" in r.text
    assert 'name="username"' in r.text
    assert 'name="password"' in r.text
    assert 'name="csrf_token"' in r.text
    assert "/auth/google" not in r.text
    # The art is the page background, not an <img> in the panel.
    assert 'class="cover"' in r.text
    assert "url('/static/login.webp')" in r.text
    assert c.get("/static/login.webp").status_code == 200
    # No sidebar on an unauthenticated page: every link in it would 307 back
    # here.
    assert 'class="sidebar"' not in r.text
    # The page is public, so it must not describe the auth model. An earlier
    # version named the allowed domains, the sync interval, and how the address
    # check works.
    for leak in ("tester@example.com", "Basic", "allowlist",
                 "refreshed every"):
        assert leak not in r.text, leak


def test_unauthenticated_pages_disclaim_affiliation_with_shopify(db):
    """The sign-in screen and the error screens are the only surfaces a stranger
    reaches on a deployment that is on a real hostname, so they are the ones
    that have to say this is not Shopify's. Pinned by a test because a template
    tidy-up would otherwise drop it silently, and the reason it exists is not
    visible from the markup."""
    app = create_app(conn_factory=lambda: db)
    c = dashboard_client(app, authenticated=False)
    for path in ("/auth/login", "/no-such-page"):
        # Collapsed, so reflowing the template's source lines cannot break the
        # assertion on a phrase that is still on the page.
        text = " ".join(c.get(path, headers={"accept": "text/html"}).text.split())
        assert "Not affiliated with, endorsed by, or a product of Shopify." in text, path
        assert "is a trademark of Shopify Inc." in text, path

    # Signed in, it is gone: the operator installed this and knows whose it is.
    signed_in = dashboard_client(app)
    assert "Not affiliated with" not in signed_in.get("/").text


def test_nothing_here_is_indexable(db):
    app = create_app(conn_factory=lambda: db)
    c = dashboard_client(app, authenticated=False)
    robots = c.get("/robots.txt")
    assert robots.status_code == 200
    assert "Disallow: /" in robots.text
    # The header covers what has no <head>: the .md twins, the illustrations,
    # a JSON error body.
    for path in ("/robots.txt", "/auth/login", "/healthz"):
        assert c.get(path).headers["x-robots-tag"] == "noindex, nofollow"
    assert '<meta name="robots" content="noindex, nofollow">' in \
        c.get("/auth/login").text


def test_failed_login_keeps_the_login_illustration(db):
    app = create_app(conn_factory=lambda: keep_open(db))
    c = dashboard_client(app, authenticated=False)
    unauthorized = submit_login(c, password="wrong")
    assert unauthorized.status_code == 401
    assert "url('/static/login.webp')" in unauthorized.text
    assert 'class="cover"' in unauthorized.text
    assert "www-authenticate" not in unauthorized.headers


def test_a_signed_in_reader_keeps_the_sidebar_on_an_error(db):
    """A 404 is a place to navigate away from, and for them the links work."""
    app = create_app(conn_factory=lambda: db)
    c = dashboard_client(app)
    c.cookies.set(SESSION_COOKIE,
                  issue_session(SESSION_SECRET, "tester@example.com", "Test User"))
    signed_in = c.get("/nope", headers={"Accept": "text/html"})
    assert signed_in.status_code == 404
    assert 'class="sidebar"' in signed_in.text
    assert "Back to Overview" in signed_in.text

    c.cookies.clear()
    signed_out = c.get("/nope", headers={"Accept": "text/html"})
    assert 'class="sidebar"' not in signed_out.text
    assert "Go to sign-in" in signed_out.text


def test_long_app_name_can_wrap_inside_sidebar(db, test_app):
    db.execute(
        "update apps set name='Happy Birthday Marketing App' where id=%s",
        (test_app.id,),
    )
    app = create_app(conn_factory=lambda: keep_open(db))
    c = dashboard_client(app)

    page = c.get("/?app=test-app")

    assert "Happy Birthday Marketing App" in page.text
    assert "Happy&nbsp;Birthday&nbsp;Marketing&nbsp;App" not in page.text
    assert ".wordmark > span:last-child" in page.text


def test_footer_reports_the_render_time(db):
    """It reads request.state.started, which the security middleware stamps.
    If that ever stops being set the footer empties silently, so pin it."""
    app = create_app(conn_factory=lambda: keep_open(db))
    c = dashboard_client(app)
    for path in ("/", "/reports/churn"):
        body = c.get(path).text
        assert re.search(r"Rendered in \d+(\.\d)? ms", body), path
    # Including the 404, which renders the same shell.
    assert re.search(r"Rendered in \d+(\.\d)? ms",
                     c.get("/nope",
                           headers={"Accept": "text/html"}).text)


def test_browser_404_renders_the_page_and_others_keep_json(db):
    """A browser gets the app's own chrome; anything parsing a response does
    not, so API-style requests and curl see the JSON body they always saw."""
    # keep_open: this test makes more than one request, and the customer route
    # closes the connection it was handed.
    app = create_app(conn_factory=lambda: keep_open(db))
    c = dashboard_client(app)
    accept_html = {"Accept": "text/html,application/xhtml+xml"}

    missing_shop = c.get("/customers/nope.myshopify.com",
                         headers=accept_html)
    assert missing_shop.status_code == 404
    # Unescaped because Jinja autoescaping renders the apostrophe as &#39;.
    assert "That shop isn't on record" in unescape(missing_shop.text)
    assert "Back to Customers" in missing_shop.text
    generic = c.get("/no-such-route", headers=accept_html)
    assert generic.status_code == 404
    assert "Page not found" in generic.text

    # Default TestClient Accept is */*, which is what curl sends.
    as_json = c.get("/customers/nope.myshopify.com")
    assert as_json.status_code == 404
    assert as_json.json() == {"detail": "No such shop"}


def test_404_page_carries_the_csp_nonce(db):
    """It extends base.html, so its inline scripts need the nonce like every
    other page. A mismatch here is silent in the status code."""
    app = create_app(conn_factory=lambda: db)
    c = dashboard_client(app)
    r = c.get("/no-such-route",
              headers={"Accept": "text/html"})
    nonce = r.headers["content-security-policy"].split("'nonce-")[1].split("'")[0]
    assert f'nonce="{nonce}"' in r.text


def test_non_404_errors_keep_their_shape(db):
    """The handler is registered for every HTTPException, so the redirect to
    /auth/login has to survive a browser Accept header."""
    app = create_app(conn_factory=lambda: db)
    c = dashboard_client(app, authenticated=False)
    r = c.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/auth/login"


def test_customers_page_carries_shops_but_no_contact_details(db):
    db.execute(
        "insert into shops (shop_gid, shop_name, shop_domain, owner_name, email, "
        "country, install_state) values "
        "('g1','Visible Shop','visible.myshopify.com','Jane Merchant',"
        "'jane@visible.example','US','installed')")
    db.commit()
    app = create_app(conn_factory=lambda: db)
    c = dashboard_client(app)
    text = c.get("/customers").text
    assert "Visible Shop" in text and "visible.myshopify.com" in text
    assert "jane@visible.example" not in text
    assert "Jane Merchant" not in text


def test_actions_carries_no_contact_details(db):
    """A vendor export's contact columns were every staff account on the shop, agencies
    and our own team included, so the review sheet was naming the wrong people
    entirely. The page omits them and the shop still identifies itself by name
    and domain."""
    db.execute(
        "insert into shops (shop_gid, shop_name, shop_domain, owner_name, email, "
        "install_state) values ('g1','Askable','askable.myshopify.com',"
        "'Jane Merchant','jane@askable.example','installed')")
    db.execute(
        "insert into subscriptions (id, shop_gid, monthly_amount, converted_at) "
        "values ('c1','g1',19.00, now() - interval '90 days')")
    db.commit()
    c = dashboard_client(create_app(conn_factory=lambda: keep_open(db)))

    body = c.get("/actions").text
    assert "askable.myshopify.com" in body
    assert "Jane Merchant" not in body
    assert "jane@askable.example" not in body


# --- POST /ingest/usage ----------------------------------------------------

USAGE_TOKEN = "ingest-token-for-tests"


def _usage_body(**over):
    event = {"event_id": "e1", "shop_gid": "gid://shopify/Shop/1",
             "event_type": "offer_created", "occurred_at": "2026-08-07T11:00:00Z"}
    event.update(over)
    return {"events": [event]}


@pytest.fixture
def ingest_client(db, test_app, monkeypatch):
    monkeypatch.setenv("TEST_APP_USAGE_TOKEN", USAGE_TOKEN)
    db.execute(
        """update apps set usage_token_env = %s, usage_event_types = %s,
                  usage_activation_event = %s, usage_live_event = %s
           where id = %s""",
        (
            "TEST_APP_USAGE_TOKEN",
            Jsonb(["offer_created", "offer_impression", "offer_conversion"]),
            "offer_created",
            "offer_impression",
            test_app.id,
        ),
    )
    db.commit()
    return dashboard_client(create_app(conn_factory=lambda: keep_open(db)))


def test_ingest_stores_a_batch_with_the_token(ingest_client, db):
    r = ingest_client.post("/ingest/usage/test-app", json=_usage_body(),
                           headers={"X-Usage-Token": USAGE_TOKEN})
    assert r.status_code == 200
    assert r.json()["stored"] == 1
    assert db.execute("select count(*) from usage_events").fetchone()[0] == 1


@pytest.mark.parametrize("headers", [
    {},                                    # no token at all
    {"X-Usage-Token": ""},                 # empty token
    {"X-Usage-Token": "wrong"},
    {"X-Usage-Token": USAGE_TOKEN + "x"},  # prefix of the real token
])
def test_ingest_refuses_every_wrong_token_identically(ingest_client, db, headers):
    r = ingest_client.post("/ingest/usage/test-app", json=_usage_body(), headers=headers)
    assert r.status_code == 401
    assert r.json() == {"detail": "Unauthorized"}
    assert db.execute("select count(*) from usage_events").fetchone()[0] == 0


def test_ingest_refuses_everything_when_no_token_is_configured(db, monkeypatch):
    """An unconfigured server must look exactly like a wrong token, so probing
    cannot tell the two apart."""
    monkeypatch.delenv("USAGE_INGEST_TOKEN", raising=False)
    c = dashboard_client(create_app(conn_factory=lambda: keep_open(db)))
    r = c.post(
        "/ingest/usage/test-app",
        json=_usage_body(),
        headers={"X-Usage-Token": "anything"},
    )
    assert r.status_code == 401
    assert r.json() == {"detail": "Unauthorized"}


def test_ingest_does_not_accept_a_dashboard_session_or_basic_auth(ingest_client):
    """The ingest secret is the only key to this door: a signed-in human, or
    anyone holding dashboard credentials, still cannot write events."""
    r = ingest_client.post(
        "/ingest/usage/test-app",
        json=_usage_body(),
    )
    assert r.status_code == 401


def test_ingest_rejects_an_oversized_body_before_parsing_it(ingest_client, db):
    from app_dashboard.usage import MAX_BODY_BYTES
    r = ingest_client.post(
        "/ingest/usage/test-app",
        content=b'{"events": [' + b"x" * (MAX_BODY_BYTES + 1024) + b"]}",
        headers={"X-Usage-Token": USAGE_TOKEN, "Content-Type": "application/json"})
    assert r.status_code == 413
    assert db.execute("select count(*) from usage_events").fetchone()[0] == 0


def test_ingest_rejects_an_unknown_event_type(ingest_client, db):
    r = ingest_client.post("/ingest/usage/test-app", json=_usage_body(event_type="drop_table"),
                           headers={"X-Usage-Token": USAGE_TOKEN})
    assert r.status_code == 422
    assert db.execute("select count(*) from usage_events").fetchone()[0] == 0


def test_ingest_is_safe_to_retry(ingest_client):
    headers = {"X-Usage-Token": USAGE_TOKEN}
    first = ingest_client.post(
        "/ingest/usage/test-app", json=_usage_body(), headers=headers
    ).json()
    second = ingest_client.post(
        "/ingest/usage/test-app", json=_usage_body(), headers=headers
    ).json()
    assert first["stored"] == 1
    assert second["stored"] == 0 and second["duplicates"] == 1


def test_usage_tokens_and_event_ids_are_isolated_per_app(
    db, test_app, app_factory, monkeypatch
):
    other = app_factory(slug="other-app", name="Other App")
    monkeypatch.setenv("TOKEN_2", "second-partner-token")
    monkeypatch.setenv("FIRST_USAGE_TOKEN", "first-secret")
    monkeypatch.setenv("SECOND_USAGE_TOKEN", "second-secret")
    event_types = Jsonb(["offer_created", "offer_impression"])
    db.execute(
        """update apps set
                  usage_token_env = case when slug = 'test-app'
                                         then 'FIRST_USAGE_TOKEN'
                                         else 'SECOND_USAGE_TOKEN' end,
                  usage_event_types = %s,
                  usage_activation_event = 'offer_created',
                  usage_live_event = 'offer_impression'
           where id = any(%s)""",
        (event_types, [test_app.id, other.id]),
    )
    db.commit()
    c = dashboard_client(create_app(conn_factory=lambda: keep_open(db)))

    first = c.post(
        "/ingest/usage/test-app",
        json=_usage_body(),
        headers={"X-Usage-Token": "first-secret"},
    )
    second = c.post(
        "/ingest/usage/other-app",
        json=_usage_body(),
        headers={"X-Usage-Token": "second-secret"},
    )
    crossed = c.post(
        "/ingest/usage/other-app",
        json=_usage_body(event_id="crossed"),
        headers={"X-Usage-Token": "first-secret"},
    )

    assert first.json()["stored"] == 1
    assert second.json()["stored"] == 1
    assert crossed.status_code == 401
    assert db.execute("select count(*) from usage_events").fetchone()[0] == 2


def test_event_properties_render_escaped_if_they_ever_reach_a_page(ingest_client, db):
    """Nothing renders raw `properties` today, and the pages that would are
    Jinja-autoescaped. This pins that: a script tag pushed through the ingest
    endpoint cannot come back as markup."""
    ingest_client.post(
        "/ingest/usage/test-app",
        json=_usage_body(properties={"offer_name": "<script>alert(1)</script>"}),
        headers={"X-Usage-Token": USAGE_TOKEN})
    r = ingest_client.get("/reports/funnel")
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text


def test_funnel_shows_an_honest_empty_state_before_any_usage_data(db):
    c = dashboard_client(create_app(conn_factory=lambda: keep_open(db)))
    r = c.get("/reports/funnel")
    assert "Nothing has arrived yet" in r.text
    # Zero activation would be a claim; "unknown" is the truth, so the stat
    # tiles must not render at all.
    assert "Built an offer" not in r.text
    assert "Median time to first offer" not in r.text


def test_customer_detail_page_uses_the_stable_shop_gid(db):
    """Customer identity is the stable shop GID, not a mutable domain."""
    db.execute("insert into shops (shop_gid, shop_domain, shop_name, install_state, "
               "owner_name, email) values ('g1', 'x.myshopify.com', 'Ex', 'installed', "
               "'Jo Smith', 'jo@example.com')")
    db.execute("insert into raw_app_events (id, type, occurred_at, shop_gid, payload) "
               "values ('e1', 'RELATIONSHIP_INSTALLED', '2026-01-10Z', 'g1', '{}')")
    db.execute("insert into app_events (platform_event_id, type, occurred_at, shop_gid) "
               "values ('e1', 'installed', '2026-01-10Z', 'g1')")
    db.commit()

    c = dashboard_client(create_app(conn_factory=lambda: keep_open(db)))
    page = c.get("/customers/g1")
    assert page.status_code == 200
    assert "Ex" in page.text

    assert c.get("/customers/nope").status_code == 404


def test_customer_detail_distinguishes_free_and_paid_plans(db):
    db.execute(
        "insert into shops (shop_gid, shop_domain, shop_name, install_state) values "
        "('free-shop', 'free.myshopify.com', 'Free Shop', 'installed'), "
        "('paid-shop', 'paid.myshopify.com', 'Paid Shop', 'installed')"
    )
    db.execute(
        "insert into subscriptions (id, shop_gid, monthly_amount, converted_at) values "
        "('free-sub', 'free-shop', 0, '2026-08-10Z'), "
        "('paid-sub', 'paid-shop', 29, '2026-08-10Z')"
    )
    db.execute(
        "insert into charges (gid, plan_amount, plan_interval) values "
        "('free-sub', 0, 'EVERY_30_DAYS'), "
        "('paid-sub', 29, 'EVERY_30_DAYS')"
    )
    db.commit()

    c = dashboard_client(create_app(conn_factory=lambda: keep_open(db)))
    free = c.get(
        "/customers/free-shop?app=test-app",
    ).text
    paid = c.get(
        "/customers/paid-shop?app=test-app",
    ).text

    assert "Free plan" in free
    assert "No recurring charge" in free
    assert "$0<small>&nbsp;/mo</small>" not in free
    assert "$29<small>&nbsp;/mo</small>" in paid
    assert "Free plan" not in paid


def test_latest_activity_links_to_merchant_detail_and_storefront(db):
    shop_gid = "gid://partners/Shop/123"
    db.execute(
        "insert into shops (shop_gid, shop_domain, shop_name, install_state) "
        "values (%s, 'linked.myshopify.com', 'Linked Shop', 'installed')",
        (shop_gid,),
    )
    db.execute(
        "insert into app_events "
        "(platform_event_id, type, occurred_at, shop_gid) "
        "values ('linked-event', 'installed', '2026-08-10Z', %s)",
        (shop_gid,),
    )
    db.commit()

    c = dashboard_client(create_app(conn_factory=lambda: keep_open(db)))
    page = c.get(
        "/?app=test-app"
    ).text

    assert (
        'href="/customers/gid%3A//partners/Shop/123?app=test-app"'
        ">Linked Shop</a>"
    ) in page
    assert 'href="https://linked.myshopify.com">storefront</a>' in page


def test_customer_detail_needs_auth(db):
    c = dashboard_client(create_app(conn_factory=lambda: db), authenticated=False)
    r = c.get("/customers/x.myshopify.com", headers={"Accept": "text/html"},
              follow_redirects=False)
    assert r.status_code == 307


def test_activity_page_filters_and_links_to_the_merchant_and_store(db):
    db.execute(
        "insert into shops (shop_gid, shop_name, shop_domain, install_state) "
        "values ('activity-shop', 'Activity Shop', 'activity.myshopify.com', 'installed')"
    )
    db.execute(
        """insert into app_events
               (platform_event_id, type, occurred_at, shop_gid, net_change)
           values ('activity-paid', 'subscribed', '2026-08-10T10:00:00Z',
                   'activity-shop', 19.00),
                  ('activity-install', 'installed', '2026-08-11T10:00:00Z',
                   'activity-shop', null)"""
    )
    db.commit()

    c = dashboard_client(create_app(conn_factory=lambda: keep_open(db)))
    page = c.get(
        "/activity?app=test-app&on=2026-08-10&event_type=subscribed",
    )
    assert page.status_code == 200
    assert 'name="on"' in page.text and "data-datepicker" in page.text
    assert "Activity Shop" in page.text
    assert "Subscribed" in page.text
    assert "+$19.00" in page.text
    assert "When (CET/CEST)" in page.text
    assert "Aug 10, 2026 12:00 CEST" in page.text
    assert 'class="pill installed"' not in page.text
    assert 'href="/customers/activity-shop?app=test-app"' in page.text
    assert 'href="https://activity.myshopify.com"' in page.text
    assert 'href="/activity?app=test-app"' in page.text


def test_activity_page_rejects_invalid_filters_without_a_422(db):
    c = dashboard_client(create_app(conn_factory=lambda: keep_open(db)))
    page = c.get(
        "/activity?on=not-a-date&event_type=not-a-real-event&page=banana",
    )
    assert page.status_code == 200


def test_all_apps_overview_shows_portfolio_unit_economics_and_live_trials(
    db, app_factory, test_app, monkeypatch
):
    monkeypatch.setenv("TOKEN_2", "test-partner-token-2")
    beta = app_factory(slug="beta", name="Beta")
    db.execute(
        """insert into shops
               (app_id, shop_gid, shop_name, install_state, installed_at)
           values (%s, 'paid', 'Paid Shop', 'installed', now() - interval '300 days'),
                  (%s, 'free', 'Free Shop', 'installed', now() - interval '300 days'),
                  (%s, 'trial', 'Trial Shop', 'installed', now() - interval '2 days')""",
        (test_app.id, test_app.id, beta.id),
    )
    db.execute(
        """insert into subscriptions
               (app_id, id, shop_gid, monthly_amount, converted_at)
           values (%s, 'paid-sub', 'paid', 20.00, now() - interval '250 days'),
                  (%s, 'trial-sub', 'trial', 30.00, now() - interval '2 days')""",
        (test_app.id, beta.id),
    )
    db.execute(
        """insert into active_subscriptions
               (app_id, shop_gid, legacy_subscription_id, trial_ends_at, observed_at)
           values (%s, 'trial', 'trial-sub', now() + interval '5 days', now())""",
        (beta.id,),
    )
    db.commit()

    page = _signed_in().get("/")
    body = unescape(page.text)
    assert page.status_code == 200
    for heading in (
        "Installed", "Paying", "Paid share", "Monthly churn", "LTV",
        "Current trials", "Trial MRR",
    ):
        assert heading in body
    assert "Paid Shop" not in body
    assert "50%" in body
    assert "Beta" in body
    assert "$30.00" in body
    assert "Trial conversion" not in body


# --- Definitions and deltas ---------------------------------------------------

def _signed_in():
    """A client with the session cookie annotation writes require."""
    from app_dashboard.db import connect
    app = create_app(conn_factory=connect)
    c = dashboard_client(app)
    c.cookies.set(SESSION_COOKIE,
                  issue_session(SESSION_SECRET, "tester@example.com", "Test User"))
    return c


def test_overview_carries_each_tile_definition(db):
    """A number with no definition beside it is the thing metrics.py exists to
    make impossible, so the page has to actually render them."""
    from app_dashboard.metrics import METRICS
    r = _signed_in().get("/")
    assert r.status_code == 200
    body = unescape(r.text)
    for key in ("installed", "active_mrr", "paying", "net_30d"):
        assert METRICS[key].definition in body
        assert METRICS[key].source in body


def test_overview_compares_every_headline_tile(db):
    r = _signed_in().get("/")
    # Six tiles, each with its own delta line, and each saying what it is
    # against rather than leaving the reader to assume.
    assert r.text.count('class="delta-num"') == 6
    assert "vs 30 days ago" in r.text
    assert "vs prior 30 days" in r.text


# --- Annotations --------------------------------------------------------------

def test_a_note_is_written_and_shown(db):
    c = _signed_in()
    posted = c.post("/annotations",
                    data={"on_date": "2026-03-01", "note": "Raised the price to $19"},
                    follow_redirects=False)
    assert posted.status_code == 303
    body = unescape(c.get("/").text)
    assert "Raised the price to $19" in body
    assert "tester@example.com" in body


def test_a_note_marks_its_month_on_the_charts(db):
    c = _signed_in()
    c.post("/annotations", data={"on_date": "2026-03-01", "note": "price change"})
    assert 'class="anno-dot"' in c.get("/?months=24").text


def test_the_author_comes_from_the_session_not_the_form(db):
    """A field the browser supplies is a field anyone can set."""
    c = _signed_in()
    c.post("/annotations", data={"on_date": "2026-03-01", "note": "n",
                                 "author": "someone-else@evil.test"})
    from app_dashboard import annotations as anno
    from app_dashboard.db import connect
    conn = connect()
    try:
        assert anno.recent(conn)[0]["author"] == "tester@example.com"
    finally:
        conn.close()


def test_a_bad_note_comes_back_with_a_message_rather_than_a_500(db):
    c = _signed_in()
    r = c.post("/annotations", data={"on_date": "not-a-date", "note": "x"},
               follow_redirects=False)
    assert r.status_code == 303
    assert "note_error" in r.headers["location"]
    assert "YYYY-MM-DD" in unescape(c.get(r.headers["location"]).text)


def test_anonymous_user_cannot_write_a_note(db):
    from app_dashboard.db import connect
    c = dashboard_client(create_app(conn_factory=connect), authenticated=False)
    r = c.post("/annotations",
               data={"on_date": "2026-03-01", "note": "x"},
               follow_redirects=False)
    assert r.status_code == 401
    from app_dashboard import annotations as anno
    conn = connect()
    try:
        assert anno.recent(conn) == []
    finally:
        conn.close()


def test_an_anonymous_post_never_reaches_the_database(db):
    from app_dashboard.db import connect
    c = dashboard_client(create_app(conn_factory=connect), authenticated=False)
    r = c.post("/annotations", data={"on_date": "2026-03-01", "note": "x"},
               follow_redirects=False)
    # 303, not 307: a method-preserving redirect would make the browser re-POST
    # the note to the GET-only login page, losing the text on a 405.
    assert r.status_code in (303, 401, 403)
    from app_dashboard import annotations as anno
    conn = connect()
    try:
        assert anno.recent(conn) == []
    finally:
        conn.close()


def test_a_note_cannot_inject_markup_into_the_page(db):
    """The first route that stores user text must return it HTML-escaped."""
    c = _signed_in()
    c.post("/annotations", data={"on_date": "2026-03-01",
                                 "note": "<script>alert(1)</script>"})
    page = c.get("/")
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text

def _only_note_id():
    from app_dashboard import annotations as anno
    from app_dashboard.db import connect
    conn = connect()
    try:
        rows = anno.recent(conn)
        return rows[0]["id"] if rows else None
    finally:
        conn.close()


def test_a_note_can_be_deleted_from_the_page(db):
    c = _signed_in()
    c.post("/annotations", data={"on_date": "2026-03-01", "note": "wrong on purpose"})
    assert "wrong on purpose" in unescape(c.get("/").text)

    r = c.post("/annotations/delete", data={"id": _only_note_id()},
               follow_redirects=False)
    assert r.status_code == 303
    assert "note_error" not in r.headers["location"]
    assert "wrong on purpose" not in unescape(c.get("/").text)
    assert _only_note_id() is None


def test_deleting_a_note_takes_its_chart_marker_with_it(db):
    """A dot that outlives the note explaining it is worse than no dot."""
    c = _signed_in()
    c.post("/annotations", data={"on_date": "2026-03-01", "note": "price change"})
    assert 'class="anno-dot"' in c.get("/?months=24").text
    c.post("/annotations/delete", data={"id": _only_note_id()})
    assert 'class="anno-dot"' not in c.get("/?months=24").text


def test_anonymous_user_cannot_delete_a_note(db):
    from app_dashboard.db import connect
    c = _signed_in()
    c.post("/annotations", data={"on_date": "2026-03-01", "note": "keep me"})
    note_id = _only_note_id()

    anon = dashboard_client(create_app(conn_factory=connect), authenticated=False)
    r = anon.post("/annotations/delete", data={"id": note_id},
                  follow_redirects=False)
    assert r.status_code == 401
    assert _only_note_id() == note_id


def test_an_anonymous_delete_never_reaches_the_database(db):
    from app_dashboard.db import connect
    c = _signed_in()
    c.post("/annotations", data={"on_date": "2026-03-01", "note": "keep me"})
    note_id = _only_note_id()

    anon = dashboard_client(create_app(conn_factory=connect), authenticated=False)
    r = anon.post("/annotations/delete", data={"id": note_id},
                  follow_redirects=False)
    assert r.status_code in (303, 401, 403)
    assert _only_note_id() == note_id


def test_deleting_an_id_that_is_gone_says_so_rather_than_500ing(db):
    c = _signed_in()
    r = c.post("/annotations/delete", data={"id": 999999}, follow_redirects=False)
    assert r.status_code == 303
    assert "note_error" in r.headers["location"]
    assert "already gone" in unescape(c.get(r.headers["location"]).text)


def test_a_junk_id_comes_back_with_a_message(db):
    c = _signed_in()
    r = c.post("/annotations/delete", data={"id": "seven"}, follow_redirects=False)
    assert r.status_code == 303
    assert "note_error" in r.headers["location"]


def test_the_delete_control_is_available_to_a_signed_in_user(db):
    _signed_in().post("/annotations",
                      data={"on_date": "2026-03-01", "note": "visible to all"})
    assert 'class="anno-del"' in _signed_in().get("/?app=test-app").text


def test_exports_faq_and_tips_are_removed(db):
    c = _signed_in()
    page = c.get("/")
    for label in ("Copy MD", "Download JSON", "Tips"):
        assert label not in page.text
    for path in ("/export.json", "/index.md", "/customers.md", "/faq"):
        assert c.get(path).status_code == 404


# --- Window controls ----------------------------------------------------------

@pytest.mark.parametrize("path,param,good,bad", [
    ("/", "months", 24, 999),
    ("/reports/traffic", "days", 180, 7),
    ("/actions", "trial_days", 30, 1),
    ("/reports/churn", "days", 90, 5),
])
def test_a_window_outside_the_allowlist_falls_back(db, path, param, good, bad):
    """A range control is a number from the query string reaching a query. An
    unknown value falls back to the default rather than 422-ing the page or
    being clamped into an answer nobody asked for."""
    c = _signed_in()
    assert c.get(f"{path}?{param}={good}").status_code == 200
    assert c.get(f"{path}?{param}={bad}").status_code == 200
    assert c.get(f"{path}?{param}=banana").status_code == 200
    assert c.get(f"{path}?{param}=").status_code == 200


def _ga4_row(date, sessions=10, installs=1):
    from app_dashboard.db import connect
    conn = connect()
    conn.execute(
        "insert into ga4_daily (date, dimension, value, sessions, users, "
        "add_app_clicks, installs, ad_clicks) "
        "values (%s, 'total', 'total', %s, %s, 2, %s, 0)",
        (date, sessions, sessions, installs),
    )
    conn.close()


def test_the_window_is_stated_on_the_page(db):
    c = _signed_in()
    assert "MRR, last 24 months" in c.get("/?months=24").text
    assert "MRR, last 12 months" in c.get("/?months=99").text
    # The traffic page shows an empty state rather than a range control when
    # there is nothing to range over, so it needs a row to have a window at all.
    _ga4_row("2026-08-01")
    assert "Last 180 days" in c.get("/reports/traffic?days=180&app=test-app").text
    assert "Last 90 days" in c.get("/reports/traffic?days=7&app=test-app").text


def test_the_headline_tiles_ignore_the_range(db):
    """Installed base and MRR are states, not windows. A control over them would
    be a control that does nothing, so it does not exist and they must not move."""
    c = _signed_in()
    from app_dashboard.metrics import METRICS
    for months in (6, 24):
        assert METRICS["installed"].name in c.get(f"/?months={months}").text


# --- Drill-downs ---------------------------------------------------------------

def test_overview_contains_the_scoped_merchant_search(db):
    body = _signed_in().get("/?app=test-app").text
    assert 'class="merchant-search"' in body
    assert 'action="/customers"' in body
    assert 'hx-get="/customer-search"' in body
    assert 'name="app" value="test-app"' in body


def test_merchant_search_is_scoped_bounded_and_links_to_details(
    db, app_factory, monkeypatch
):
    other = app_factory(slug="other-app", name="Other App")
    monkeypatch.setenv("TOKEN_2", "second-partner-token")
    for index in range(10):
        db.execute(
            """insert into shops
                   (shop_gid, shop_name, shop_domain, install_state, installed_at)
               values (%s, %s, %s, 'installed', '2026-01-01Z')""",
            (
                f"needle-{index}",
                f"Needle Merchant {index}",
                f"needle-{index}.myshopify.com",
            ),
        )
    db.execute(
        """insert into shops
               (app_id, shop_gid, shop_name, shop_domain, install_state)
           values (%s, 'other-needle', 'Other Needle',
                   'other-needle.myshopify.com', 'installed')""",
        (other.id,),
    )
    db.commit()

    c = _signed_in()
    combined = c.get("/customer-search?search=needle")
    assert combined.status_code == 200
    assert combined.text.count("data-merchant-result") == 8
    assert "View all results" in combined.text

    scoped = c.get("/customer-search?search=needle&app=other-app")
    assert "Other Needle" in scoped.text
    assert "Needle Merchant 0" not in scoped.text
    assert "?app=other-app" in scoped.text

    blank = c.get("/customer-search?search=   ")
    assert blank.status_code == 200
    assert "data-merchant-result" not in blank.text


def test_customer_table_shows_current_commercial_state(db):
    db.execute(
        """insert into shops
               (shop_gid, shop_name, shop_domain, install_state, installed_at)
           values ('paid-shop', 'Paid Merchant', 'paid.myshopify.com',
                   'installed', '2026-01-01Z')"""
    )
    db.execute(
        """insert into subscriptions
               (id, shop_gid, monthly_amount, converted_at)
           values ('paid-sub', 'paid-shop', 19.00, '2026-01-02Z')"""
    )
    db.execute(
        "insert into charges (gid, plan_interval) "
        "values ('paid-sub', 'EVERY_30_DAYS')"
    )
    db.execute(
        """insert into app_events
               (platform_event_id, type, occurred_at, shop_gid)
           values ('paid-event', 'subscribed', '2026-01-02Z', 'paid-shop')"""
    )
    db.commit()

    body = unescape(_signed_in().get("/customers").text)
    for heading in ("Merchant", "App", "Plan", "MRR", "Status", "Installed", "Latest event"):
        assert heading in body
    assert "Paid Merchant" in body
    assert "Monthly" in body
    assert "$19.00" in body
    assert "Paying" in body
    assert "Subscribed" in body

def test_country_and_plan_rows_link_into_the_customers_filters(db):
    from app_dashboard.db import connect
    conn = connect()
    conn.execute("insert into shops (shop_gid, install_state, country) "
                 "values ('s1', 'installed', 'United States')")
    conn.execute("insert into subscriptions (id, shop_gid, monthly_amount, converted_at) "
                 "values ('c1', 's1', 19.00, '2026-01-01Z')")
    conn.execute("insert into charges (gid, plan_interval) values ('c1', 'EVERY_30_DAYS')")
    conn.close()
    body = unescape(_signed_in().get("/").text)
    assert "/customers?country=United%20States" in body
    assert "/customers?plan=EVERY_30_DAYS" in body


def test_the_customers_plan_filter_narrows_to_that_interval(db):
    from app_dashboard.db import connect
    conn = connect()
    conn.execute("insert into shops (shop_gid, shop_domain, install_state) "
                 "values ('s1', 'monthly.myshopify.com', 'installed')")
    conn.execute("insert into shops (shop_gid, shop_domain, install_state) "
                 "values ('s2', 'annual.myshopify.com', 'installed')")
    conn.execute("insert into subscriptions (id, shop_gid, monthly_amount, converted_at) "
                 "values ('c1', 's1', 19.00, '2026-01-01Z')")
    conn.execute("insert into subscriptions (id, shop_gid, monthly_amount, converted_at) "
                 "values ('c2', 's2', 15.83, '2026-01-01Z')")
    conn.execute("insert into charges (gid, plan_interval) values ('c1', 'EVERY_30_DAYS')")
    conn.execute("insert into charges (gid, plan_interval) values ('c2', 'ANNUAL')")
    conn.close()
    c = _signed_in()
    annual = c.get("/customers?plan=ANNUAL").text
    assert "annual.myshopify.com" in annual
    assert "monthly.myshopify.com" not in annual
    # An unrecognised plan is no filter at all, never an empty page.
    both = c.get("/customers?plan=NONSENSE").text
    assert "annual.myshopify.com" in both and "monthly.myshopify.com" in both


def test_a_reason_bar_links_to_the_merchants_who_said_it(db):
    from app_dashboard.db import connect
    conn = connect()
    conn.execute("insert into shops (shop_gid, shop_domain, install_state) "
                 "values ('s1', 'gone.myshopify.com', 'uninstalled')")
    conn.execute("insert into raw_app_events (id, type, occurred_at, shop_gid, payload) "
                 "values ('e1', 'RELATIONSHIP_UNINSTALLED', '2026-07-01Z', 's1', '{}')")
    conn.execute("insert into app_events (platform_event_id, type, occurred_at, shop_gid, "
                 "uninstall_reason) values ('e1', 'uninstalled', '2026-07-01Z', 's1', "
                 "'Too expensive')")
    conn.close()
    c = _signed_in()
    assert "/reports/churn?bucket=Too%20expensive" in unescape(c.get("/").text)
    filtered = c.get("/reports/churn?bucket=Too expensive").text
    assert "gone.myshopify.com" in filtered
    assert c.get("/reports/churn?bucket=Nobody said this").status_code == 200


def test_a_cross_origin_annotation_write_is_refused(db):
    """SameSite=lax blocks a cross-*site* post, but "site" is the registrable
    domain: any sibling host under the same domain still gets the cookie
    attached. The cookie alone is therefore not a CSRF defence."""
    c = dashboard_client(create_app(conn_factory=lambda: keep_open(db)))
    c.cookies.set(SESSION_COOKIE,
                  issue_session(SESSION_SECRET, "tester@example.com", "Test User"))
    r = c.post("/annotations", data={"on_date": "2026-03-01", "note": "x"},
               headers={"Origin": "https://evil.example"}, follow_redirects=False)
    assert r.status_code == 403
    from app_dashboard import annotations as anno
    assert anno.recent(db) == []


def test_a_same_origin_annotation_write_still_works(db):
    c = dashboard_client(create_app(conn_factory=lambda: keep_open(db)))
    c.cookies.set(SESSION_COOKIE,
                  issue_session(SESSION_SECRET, "tester@example.com", "Test User"))
    r = c.post("/annotations", data={"on_date": "2026-03-01", "note": "shipped v2"},
               headers={"Origin": "https://dash.test"}, follow_redirects=False)
    assert r.status_code == 303
    from app_dashboard import annotations as anno
    assert [n["note"] for n in anno.recent(db)] == ["shipped v2"]
