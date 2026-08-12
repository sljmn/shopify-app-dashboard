"""Trusted contact capture and native Shopify review-prompt decisions."""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg

from app_dashboard.catalog import AppConfig
from app_dashboard.usage import SHOP_GID_RE, UsageError

USER_ID_RE = re.compile(r"^\d{1,32}$")
MAX_CONTACT_BODY = 32_768
MAX_OUTCOME_MESSAGE = 500
DECISION_TTL = timedelta(minutes=15)

ALLOWED_CODES = {
    "success", "cancelled", "cooldown-period", "annual-limit-reached",
    "recently-installed", "mobile-app", "already-reviewed",
    "merchant-ineligible", "already-open", "open-in-progress",
}
TRANSIENT_CODES = {"mobile-app", "already-open", "open-in-progress"}


@dataclass(frozen=True)
class ReviewDecision:
    decision_id: str
    expires_at: datetime


def _json_object(raw: bytes, *, limit: int = MAX_CONTACT_BODY) -> dict:
    if len(raw) > limit:
        raise UsageError(413, "body is too large")
    try:
        value = json.loads(raw)
    except Exception:
        raise UsageError(400, "body is not valid JSON") from None
    if not isinstance(value, dict):
        raise UsageError(422, "body must be a JSON object")
    return value


def _text(value, name: str, *, required: bool = False, limit: int = 320) -> str | None:
    if value is None or value == "":
        if required:
            raise UsageError(422, f"{name} is required")
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise UsageError(422, f"{name} must be a string under {limit} characters")
    return value.strip() or None


