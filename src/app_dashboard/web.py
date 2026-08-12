import csv
import io
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from math import ceil
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

# Uvicorn only configures its own loggers; without this, app INFO lines
# (run_sync summaries, Slack skips) never reach the host's logs.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from datetime import date
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app_dashboard.auth import (
    LOGIN_CSRF_COOKIE,
    LOGIN_CSRF_MAX_AGE,
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    display_name,
    issue_login_csrf,
    issue_session,
    read_session,
    valid_login_csrf,
)
from app_dashboard import annotations as anno
from app_dashboard.aso import (
    current_listings,
    install_source_report,
    keyword_report,
    keyword_research,
    listing_history,
    position_history,
    portfolio_report,
)
from app_dashboard.app_store_discovery import discovery_report
from app_dashboard.catalog import AppConfig, list_apps
from app_dashboard.integrations import (
    IntegrationError,
    LIFECYCLE_STATUSES,
    LISTING_STATUSES,
    TRACKING_STATUSES,
    archive_app,
    archive_organization,
    get_app,
    integration_rows,
    list_organizations,
    save_app,
    save_organization,
)
from app_dashboard.config import get_settings
from app_dashboard.customers import (
    PLAN_INTERVALS,
    count_customers,
    customer_detail,
    distinct_facets,
    list_customers,
)
from app_dashboard.db import connect
from app_dashboard.display_time import (
    DISPLAY_TIMEZONE_LABEL,
    format_local_time,
    local_today,
)
from app_dashboard.metrics import COMPARE_LABEL, METRICS, signed
from app_dashboard.manual_sync import (
    InvalidSyncMode,
    ManualSyncCoordinator,
    MODES,
    SyncAlreadyRunning,
)
from app_dashboard.ops import sync_health
from app_dashboard.period_report import (
    build_period_report,
    normalise_sort,
    sort_rows,
)
from app_dashboard.periods import PRESET_LABELS, resolve_period
from app_dashboard.ranges import (
    CHURN_DAYS,
    MONEY_MONTHS,
    TRAFFIC_DAYS,
    TRIAL_DAYS,
    choice,
)
from app_dashboard.scheduler import start_scheduler
from app_dashboard.scope import Scope
from app_dashboard.security import RateLimiter, SecurityHeadersMiddleware, client_key
from app_dashboard.stats import (
    ACTIVITY_TYPES,
    PLAN_LABELS,
    activity_feed,
    annual_upgrade_candidates,
    churn_composition,
    churn_rows,
    collected_revenue,
    country_breakdown,
    funnel_stats,
    install_reconciliation,
    install_retention_cohorts,
    monthly_activity,
    monthly_conversion,
    mrr_movements,
    mrr_trend,
    overview_comparison,
    overview_stats,
    plan_mix,
    recent_events,
    retention_cohorts,
    revenue_by_month,
    review_candidates,
    store_deaths,
    time_to_uninstall,
    traffic_breakdown,
    traffic_monthly,
    traffic_summary,
    trial_watch,
    uninstall_reasons,
    uninstall_verbatims,
    unit_economics,
)
from app_dashboard.trials import current_trials
from app_dashboard.usage import (
    MAX_BODY_BYTES,
    UsageError,
    activation_cohorts,
    at_risk_shops,
    has_usage_data,
    parse_batch,
    time_to_activation,
)
from app_dashboard.usage import ingest as ingest_usage_events

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

CUSTOMERS_PAGE_SIZE = 50

# Header the app sends its shared secret in.
USAGE_TOKEN_HEADER = "X-Usage-Token"


def _same_secret(supplied: str | None, expected: str | None) -> bool:
    """Constant-time compare that survives non-ASCII input.

    secrets.compare_digest raises TypeError on a str containing a codepoint
    above 127, and Starlette decodes request headers as latin-1, so any byte in
    0x80-0xFF (which h11 permits) reaches these comparisons as such a str. That
    turned a wrong credential into a 500 while every wrong *ASCII* credential
    returned 401 -- a one-bit oracle telling an unauthenticated caller whether a
    secret is configured at all. Comparing UTF-8 bytes removes the difference
    without changing what counts as a match.
    """
    if not supplied or not expected:
        return False
    return secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


# Hosts where a weak session secret is tolerated, so local development works
# without ceremony. Compared against the parsed hostname, never as a substring.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}
# token_urlsafe(32) yields 43 characters; 32 leaves room for other generators
# while still being far past guessable.
MIN_SESSION_SECRET_BYTES = 32

# Failed dashboard logins per client address, and failed /ingest/usage calls.
# Generous enough that a person fat-fingering a password never notices, tight
# enough that guessing a shared secret over the network is not worth starting.
LOGIN_LIMIT, LOGIN_WINDOW = 10, 300
INGEST_LIMIT, INGEST_WINDOW = 20, 60


def create_app(conn_factory, manual_sync_coordinator=None) -> FastAPI:
    settings = get_settings()
    sync_coordinator = manual_sync_coordinator or ManualSyncCoordinator(
        conn_factory, settings
    )
    # Fail closed on a weak session secret. This repository is public, so the
    # placeholder in .env.example and the cookie salt are both known to anyone
    # who wants them: a deployment running on either forges any session, for any
    # allowed address, with no credential at all.
    #
    # Checked by LENGTH, not by equality with the placeholder. An equality test
    # catches exactly one bad value out of the infinite set of them, and the
    # likeliest deployment accident is not the placeholder, it is an unset or
    # empty environment variable.
    #
    # Localhost is exempted so `uvicorn --reload` works out of the box. The host
    # is parsed rather than substring-matched: "localhost" appears in
    # https://localhost.evil.com and in https://acme.com/?next=localhost, and a
    # guard that reads those as local is a guard that is not there.
    hostname = (urlparse(settings.public_base_url).hostname or "").lower()
    if hostname not in LOCAL_HOSTS and len(settings.session_secret.strip()) < MIN_SESSION_SECRET_BYTES:
        raise RuntimeError(
            f"SESSION_SECRET is missing or too short while PUBLIC_BASE_URL is "
            f"{settings.public_base_url!r}. It signs every session cookie, and "
            f"a guessable one is a full authentication bypass. Set at least "
            f"{MIN_SESSION_SECRET_BYTES} characters before serving: "
            'python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )

    templates = Jinja2Templates(directory=TEMPLATES_DIR)

    def render_ms(request: Request) -> str:
        """How long this request has taken when the footer renders.

        A Jinja global rather than a value every route has to pass, and read
        during the render rather than after the response, which is the only way
        the number can appear in the page it describes. So it covers the work
        that varies -- auth, the aggregate queries, the template itself -- and
        excludes serialisation and transfer. Empty when the middleware did not
        run, which should not happen; an empty footer beats a fake zero.
        """
        started = getattr(request.state, "started", None)
        if started is None:
            return ""
        ms = (time.perf_counter() - started) * 1000
        # A page with no queries lands under a millisecond, and "0 ms" reads as
        # broken rather than fast.
        return f"{ms:.1f}" if ms < 10 else f"{ms:.0f}"

    templates.env.globals["render_ms"] = render_ms
    # The definition registry, as a global rather than a value every route has
    # to remember to pass. A tile that shows a number it cannot define is the
    # thing this is here to make impossible.
    templates.env.globals["METRICS"] = METRICS
    templates.env.globals["COMPARE_LABEL"] = COMPARE_LABEL
    templates.env.globals["signed"] = signed
    templates.env.globals["DASHBOARD_NAME"] = "Shopify Apps Analytics"
    templates.env.globals["APP_NAME"] = "Shopify Apps"
    templates.env.globals["APP_LISTING_URL"] = ""
    templates.env.globals["GA4_PROPERTY_ID"] = None
    templates.env.globals["DISPLAY_TIMEZONE_LABEL"] = DISPLAY_TIMEZONE_LABEL
    templates.env.filters["local_time"] = format_local_time

    secure_cookies = urlparse(settings.public_base_url).scheme == "https"
    login_limiter = RateLimiter(LOGIN_LIMIT, LOGIN_WINDOW)
    ingest_limiter = RateLimiter(INGEST_LIMIT, INGEST_WINDOW)

    def active_apps(conn) -> list[AppConfig]:
        return list_apps(conn)

    def resolve_scope(
        request: Request, conn
    ) -> tuple[Scope, AppConfig | None, list[AppConfig]]:
        apps = active_apps(conn)
        slug = request.query_params.get("app")
        if not slug:
            return Scope.all(), None, apps
        selected = next((candidate for candidate in apps if candidate.slug == slug), None)
        if selected is None:
            raise HTTPException(status_code=404, detail="Unknown app")
        return Scope.for_app(selected.id), selected, apps

    def page_context(
        request: Request,
        user: str,
        active: str,
        selected: AppConfig | None,
        apps: list[AppConfig],
    ) -> dict:
        retained = [
            (key, value)
            for key, value in request.query_params.multi_items()
            if key not in {"app", "page"}
        ]
        return {
            "user": _display(request, user),
            "active": active,
            "active_apps": apps,
            "selected_app": selected,
            "scope_qs": f"?app={selected.slug}" if selected else "",
            "selector_params": retained,
            "APP_NAME": selected.name if selected else "Shopify Apps",
            "APP_LISTING_URL": selected.listing_url if selected else "",
            "GA4_PROPERTY_ID": selected.ga4_property_id if selected else None,
        }

    def verify_creds(request: Request) -> str:
        """Require the signed session created by the password login form."""
        email = read_session(settings.session_secret,
                             request.cookies.get(SESSION_COOKIE),
                             settings.dashboard_username)
        if email:
            return email

        if "text/html" in request.headers.get("accept", ""):
            code = 303 if request.method not in ("GET", "HEAD") else 307
            raise HTTPException(status_code=code, detail="login",
                                headers={"Location": "/auth/login"})
        raise HTTPException(status_code=401, detail="Unauthorized")

    def _display(request: Request, user: str) -> str:
        """The name the header shows. Never used to decide anything: verify_creds
        has already run and it is keyed on the email."""
        return display_name(settings.session_secret,
                            request.cookies.get(SESSION_COOKIE), user)

    def _session_email(request: Request) -> str | None:
        """The signed-in address, or None if this request came in some other way.

        Used only by the annotation write path, which needs a stronger statement
        than "authenticated": it needs "authenticated by a cookie the browser
        will not send cross-site".
        """
        return read_session(settings.session_secret,
                            request.cookies.get(SESSION_COOKIE),
                            settings.dashboard_username)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scheduler = None
        if not os.environ.get("NO_SCHEDULER"):
            def scheduler_apps():
                catalog_conn = conn_factory()
                try:
                    return active_apps(catalog_conn)
                finally:
                    catalog_conn.close()

            scheduler = start_scheduler(conn_factory, settings, scheduler_apps)
        yield
        if scheduler is not None:
            scheduler.shutdown()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(SecurityHeadersMiddleware)
    # Three error illustrations, and nothing else. Unauthenticated on purpose:
    # they are decoration on pages a signed-out visitor is meant to see, and
    # they carry no data. StaticFiles resolves within the directory, so the
    # mount cannot be walked out of.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/robots.txt", include_in_schema=False)
    def robots():
        # Belt to the X-Robots-Tag braces in security.py. Everything here is
        # behind sign-in except the login and error screens, and those are the
        # ones a crawler could otherwise reach.
        return PlainTextResponse("User-agent: *\nDisallow: /\n")

    # These were raw JSON blobs: the only screens in the app that did not look
    # like the app, and 403 in particular is a human moment (someone signed in
    # with the wrong Google account and got a JSON object back). Only browsers
    # get the HTML. The .md twins, curl, and anything else keep the body they
    # have always had, so nothing that parses a response changes shape.
    RENDERED_STATUSES = (401, 403, 404)

    def _error_page(request: Request, exc: StarletteHTTPException,
                    signed_in: bool):
        """Copy for one error, as (title, body, link href, link text)."""
        if exc.status_code == 401:
            return ("Those credentials were not accepted",
                    "Check the email address and password, then try again.",
                    "/auth/login", "Go to sign-in")
        if exc.status_code == 403:
            return ("Not allowed", str(exc.detail),
                    "/auth/login", "Go to sign-in")
        if str(exc.detail) == "No such shop":
            return ("That shop isn't on record",
                    "Check the domain, or it never installed. Customers is "
                    "searchable by shop name and domain.",
                    "/customers", "Back to Customers")
        if signed_in:
            return ("Page not found",
                    "The link may be stale. Every page in this dashboard is in "
                    "the sidebar.",
                    "/", "Back to Overview")
        return ("Page not found",
                "The link may be stale, or the page is one of the ones behind "
                "sign-in.",
                "/auth/login", "Go to sign-in")

    @app.exception_handler(StarletteHTTPException)
    async def render_http_exception(request: Request,
                                    exc: StarletteHTTPException):
        wants_html = "text/html" in request.headers.get("accept", "")
        if exc.status_code not in RENDERED_STATUSES or not wants_html:
            # Everything else, including the 307 to /auth/login, keeps its
            # existing behaviour.
            return await http_exception_handler(request, exc)
        # The session decides whether the error page may draw authenticated
        # navigation around its message.
        signed_in = bool(read_session(settings.session_secret,
                                      request.cookies.get(SESSION_COOKIE),
                                      settings.dashboard_username))
        title, body, href, text = _error_page(request, exc, signed_in)
        response = templates.TemplateResponse(
            request, "error.html",
            # The header name is display-only here as everywhere else, and an
            # unauthenticated error simply has none.
            {"user": display_name(settings.session_secret,
                                  request.cookies.get(SESSION_COOKIE), ""),
             "active": None, "signed_in": signed_in,
             "art": None if signed_in else f"/static/error-{exc.status_code}.webp",
             "error_code": exc.status_code,
             "title": title, "body": body,
             "link_href": href, "link_text": text},
            status_code=exc.status_code,
        )
        for key, value in (exc.headers or {}).items():
            response.headers[key] = value
        return response

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    def _check_usage_token(request: Request, selected_app: AppConfig) -> None:
        """Constant-time check of the ingest secret.

        Every failure returns the same flat 401 with no detail: a wrong token,
        a missing header, and an unconfigured server are indistinguishable from
        outside, so probing this route tells an attacker nothing.
        """
        key = client_key(request)
        if not ingest_limiter.check(key):
            raise HTTPException(status_code=429, detail="Too many requests")
        if not _same_secret(
            request.headers.get(USAGE_TOKEN_HEADER), selected_app.usage_token
        ):
            ingest_limiter.record(key)
            raise HTTPException(status_code=401, detail="Unauthorized")

    async def _read_capped(request: Request) -> bytes:
        """Read the body, refusing anything past the cap.

        Streamed rather than `await request.body()` so an oversized or lying
        Content-Length is rejected after one chunk instead of being buffered
        whole.
        """
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Payload too large")
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                raise HTTPException(status_code=413, detail="Payload too large")
            chunks.append(chunk)
        return b"".join(chunks)

    @app.get("/sync/status")
    def manual_sync_status(user: str = Depends(verify_creds)):
        return sync_coordinator.status()

    @app.post("/sync")
    async def start_manual_sync(
        request: Request, user: str = Depends(verify_creds)
    ):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != settings.public_base_url.rstrip("/"):
            raise HTTPException(status_code=403, detail="Cross-origin write refused")
        if not request.headers.get("content-type", "").startswith(
            "application/x-www-form-urlencoded"
        ):
            raise HTTPException(status_code=415, detail="Send a form body")
        form = parse_qs((await _read_capped(request)).decode("utf-8", "replace"))
        mode = (form.get("mode") or [""])[0]
        app_slug = (form.get("app") or [""])[0]
        if mode not in MODES:
            raise HTTPException(status_code=400, detail="Unknown sync mode")

        conn = conn_factory()
        try:
            apps = active_apps(conn)
            if app_slug:
                selected = next(
                    (candidate for candidate in apps if candidate.slug == app_slug),
                    None,
                )
                if selected is None:
                    raise HTTPException(status_code=404, detail="Unknown app")
                scoped_apps = [selected]
            else:
                scoped_apps = apps
        finally:
            conn.close()

        try:
            sync_coordinator.start(scoped_apps, mode=mode)
        except InvalidSyncMode:
            raise HTTPException(status_code=400, detail="Unknown sync mode") from None
        except SyncAlreadyRunning:
            raise HTTPException(status_code=409, detail="A sync is already running") from None
        target = "/" + ("?" + urlencode({"app": app_slug}) if app_slug else "")
        return RedirectResponse(target, status_code=303)

    @app.post("/ingest/usage/{app_slug}")
    async def ingest_usage(request: Request, app_slug: str):
        """Product-usage events from the app itself.

        The one route with no interactive auth: it is machine-to-machine, and
        gating it on an SSO session would mean the app could never call it. The
        token check runs before the body is read, so an unauthenticated caller
        cannot make us buffer anything.
        """
        conn = conn_factory()
        try:
            selected_app = next(
                (candidate for candidate in active_apps(conn) if candidate.slug == app_slug),
                None,
            )
        finally:
            conn.close()
        if selected_app is None:
            raise HTTPException(status_code=404, detail="Unknown app")

        _check_usage_token(request, selected_app)
        raw = await _read_capped(request)
        try:
            events = parse_batch(raw, selected_app.usage_event_types)
        except UsageError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from None
        conn = conn_factory()
        try:
            result = ingest_usage_events(conn, selected_app.id, events)
        finally:
            conn.close()
        return result

    @app.get("/auth/login")
    def auth_login(request: Request):
        if read_session(settings.session_secret,
                        request.cookies.get(SESSION_COOKIE),
                        settings.dashboard_username):
            return RedirectResponse("/", status_code=303)
        csrf_token = issue_login_csrf(settings.session_secret)
        response = templates.TemplateResponse(
            request, "login.html",
            {"user": None, "active": None, "signed_in": False,
             "art": "/static/login.webp", "csrf_token": csrf_token,
             "error": None},
        )
        response.set_cookie(
            LOGIN_CSRF_COOKIE, csrf_token, max_age=LOGIN_CSRF_MAX_AGE,
            httponly=True, secure=secure_cookies, samesite="lax",
        )
        return response

    @app.post("/auth/login")
    async def auth_login_submit(request: Request):
        content_type = request.headers.get("content-type", "")
        if not content_type.startswith("application/x-www-form-urlencoded"):
            raise HTTPException(status_code=415, detail="Send a form body")
        form = parse_qs((await _read_capped(request)).decode("utf-8", "replace"),
                        keep_blank_values=True)
        csrf_token = (form.get("csrf_token") or [None])[0]
        if not valid_login_csrf(
            settings.session_secret, csrf_token,
            request.cookies.get(LOGIN_CSRF_COOKIE),
        ):
            raise HTTPException(status_code=400, detail="Invalid login form")

        key = client_key(request)
        if not login_limiter.check(key):
            raise HTTPException(status_code=429, detail="Too many attempts")
        username = (form.get("username") or [""])[0]
        password = (form.get("password") or [""])[0]
        username_ok = _same_secret(username, settings.dashboard_username)
        password_ok = _same_secret(password, settings.dashboard_password)
        if not (username_ok and password_ok):
            login_limiter.record(key)
            return templates.TemplateResponse(
                request, "login.html",
                {"user": None, "active": None, "signed_in": False,
                 "art": "/static/login.webp", "csrf_token": csrf_token,
                 "error": "Incorrect email or password"},
                status_code=401,
            )

        login_limiter.reset(key)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            issue_session(settings.session_secret, settings.dashboard_username),
            max_age=SESSION_MAX_AGE, httponly=True,
            secure=secure_cookies, samesite="lax",
        )
        response.delete_cookie(LOGIN_CSRF_COOKIE)
        logger.info("signed in %s", settings.dashboard_username)
        return response

    @app.get("/auth/logout")
    def auth_logout():
        response = RedirectResponse("/auth/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE)
        return response

    @app.get("/")
    def overview(request: Request, months: str | None = None,
                 user: str = Depends(verify_creds)):
        # Taken as a string and validated, not typed as int: an `int` parameter
        # would make FastAPI return 422 for `?months=banana`, and a range
        # control that can 422 the whole page is worse than one that ignores
        # nonsense. The headline tiles are unaffected by this on purpose --
        # installed base and MRR are states, not windows.
        months = choice(months, MONEY_MONTHS, 12)
        conn = conn_factory()
        try:
            scope, selected_app, apps = resolve_scope(request, conn)
            stats = overview_stats(conn, scope)
            activity = monthly_activity(conn, scope=scope)
            events = recent_events(conn, scope=scope)
            trend = mrr_trend(conn, months, scope)
            movements = mrr_movements(conn, months, scope)
            countries = country_breakdown(conn, scope=scope)
            plans = plan_mix(conn, scope)
            reasons = uninstall_reasons(conn, scope)
            health = sync_health(conn, settings.poll_interval_minutes, scope)
            money = collected_revenue(conn, scope)
            revenue = revenue_by_month(conn, months, scope)
            comparison = overview_comparison(
                conn, {**stats, "net_30d": money["net_30d"]}, scope=scope)
            notes = anno.recent(conn, scope)
            notes_by_month = anno.by_month(conn, scope)
            app_comparison = []
            if selected_app is None:
                for candidate in apps:
                    candidate_scope = Scope.for_app(candidate.id)
                    candidate_stats = overview_stats(conn, candidate_scope)
                    economics = unit_economics(conn, scope=candidate_scope)
                    trial_stats = current_trials(conn, candidate_scope)
                    installed = candidate_stats["installed"]
                    app_comparison.append({
                        "app": candidate,
                        "stats": candidate_stats,
                        "paid_share": (
                            round(100 * candidate_stats["paying"] / installed)
                            if installed else None
                        ),
                        "monthly_churn_pct": economics["monthly_churn_pct"],
                        "ltv": economics["ltv"],
                        "current_trials": trial_stats["count"],
                        "trial_mrr": trial_stats["converting_mrr"],
                    })
        finally:
            conn.close()
        activity_max = max(
            [m["installs"] for m in activity] + [m["uninstalls"] for m in activity] + [1]
        )
        trend_max = max([m["mrr"] for m in trend] + [1])
        # One scale for both halves of the waterfall so a $20 gain and a $20
        # loss draw the same length.
        movement_scale = max(
            [m["new"] + m["reactivation"] + m["expansion"] for m in movements]
            + [-(m["contraction"] + m["churned"]) for m in movements] + [1]
        )
        country_max = max([c["installed"] for c in countries] + [c["ever"] for c in countries] + [1])
        # Gross and net share one scale, so the gap between the two bars is the
        # fee rather than an artefact of scaling them independently.
        revenue_max = max([m["gross"] for m in revenue] + [1])
        return templates.TemplateResponse(
            request,
            "overview.html",
            {**page_context(request, user, "overview", selected_app, apps),
             "stats": stats, "activity": activity,
             "health": health,
             "activity_max": activity_max, "events": events,
             "trend": trend, "trend_max": trend_max,
             "movements": movements, "movement_scale": movement_scale,
             "countries": countries, "country_max": country_max,
             "money": money, "revenue": revenue, "revenue_max": revenue_max,
             "plans": plans, "reasons": reasons["buckets"][:5],
             "comparison": comparison, "months": months,
             "app_comparison": app_comparison,
             "manual_sync": sync_coordinator.status(),
             "month_choices": MONEY_MONTHS,
             "notes": notes, "notes_by_month": notes_by_month,
             "note_max": anno.NOTE_MAX, "today": local_today().isoformat(),
             # Only a cookie session may write. See the POST route.
             "can_annotate": bool(selected_app and _session_email(request)),
             "note_error": request.query_params.get("note_error")},
        )

    async def _browser_form(request: Request) -> tuple[str, dict]:
        """The session, origin gate, and body parser for browser writes.

        Every route that changes operator-owned state uses this boundary.

        SameSite=lax on the cookie blocks a cross-*site* form post, but "site"
        is the registrable domain, not the origin. A page on any sibling host
        (a blog, a staging box, anything under the same domain) still gets the
        cookie attached, so the cookie alone is not a CSRF defence. Hence the
        explicit Origin check below, which is what makes the paragraph above
        true rather than nearly true.

        Returns the verified address and the parsed form. The address is what
        gets stored as `author`; a form field never is.
        """
        email = _session_email(request)
        if not email:
            raise HTTPException(
                status_code=403,
                detail="Changing dashboard data needs a browser session. Sign in first.",
            )
        # Same-origin only. A browser always sends Origin on a cross-origin
        # POST; its absence means a same-origin form or a non-browser client.
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != settings.public_base_url.rstrip("/"):
            raise HTTPException(status_code=403, detail="Cross-origin write refused")
        # Parsed here rather than through FastAPI's Form(), which pulls in
        # python-multipart for a form that has no file input and never will.
        # Starlette handles urlencoded bodies without it; multipart is refused
        # outright so the import can never be reached.
        content_type = request.headers.get("content-type", "")
        if not content_type.startswith("application/x-www-form-urlencoded"):
            raise HTTPException(status_code=415, detail="Send a form body")
        body = await _read_capped(request)
        return email, parse_qs(body.decode("utf-8", "replace"))

    async def _annotation_form(request: Request) -> tuple[str, dict]:
        return await _browser_form(request)

    def _flat_form(form: dict) -> dict[str, str]:
        return {key: (values[0] if values else "") for key, values in form.items()}

    def _management_context(request: Request, user: str, **extra) -> dict:
        conn = conn_factory()
        try:
            apps = active_apps(conn)
        finally:
            conn.close()
        return {
            **page_context(request, user, "management", None, apps),
            **extra,
        }

    @app.get("/management/integrations")
    def manage_integrations(
        request: Request, user: str = Depends(verify_creds)
    ):
        status = request.query_params.get("status", "")
        conn = conn_factory()
        try:
            rows = integration_rows(conn)
            organizations = list_organizations(conn)
        finally:
            conn.close()
        if status:
            rows = [row for row in rows if row["lifecycle_status"] == status]
        return templates.TemplateResponse(
            request,
            "integrations.html",
            _management_context(
                request, user, rows=rows, organizations=organizations,
                status=status, lifecycle_statuses=LIFECYCLE_STATUSES,
            ),
        )

    @app.get("/discover")
    def discover_apps(
        request: Request,
        q: str = "",
        category: str = "",
        page: int = 1,
        user: str = Depends(verify_creds),
    ):
        conn = conn_factory()
        try:
            apps = active_apps(conn)
            report = discovery_report(
                conn, search=q, category=category, page=page
            )
        finally:
            conn.close()
        query = {"q": q.strip(), "category": category.strip()}
        query = {key: value for key, value in query.items() if value}
        return templates.TemplateResponse(
            request,
            "discover.html",
            {
                **page_context(request, user, "discover", None, apps),
                **report,
                "query": q.strip(),
                "selected_category": category.strip(),
                "filter_qs": urlencode(query),
            },
        )

    def _organization_form_response(
        request: Request, user: str, values: dict, *, error: str | None = None
    ):
        return templates.TemplateResponse(
            request,
            "integration_form.html",
            _management_context(
                request, user, kind="organization", values=values, error=error,
                lifecycle_statuses=LIFECYCLE_STATUSES,
            ),
            status_code=422 if error else 200,
        )

    @app.get("/management/organizations/new")
    def new_organization(request: Request, user: str = Depends(verify_creds)):
        return _organization_form_response(
            request, user, {"lifecycle_status": "draft"}
        )

    @app.get("/management/organizations/{org_id}/edit")
    def edit_organization(
        request: Request, org_id: int, user: str = Depends(verify_creds)
    ):
        conn = conn_factory()
        try:
            record = next((r for r in list_organizations(conn) if r.id == org_id), None)
        finally:
            conn.close()
        if record is None:
            raise HTTPException(status_code=404, detail="Unknown organization")
        return _organization_form_response(request, user, record.__dict__)

    @app.post("/management/organizations/save")
    async def store_organization(request: Request, user: str = Depends(verify_creds)):
        _, raw = await _browser_form(request)
        values = _flat_form(raw)
        org_id = int(values["id"]) if values.get("id", "").isdigit() else None
        conn = conn_factory()
        try:
            saved_id = save_organization(conn, values, org_id)
        except IntegrationError as exc:
            return _organization_form_response(request, user, values, error=str(exc))
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown organization") from None
        finally:
            conn.close()
        return RedirectResponse(
            f"/management/organizations/{saved_id}/edit?saved=1", status_code=303
        )

    @app.post("/management/organizations/{org_id}/archive")
    async def remove_organization(
        request: Request, org_id: int, user: str = Depends(verify_creds)
    ):
        await _browser_form(request)
        conn = conn_factory()
        try:
            archive_organization(conn, org_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown organization") from None
        finally:
            conn.close()
        return RedirectResponse("/management/integrations", status_code=303)

    def _app_form_response(
        request: Request, user: str, values: dict, *, error: str | None = None
    ):
        conn = conn_factory()
        try:
            organizations = [org for org in list_organizations(conn) if not org.archived]
        finally:
            conn.close()
        return templates.TemplateResponse(
            request,
            "integration_form.html",
            _management_context(
                request, user, kind="app", values=values, organizations=organizations,
                error=error, lifecycle_statuses=LIFECYCLE_STATUSES,
                listing_statuses=LISTING_STATUSES,
                tracking_statuses=TRACKING_STATUSES,
            ),
            status_code=422 if error else 200,
        )

    @app.get("/management/apps/new")
    def new_managed_app(request: Request, user: str = Depends(verify_creds)):
        return _app_form_response(request, user, {
            "lifecycle_status": "draft", "listing_status": "unknown",
            "tracking_status": "unknown", "listing_locales": ["en"],
            "annual_plan_amounts": [],
        })

    @app.get("/management/apps/{app_id}/edit")
    def edit_managed_app(
        request: Request, app_id: int, user: str = Depends(verify_creds)
    ):
        conn = conn_factory()
        try:
            values = get_app(conn, app_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown app") from None
        finally:
            conn.close()
        return _app_form_response(request, user, values)

    @app.post("/management/apps/save")
    async def store_managed_app(request: Request, user: str = Depends(verify_creds)):
        _, raw = await _browser_form(request)
        values = _flat_form(raw)
        app_id = int(values["id"]) if values.get("id", "").isdigit() else None
        conn = conn_factory()
        try:
            saved_id = save_app(conn, values, app_id)
        except IntegrationError as exc:
            return _app_form_response(request, user, values, error=str(exc))
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown app") from None
        finally:
            conn.close()
        return RedirectResponse(
            f"/management/apps/{saved_id}/edit?saved=1", status_code=303
        )

    @app.post("/management/apps/{app_id}/archive")
    async def remove_managed_app(
        request: Request, app_id: int, user: str = Depends(verify_creds)
    ):
        await _browser_form(request)
        conn = conn_factory()
        try:
            archive_app(conn, app_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown app") from None
        finally:
            conn.close()
        return RedirectResponse("/management/integrations", status_code=303)

    @app.get("/management/runbook")
    def management_runbook(request: Request, user: str = Depends(verify_creds)):
        conn = conn_factory()
        try:
            rows = integration_rows(conn)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request, "runbook.html",
            _management_context(request, user, rows=rows),
        )

    def _back_to_notes(
        selected_app: AppConfig, error: str | None = None
    ) -> RedirectResponse:
        params = {"app": selected_app.slug}
        if error:
            params["note_error"] = error
        return RedirectResponse(
            "/?" + urlencode(params) + "#annotations", status_code=303
        )

    @app.post("/annotations")
    async def add_annotation(request: Request,
                             user: str = Depends(verify_creds)):
        """Record why a number moved."""
        email, form = await _annotation_form(request)
        on_date = (form.get("on_date") or [""])[0]
        note = (form.get("note") or [""])[0]
        app_slug = (form.get("app") or [request.query_params.get("app", "")])[0]

        conn = conn_factory()
        try:
            apps = active_apps(conn)
            selected_app = next(
                (candidate for candidate in apps if candidate.slug == app_slug), None
            )
            if selected_app is None and not app_slug and len(apps) == 1:
                selected_app = apps[0]
            if selected_app is None:
                raise HTTPException(status_code=404, detail="Unknown app")
            anno.add(
                conn,
                app_id=selected_app.id,
                on_date=on_date,
                note=note,
                author=email,
            )
        except anno.AnnotationError as exc:
            return _back_to_notes(selected_app, str(exc))
        finally:
            conn.close()
        return _back_to_notes(selected_app)

    @app.post("/annotations/delete")
    async def delete_annotation(request: Request,
                                user: str = Depends(verify_creds)):
        """Remove a note that was wrong.

        A separate POST route rather than a link, because a GET that deletes is
        one link prefetcher away from clearing the table. The template puts the
        button behind a `<details>` disclosure so it takes two deliberate
        clicks; that confirmation is native HTML because the CSP grants no
        inline script and an `onclick` confirm would never run.
        """
        _, form = await _annotation_form(request)
        annotation_id = (form.get("id") or [""])[0]
        app_slug = (form.get("app") or [request.query_params.get("app", "")])[0]

        conn = conn_factory()
        try:
            apps = active_apps(conn)
            selected_app = next(
                (candidate for candidate in apps if candidate.slug == app_slug), None
            )
            if selected_app is None and not app_slug and len(apps) == 1:
                selected_app = apps[0]
            if selected_app is None:
                raise HTTPException(status_code=404, detail="Unknown app")
            gone = anno.remove(
                conn, app_id=selected_app.id, annotation_id=annotation_id
            )
        except anno.AnnotationError as exc:
            return _back_to_notes(selected_app, str(exc))
        finally:
            conn.close()
        if gone is None:
            # Someone else deleted it first, or the page was stale. Nothing to
            # fix and nothing lost, so this is not an error the reader caused.
            return _back_to_notes(selected_app, "That note was already gone.")
        return _back_to_notes(selected_app)

    @app.get("/customers")
    def customers(
        request: Request,
        industry: str | None = None,
        country: str | None = None,
        search: str | None = None,
        install_state: str | None = None,
        plan: str | None = None,
        source: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        user: str = Depends(verify_creds),
    ):
        filters = {
            "industry": industry or None,
            "country": country or None,
            "search": search or None,
            "install_state": install_state or None,
            # Whitelisted in customers._filters; anything else falls through to
            # no filter rather than to an empty page.
            "plan": plan or None,
            "source": source or None,
            "keyword": keyword or None,
        }
        conn = conn_factory()
        try:
            scope, selected_app, apps = resolve_scope(request, conn)
            total = count_customers(conn, **filters, scope=scope)
            page = max(1, min(page, max(1, ceil(total / CUSTOMERS_PAGE_SIZE))))
            rows = list_customers(
                conn, **filters,
                limit=CUSTOMERS_PAGE_SIZE,
                offset=(page - 1) * CUSTOMERS_PAGE_SIZE,
                scope=scope,
            )
            facets = distinct_facets(conn, scope)
        finally:
            conn.close()
        # Prev/next must carry the active filters, so build the query string
        # once here rather than reassembling it in the template.
        query_values = {k: v for k, v in filters.items() if v}
        if selected_app:
            query_values["app"] = selected_app.slug
        base_qs = urlencode(query_values)
        return templates.TemplateResponse(
            request,
            "customers.html",
            {
                **page_context(request, user, "customers", selected_app, apps),
                "rows": rows,
                "facets": facets,
                "industry": industry or "",
                "country": country or "",
                "search": search or "",
                "install_state": install_state or "",
                "plan": plan or "",
                "source": source or "",
                "keyword": keyword or "",
                "plan_choices": [(v, PLAN_LABELS.get(v, v)) for v in PLAN_INTERVALS],
                "page": page,
                "pages": max(1, ceil(total / CUSTOMERS_PAGE_SIZE)),
                "total": total,
                "first_row": (page - 1) * CUSTOMERS_PAGE_SIZE + 1 if total else 0,
                "last_row": (page - 1) * CUSTOMERS_PAGE_SIZE + len(rows),
                "base_qs": base_qs + "&" if base_qs else "",
            },
        )

    @app.get("/customer-search")
    def customer_search(
        request: Request,
        search: str | None = None,
        user: str = Depends(verify_creds),
    ):
        query = (search or "").strip()
        conn = conn_factory()
        try:
            scope, selected_app, _ = resolve_scope(request, conn)
            matches = (
                list_customers(conn, search=query, limit=9, scope=scope)
                if query else []
            )
        finally:
            conn.close()
        query_values = {"search": query}
        if selected_app:
            query_values["app"] = selected_app.slug
        return templates.TemplateResponse(
            request,
            "_merchant_search_results.html",
            {
                "search": query,
                "rows": matches[:8],
                "has_more": len(matches) > 8,
                "selected_app": selected_app,
                "all_results_url": "/customers?" + urlencode(query_values),
            },
        )

    @app.get("/activiteit", include_in_schema=False)
    def legacy_activity(request: Request, user: str = Depends(verify_creds)):
        del user
        target = "/activity"
        if request.url.query:
            target += f"?{request.url.query}"
        return RedirectResponse(target, status_code=308)

    @app.get("/activity")
    def activity(
        request: Request,
        on: str | None = None,
        event_type: str | None = None,
        page: str | None = None,
        user: str = Depends(verify_creds),
    ):
        try:
            on_date = date.fromisoformat(on) if on else None
        except ValueError:
            on_date = None
        event_type = event_type if event_type in ACTIVITY_TYPES else None
        page_number = int(page) if page and page.isdigit() else 1

        conn = conn_factory()
        try:
            scope, selected_app, apps = resolve_scope(request, conn)
            feed = activity_feed(
                conn, scope=scope, on=on_date, event_type=event_type,
                page=page_number,
            )
        finally:
            conn.close()

        filters = {}
        if selected_app:
            filters["app"] = selected_app.slug
        if on_date:
            filters["on"] = on_date.isoformat()
        if event_type:
            filters["event_type"] = event_type
        base_qs = urlencode(filters)
        labels = {kind: kind.replace("_", " ").title() for kind in ACTIVITY_TYPES}
        return templates.TemplateResponse(
            request,
            "activity.html",
            {
                **page_context(request, user, "activity", selected_app, apps),
                "feed": feed,
                "on": on_date.isoformat() if on_date else "",
                "event_type": event_type or "",
                "event_types": ACTIVITY_TYPES,
                "event_labels": labels,
                "base_qs": base_qs + "&" if base_qs else "",
            },
        )

    @app.get("/period")
    def period_report(
        request: Request,
        period: str | None = None,
        start: str | None = None,
        end: str | None = None,
        sort: str | None = None,
        direction: str | None = None,
        user: str = Depends(verify_creds),
    ):
        selection = resolve_period(period, start, end)
        sort_key, sort_direction = normalise_sort(sort, direction)
        conn = conn_factory()
        try:
            scope, selected_app, apps = resolve_scope(request, conn)
            report = build_period_report(
                conn,
                apps,
                selection.start,
                selection.end,
                selection.previous_start,
                selection.previous_end,
                scope,
            )
        finally:
            conn.close()

        def report_url(items: list[tuple[str, str]]) -> str:
            query = list(items)
            if selected_app:
                query.append(("app", selected_app.slug))
            return "/period?" + urlencode(query)

        period_items = list(selection.query_items())
        preset_links = [
            {
                "key": key,
                "label": label,
                "href": report_url([("period", key)]),
                "active": selection.preset == key and not selection.error,
            }
            for key, label in PRESET_LABELS.items()
            if key != "custom"
        ]
        columns = (
            ("app", "App"),
            ("installs", "Installs"),
            ("uninstalls", "Uninstalls"),
            ("net_installs", "Net installs"),
            ("mrr_gained", "MRR gained"),
            ("mrr_lost", "MRR lost"),
            ("net_mrr", "Net MRR"),
            ("collected", "Net collected"),
        )
        sort_links = {}
        for key, _ in columns:
            next_direction = (
                "asc" if key == sort_key and sort_direction == "desc" else "desc"
            )
            sort_links[key] = report_url(
                period_items
                + [("sort", key), ("direction", next_direction)]
            )

        return templates.TemplateResponse(
            request,
            "period.html",
            {
                **page_context(request, user, "period", selected_app, apps),
                "period": selection,
                "report": report,
                "rows": sort_rows(report.rows, sort_key, sort_direction),
                "preset_links": preset_links,
                "columns": columns,
                "sort_links": sort_links,
                "sort_key": sort_key,
                "sort_direction": sort_direction,
                "period_qs": urlencode(period_items),
            },
        )

    @app.get("/trials")
    def trials(request: Request, user: str = Depends(verify_creds)):
        conn = conn_factory()
        try:
            scope, selected_app, apps = resolve_scope(request, conn)
            report = current_trials(conn, scope)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request,
            "trials.html",
            {
                **page_context(request, user, "trials", selected_app, apps),
                "trials": report,
            },
        )

    @app.get("/aso")
    def aso_report(
        request: Request,
        period: str | None = None,
        start: str | None = None,
        end: str | None = None,
        view: str | None = None,
        search_type: str | None = None,
        locale: str | None = None,
        country: str | None = None,
        device: str | None = None,
        source: str | None = None,
        keyword: str | None = None,
        focus: str | None = None,
        user: str = Depends(verify_creds),
    ):
        selection = resolve_period(period, start, end)
        safe_view = view if view in {
            "overview", "keywords", "sources", "listing", "research"
        } else "overview"
        facets = {
            key: value
            for key, value in {
                "search_type": search_type, "locale": locale, "country": country,
                "device": device, "source": source, "keyword": keyword,
            }.items()
            if value
        }
        conn = conn_factory()
        try:
            _, selected_app, apps = resolve_scope(request, conn)
            portfolio = portfolio_report(conn, apps, selection)
            keywords = (
                keyword_report(conn, selected_app.id, selection, facets)
                if selected_app else None
            )
            sources = (
                install_source_report(conn, selected_app.id, selection, facets)
                if selected_app else ()
            )
            listings = current_listings(conn, selected_app.id) if selected_app else ()
            listing_changes = (
                listing_history(conn, selected_app.id, locale) if selected_app else ()
            )
            research = (
                keyword_research(conn, selected_app.id, keyword) if selected_app else ()
            )
            history_rows = (
                position_history(conn, selected_app.id, focus, selection, facets)
                if selected_app and focus else ()
            )
            capability_rows = conn.execute(
                """select source, status, checked_at, error_code
                   from aso_source_capabilities where app_id=%s""",
                (selected_app.id,),
            ).fetchall() if selected_app else []
            capabilities = {
                row[0]: {"status": row[1], "checked_at": row[2], "error": row[3]}
                for row in capability_rows
            }
            research_state = conn.execute(
                "select last_run_at from operations_state where source='aso_popular_keywords'"
            ).fetchone()
            capabilities["aso_research"] = {
                "status": "ready" if research_state else "pending",
                "checked_at": research_state[0] if research_state else None,
                "error": None,
            }
            facet_rows = conn.execute(
                """select distinct locale, country, device, search_type
                   from aso_keyword_daily
                   where app_id=%s and btrim(keyword) <> ''""",
                (selected_app.id,),
            ).fetchall() if selected_app else []
        finally:
            conn.close()

        def href(**changes):
            values = dict(selection.query_items())
            if selected_app:
                values["app"] = selected_app.slug
            values.update(facets)
            values["view"] = safe_view
            for key, value in changes.items():
                if value is None:
                    values.pop(key, None)
                else:
                    values[key] = value
            return "/aso?" + urlencode(values)

        preset_links = [
            {"key": key, "label": label, "href": href(period=key, start=None, end=None),
             "active": selection.preset == key and not selection.error}
            for key, label in PRESET_LABELS.items() if key != "custom"
        ]
        facet_options = {
            "locale": sorted({row[0] for row in facet_rows if row[0]}),
            "country": sorted({row[1] for row in facet_rows if row[1]}),
            "device": sorted({row[2] for row in facet_rows if row[2]}),
            "search_type": sorted({row[3] for row in facet_rows if row[3]}),
        }
        return templates.TemplateResponse(
            request,
            "aso.html",
            {
                **page_context(request, user, "aso", selected_app, apps),
                "period": selection, "preset_links": preset_links,
                "period_qs": urlencode(selection.query_items()),
                "portfolio": portfolio, "keyword_report": keywords,
                "source_rows": sources, "view": safe_view, "facets": facets,
                "listings": listings, "listing_changes": listing_changes,
                "research_rows": research,
                "history_rows": history_rows, "focus": focus or "",
                "facet_options": facet_options, "capabilities": capabilities,
                "tab_url": lambda tab: href(view=tab),
            },
        )

    @app.get("/aso.csv")
    def aso_csv(
        request: Request,
        period: str | None = None,
        start: str | None = None,
        end: str | None = None,
        user: str = Depends(verify_creds),
    ):
        del user
        selection = resolve_period(period, start, end)
        conn = conn_factory()
        try:
            _, selected_app, _ = resolve_scope(request, conn)
            if selected_app is None:
                raise HTTPException(status_code=400, detail="Select one app for CSV export")
            report = keyword_report(conn, selected_app.id, selection)
        finally:
            conn.close()
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow([
            "keyword", "users", "install_clicks", "conversion_pct",
            "average_position", "latest_position", "position_change",
            "opportunity_score",
        ])
        for row in report.rows:
            writer.writerow([
                row.keyword, row.users, row.install_clicks, row.conversion_pct,
                row.average_position, row.latest_position, row.position_change,
                row.opportunity_score,
            ])
        return Response(
            buffer.getvalue(), media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition":
                    f'attachment; filename="aso-{selected_app.slug}.csv"'
            },
        )

    def _detail_or_404(request: Request, shop_gid: str) -> tuple[dict, AppConfig | None, list[AppConfig]]:
        conn = conn_factory()
        try:
            scope, selected_app, apps = resolve_scope(request, conn)
            detail = customer_detail(conn, shop_gid, scope)
            if detail is None:
                raise HTTPException(status_code=404, detail="No such shop")
        finally:
            conn.close()
        return detail, selected_app, apps

    @app.get("/customers/{shop_gid:path}")
    def customer(request: Request, shop_gid: str,
                 user: str = Depends(verify_creds)):
        detail, selected_app, apps = _detail_or_404(request, shop_gid)
        return templates.TemplateResponse(
            request, "customer.html",
            {**page_context(request, user, "customers", selected_app, apps), **detail},
        )

    @app.get("/actions")
    def actions(request: Request, trial_days: str | None = None,
                user: str = Depends(verify_creds)):
        # Only trial watch takes a window. The other two lists are business
        # rules -- 30 days paying, 3 months on monthly -- and a control over
        # those would change who is on the call sheet rather than how much of
        # it you can see, which is a different thing wearing the same clothes.
        trial_days = choice(trial_days, TRIAL_DAYS, 14)
        conn = conn_factory()
        try:
            scope, selected_app, apps = resolve_scope(request, conn)
            review = review_candidates(conn, scope=scope)
            annual = annual_upgrade_candidates(conn, scope=scope)
            trial = trial_watch(conn, trial_days, scope)
            tracking = bool(selected_app) and has_usage_data(conn, selected_app)
            at_risk = at_risk_shops(conn, selected_app) if tracking else []
        finally:
            conn.close()
        return templates.TemplateResponse(
            request, "actions.html",
            {**page_context(request, user, "actions", selected_app, apps),
             "review": review, "annual": annual,
             "trial": trial, "at_risk": at_risk, "tracking": tracking,
             "trial_days": trial_days, "trial_choices": TRIAL_DAYS},
        )

    @app.get("/reports/funnel")
    def funnel(request: Request, user: str = Depends(verify_creds)):
        conn = conn_factory()
        try:
            scope, selected_app, apps = resolve_scope(request, conn)
            data = funnel_stats(conn, scope)
            monthly = monthly_conversion(conn, scope=scope)
            tracking = bool(selected_app) and has_usage_data(conn, selected_app)
            activation = activation_cohorts(conn, selected_app) if tracking else []
            activation_summary = (
                time_to_activation(conn, selected_app) if tracking else None
            )
        finally:
            conn.close()
        return templates.TemplateResponse(
            request, "funnel.html",
            {**page_context(request, user, "funnel", selected_app, apps),
             "funnel": data, "monthly": monthly,
             "tracking": tracking, "activation": activation,
             "activation_summary": activation_summary},
        )

    @app.get("/reports/churn")
    def churn(request: Request, paid: str | None = None, reason: str | None = None,
              bucket: str | None = None, days: str | None = None,
              user: str = Depends(verify_creds)):
        # None means all time, which is the default here rather than a window:
        # the bars above the table are all-time by construction, and defaulting
        # the table to 90 days would have it disagree with them on load.
        days = choice(days, CHURN_DAYS, None)
        conn = conn_factory()
        try:
            scope, selected_app, apps = resolve_scope(request, conn)
            rows = churn_rows(conn, paid=paid, gave_reason=reason,
                              bucket=bucket or None, since_days=days, scope=scope)
            reasons = uninstall_reasons(conn, scope)
            timing = time_to_uninstall(conn, scope)
            composition = churn_composition(conn, scope)
            deaths = store_deaths(conn, scope=scope)
            verbatims = uninstall_verbatims(conn, scope=scope)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request, "churn.html",
            {**page_context(request, user, "churn", selected_app, apps),
             "rows": rows, "reasons": reasons,
             "timing": timing, "composition": composition, "deaths": deaths,
             "verbatims": verbatims,
             "paid": paid or "", "gave_reason": reason or "",
             "bucket": bucket or "", "days": days, "day_choices": CHURN_DAYS},
        )

    @app.get("/reports/retention")
    def retention(request: Request, user: str = Depends(verify_creds)):
        conn = conn_factory()
        try:
            scope, selected_app, apps = resolve_scope(request, conn)
            data = retention_cohorts(conn, scope=scope)
            installs = install_retention_cohorts(conn, scope=scope)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request, "retention.html",
            {**page_context(request, user, "retention", selected_app, apps),
             "retention": data, "installs": installs},
        )

    @app.get("/reports/traffic")
    def traffic(request: Request, days: str | None = None,
                user: str = Depends(verify_creds)):
        # One window for the tiles, the reconciliation and the breakdowns, so
        # the conversion rates on this page are always computed over the same
        # traffic they are describing. The by-month chart keeps its own 12
        # months: it is the history, not the window.
        days = choice(days, TRAFFIC_DAYS, 90)
        conn = conn_factory()
        try:
            _, selected_app, apps = resolve_scope(request, conn)
            if selected_app:
                summary = traffic_summary(conn, selected_app.id, days)
                reconciliation = install_reconciliation(conn, selected_app.id, days)
                monthly = traffic_monthly(conn, selected_app.id)
                breakdowns = {
                    key: traffic_breakdown(conn, selected_app.id, key, days)
                    for key in ("channel", "source", "country", "language")
                }
            else:
                summary = {}
                reconciliation = {}
                monthly = []
                breakdowns = {}
        finally:
            conn.close()
        monthly_max = max([m["sessions"] for m in monthly] + [1])
        return templates.TemplateResponse(
            request, "traffic.html",
            {**page_context(request, user, "traffic", selected_app, apps),
             "summary": summary, "monthly": monthly,
             "reconciliation": reconciliation,
             "monthly_max": monthly_max, "breakdowns": breakdowns,
             "days": days, "day_choices": TRAFFIC_DAYS,
             "needs_app": selected_app is None},
        )

    return app


# Production entrypoint for `uvicorn app_dashboard.web:app`. No prior task wired this up;
# `create_app` alone isn't importable as an ASGI target.
app = create_app(connect)