def _timestamp(value, name: str) -> datetime:
    if not isinstance(value, str):
        raise UsageError(422, f"{name} must be an ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise UsageError(422, f"{name} is not a valid ISO 8601 timestamp") from None
    if parsed.tzinfo is None:
        raise UsageError(422, f"{name} must carry a timezone offset")
    return parsed


def parse_contact(raw: bytes) -> dict:
    payload = _json_object(raw)
    allowed = {
        "shop_gid", "shop_domain", "kind", "shopify_user_id", "first_name",
        "last_name", "email", "email_verified", "locale", "account_owner",
        "collaborator", "access_level", "partner_development", "seen_at",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise UsageError(422, f"unknown contact fields: {', '.join(sorted(unknown))}")
    gid = _text(payload.get("shop_gid"), "shop_gid", required=True, limit=100)
    if not SHOP_GID_RE.fullmatch(gid or ""):
        raise UsageError(422, "shop_gid must be a Shopify shop GID")
    kind = _text(payload.get("kind"), "kind", required=True, limit=10)
    if kind not in {"shop", "staff"}:
        raise UsageError(422, "kind must be shop or staff")
    user_id = _text(payload.get("shopify_user_id"), "shopify_user_id", limit=32)
    if kind == "staff" and not USER_ID_RE.fullmatch(user_id or ""):
        raise UsageError(422, "staff contacts require a numeric shopify_user_id")
    if kind == "shop" and user_id:
        raise UsageError(422, "shop contacts cannot have shopify_user_id")
    partner_development = payload.get("partner_development")
    if partner_development is not None and not isinstance(partner_development, bool):
        raise UsageError(422, "partner_development must be a boolean")
    if kind == "staff" and partner_development is not None:
        raise UsageError(422, "partner_development belongs on the shop contact")
    verified = payload.get("email_verified") is True
    email = _text(payload.get("email"), "email") if verified else None
    return {
        "shop_gid": gid,
        "shop_domain": (_text(payload.get("shop_domain"), "shop_domain") or "").lower() or None,
        "kind": kind,
        "shopify_user_id": user_id,
        "first_name": _text(payload.get("first_name"), "first_name"),
        "last_name": _text(payload.get("last_name"), "last_name"),
        "email": email.lower() if email else None,
        "email_verified": verified,
        "locale": _text(payload.get("locale"), "locale", limit=40),
        "account_owner": payload.get("account_owner") if isinstance(payload.get("account_owner"), bool) else None,
        "collaborator": payload.get("collaborator") if isinstance(payload.get("collaborator"), bool) else None,
        "access_level": _text(payload.get("access_level"), "access_level", limit=40),
        "partner_development": partner_development,
        "seen_at": _timestamp(payload.get("seen_at"), "seen_at"),
    }


def upsert_contact(conn: psycopg.Connection, app_id: int, contact: dict) -> None:
    conflict = (
        "(app_id, shop_gid) where kind = 'shop'" if contact["kind"] == "shop"
        else "(app_id, shop_gid, shopify_user_id) where kind = 'staff'"
    )
    conn.execute(
        f"""insert into merchant_contacts
            (app_id, shop_gid, shop_domain, kind, shopify_user_id, first_name,
             last_name, email, email_verified, locale, account_owner, collaborator,
             access_level, first_seen_at, last_seen_at)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict {conflict} do update set
              shop_domain=coalesce(excluded.shop_domain, merchant_contacts.shop_domain),
              first_name=coalesce(excluded.first_name, merchant_contacts.first_name),
              last_name=coalesce(excluded.last_name, merchant_contacts.last_name),
              email=coalesce(excluded.email, merchant_contacts.email),
              email_verified=merchant_contacts.email_verified or excluded.email_verified,
              locale=coalesce(excluded.locale, merchant_contacts.locale),
              account_owner=coalesce(excluded.account_owner, merchant_contacts.account_owner),
              collaborator=coalesce(excluded.collaborator, merchant_contacts.collaborator),
              access_level=coalesce(excluded.access_level, merchant_contacts.access_level),
              first_seen_at=least(merchant_contacts.first_seen_at, excluded.first_seen_at),
              last_seen_at=greatest(merchant_contacts.last_seen_at, excluded.last_seen_at),
              updated_at=now()""",
        (app_id, contact["shop_gid"], contact["shop_domain"], contact["kind"],
         contact["shopify_user_id"], contact["first_name"], contact["last_name"],
         contact["email"], contact["email_verified"], contact["locale"],
         contact["account_owner"], contact["collaborator"], contact["access_level"],
         contact["seen_at"], contact["seen_at"]),
    )
    if contact["kind"] == "shop" and contact["partner_development"] is not None:
        conn.execute(
            """insert into shops
                   (app_id, shop_gid, shop_domain, partner_development, updated_at)
               values (%s, %s, %s, %s, now())
               on conflict (app_id, shop_gid) do update set
                   shop_domain=coalesce(shops.shop_domain, excluded.shop_domain),
                   partner_development=excluded.partner_development,
                   updated_at=now()""",
            (app_id, contact["shop_gid"], contact["shop_domain"],
             contact["partner_development"]),
        )
    conn.commit()


def redact_contacts(conn: psycopg.Connection, app_id: int, shop_gid: str) -> int:
    with conn.transaction():
        deleted = conn.execute(
            "delete from merchant_contacts where app_id=%s and shop_gid=%s",
            (app_id, shop_gid),
        ).rowcount
        conn.execute(
            """update review_prompt_decisions set response_message=null
               where app_id=%s and shop_gid=%s""", (app_id, shop_gid),
        )
    return deleted


def issue_review_decision(
    conn: psycopg.Connection, app: AppConfig, *, shop_gid: str, event_id: str,
    event_type: str, now: datetime | None = None,
) -> ReviewDecision | None:
    now = now or datetime.now(timezone.utc)
    if not app.review_prompt_enabled or event_type != app.review_trigger_event:
        return None
    with conn.transaction():
        shop = conn.execute(
            """select installed_at, partner_development
               from shops where app_id=%s and shop_gid=%s
               for update""", (app.id, shop_gid),
        ).fetchone()
        if not shop or not shop[0] or shop[0] > now - timedelta(hours=app.review_min_install_hours):
            return None
        if shop[1] is True:
            return None
        if conn.execute(
            "select 1 from review_prompt_suppressions where app_id=%s and shop_gid=%s",
            (app.id, shop_gid),
        ).fetchone():
            return None
        if conn.execute(
            """select 1 from review_prompt_decisions
               where app_id=%s and shop_gid=%s and event_id=%s""",
            (app.id, shop_gid, event_id),
        ).fetchone():
            return None
        successes = conn.execute(
            """select count(*) from usage_events
               where app_id=%s and shop_gid=%s and event_type=%s""",
            (app.id, shop_gid, app.review_trigger_event),
        ).fetchone()[0]
        if successes < app.review_min_success_count:
            return None
        prior = conn.execute(
            """select outcome, response_code, next_eligible_at, expires_at
               from review_prompt_decisions where app_id=%s and shop_gid=%s
               order by issued_at desc limit 1""", (app.id, shop_gid),
        ).fetchone()
        if prior and ((prior[0] == "issued" and prior[3] > now)
                      or prior[0] in {"already_reviewed", "ineligible"}
                      or (prior[2] and prior[2] > now)):
            return None
        shown = conn.execute(
            """select count(*) from review_prompt_decisions
               where app_id=%s and shop_gid=%s and outcome='shown'
                 and issued_at >= %s""",
            (app.id, shop_gid, now - timedelta(days=365)),
        ).fetchone()[0]
        if shown >= app.review_annual_cap:
            return None
        decision_id = secrets.token_urlsafe(32)
        expires_at = now + DECISION_TTL
        conn.execute(
            """insert into review_prompt_decisions
               (decision_id, app_id, shop_gid, event_id, event_type, issued_at, expires_at)
               values (%s,%s,%s,%s,%s,%s,%s)""",
            (decision_id, app.id, shop_gid, event_id, event_type, now, expires_at),
        )
    return ReviewDecision(decision_id, expires_at)


def parse_outcome(raw: bytes) -> dict:
    payload = _json_object(raw)
    if set(payload) - {"decision_id", "success", "code", "message"}:
        raise UsageError(422, "outcome contains unknown fields")
    decision_id = _text(payload.get("decision_id"), "decision_id", required=True, limit=100)
    if not isinstance(payload.get("success"), bool):
        raise UsageError(422, "success must be boolean")
    code = _text(payload.get("code"), "code", required=True, limit=60)
    if code not in ALLOWED_CODES:
        raise UsageError(422, "unknown Shopify review result code")
    if payload["success"] != (code == "success"):
        raise UsageError(422, "success does not match the Shopify review result code")
    return {"decision_id": decision_id, "success": payload["success"], "code": code,
            "message": _text(payload.get("message"), "message", limit=MAX_OUTCOME_MESSAGE)}


def record_outcome(
    conn: psycopg.Connection, app_id: int, outcome: dict,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    with conn.transaction():
        row = conn.execute(
            """select d.id, d.outcome, d.expires_at, a.review_retry_days
               from review_prompt_decisions d
               join apps a on a.id=d.app_id
               where d.app_id=%s and d.decision_id=%s for update of d""",
            (app_id, outcome["decision_id"]),
        ).fetchone()
        if not row:
            raise KeyError(outcome["decision_id"])
        if row[1] != "issued":
            return row[1]
        if row[2] <= now:
            conn.execute("update review_prompt_decisions set outcome='expired' where id=%s", (row[0],))
            raise RuntimeError("expired")
        code = outcome["code"]
        if outcome["success"]:
            status, next_at = "shown", now + timedelta(days=60)
        elif code == "cancelled":
            status, next_at = "cancelled", now + timedelta(days=row[3])
        elif code == "cooldown-period":
            status, next_at = "temporarily_declined", now + timedelta(days=60)
        elif code == "annual-limit-reached":
            status, next_at = "temporarily_declined", now + timedelta(days=365)
        elif code == "recently-installed":
            status, next_at = "temporarily_declined", now + timedelta(hours=24)
        elif code == "already-reviewed":
            status, next_at = "already_reviewed", None
        elif code == "merchant-ineligible":
            status, next_at = "ineligible", None
        else:
            status, next_at = "failed", None
        conn.execute(
            """update review_prompt_decisions set outcome=%s, response_code=%s,
               response_message=%s, responded_at=%s, next_eligible_at=%s where id=%s""",
            (status, code, outcome["message"], now, next_at, row[0]),
        )
    return status


def app_summary(
    conn: psycopg.Connection, app_id: int, now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    rows = conn.execute(
        """select case when outcome='issued' and expires_at <= %s
                       then 'expired' else outcome end as effective_outcome,
                  count(*)
           from review_prompt_decisions
           where app_id=%s
           group by effective_outcome""", (now, app_id),
    ).fetchall()
    counts = dict(rows)
    requests = sum(counts.values())
    awaiting = counts.get("issued", 0)
    shown = counts.get("shown", 0)
    return {
        "requests": requests,
        "shown": shown,
        "not_opened": requests - shown - awaiting,
        "awaiting": awaiting,
    }


def recent_app_attempts(
    conn: psycopg.Connection, app_id: int, *, limit: int = 25,
    now: datetime | None = None,
) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    rows = conn.execute(
        """select d.shop_gid, coalesce(s.shop_name, s.shop_domain, d.shop_gid),
                  s.shop_domain, d.event_type, d.issued_at,
                  case when d.outcome='issued' and d.expires_at <= %s
                       then 'expired' else d.outcome end as effective_outcome,
                  d.response_code, d.response_message, d.responded_at
           from review_prompt_decisions d
           left join shops s on s.app_id=d.app_id and s.shop_gid=d.shop_gid
           where d.app_id=%s
           order by d.issued_at desc
           limit %s""",
        (now, app_id, limit),
    ).fetchall()
    return [
        {
            "shop_gid": row[0], "shop": row[1], "domain": row[2],
            "event_type": row[3], "issued_at": row[4], "outcome": row[5],
            "response_code": row[6], "response_message": row[7],
            "responded_at": row[8],
        }
        for row in rows
    ]
