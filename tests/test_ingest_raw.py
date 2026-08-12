from decimal import Decimal

from app_dashboard.ingest_raw import (
    upsert_payout_earnings,
    upsert_charges,
    upsert_raw_events,
    upsert_transactions,
)


def _ev(**kw):
    base = dict(id="e1", type="RELATIONSHIP_INSTALLED",
                occurred_at="2026-06-01T00:00:00Z",
                shop_gid="ai1", charge_gid=None, payload={})
    base.update(kw); return base


def test_insert_then_dedupe(db, test_app):
    assert upsert_raw_events(db, test_app, [_ev()]) == 1
    # exact same event re-ingested (overlap window) inserts nothing
    assert upsert_raw_events(db, test_app, [_ev()]) == 0


def test_null_charge_dedupes_via_sentinel(db, test_app):
    upsert_raw_events(db, test_app, [_ev(id="a", charge_gid=None)])
    # different id, same (install,type,time,null-charge) => duplicate, skipped
    assert upsert_raw_events(db, test_app, [_ev(id="b", charge_gid=None)]) == 0


def test_upsert_charges_from_inline_charge_objects(db, test_app):
    events = [
        _ev(),                                    # no charge -> skipped
        _ev(id="e2", type="SUBSCRIPTION_CHARGE_ACTIVATED", charge_gid="c1",
            charge={"id": "c1", "amount": {"amount": "19.0", "currencyCode": "USD"},
                    "billingOn": None, "name": "Pro", "test": False}),
    ]
    assert upsert_charges(db, test_app, events) == 1
    row = db.execute("select amount, currency_code, subscription_id, plan_interval, test "
                     "from charges where app_id=%s and gid='c1'", (test_app.id,)).fetchone()
    assert row == (Decimal("19.00"), "USD", "c1", "EVERY_30_DAYS", False)


def _charge_ev(gid, amount):
    return _ev(id=f"ev-{gid}", type="SUBSCRIPTION_CHARGE_ACTIVATED", charge_gid=gid,
               charge={"id": gid, "amount": {"amount": amount, "currencyCode": "USD"},
                       "billingOn": None, "name": "Pro", "test": False})


def test_annual_price_is_stored_as_an_annual_interval(db, test_app):
    # $190 is the yearly price of a plan that also sells at $19/30 days, and
    # ANNUAL_PLAN_AMOUNTS lists it. Storing it as EVERY_30_DAYS would count one
    # subscriber as $190/mo of MRR.
    upsert_charges(db, test_app, [_charge_ev("c-annual", "190.0")])
    (interval,) = db.execute(
        "select plan_interval from charges where app_id=%s and gid='c-annual'",
        (test_app.id,),
    ).fetchone()
    assert interval == "ANNUAL"


def test_reingesting_repairs_a_wrong_stored_interval(db, test_app):
    upsert_charges(db, test_app, [_charge_ev("c-annual", "190.0")])
    db.execute(
        "update charges set plan_interval='EVERY_30_DAYS' where app_id=%s and gid='c-annual'",
        (test_app.id,),
    )
    # plan_interval is in the ON CONFLICT update set, so the next poll corrects
    # history rather than leaving the bad value in place forever.
    upsert_charges(db, test_app, [_charge_ev("c-annual", "190.0")])
    (interval,) = db.execute(
        "select plan_interval from charges where app_id=%s and gid='c-annual'",
        (test_app.id,),
    ).fetchone()
    assert interval == "ANNUAL"


def test_an_unlisted_annual_price_is_silently_counted_as_monthly(db, app_factory):
    """The trap this setting exists to make visible.

    AppSubscription carries no billing-interval field, so the only signal is the
    price. A price the operator forgets to list in ANNUAL_PLAN_AMOUNTS is not
    rejected or flagged, it is counted as a 30-day plan, which reports it at
    twelve times its real MRR. Empty is the default precisely because inheriting
    somebody else's price list would be worse.
    """
    app = app_factory(annual_plan_amounts=frozenset())
    upsert_charges(db, app, [_charge_ev("c-unlisted", "490.0")])
    (interval,) = db.execute(
        "select plan_interval from charges where app_id=%s and gid='c-unlisted'",
        (app.id,),
    ).fetchone()
    assert interval == "EVERY_30_DAYS"


def test_listing_the_price_is_what_makes_it_annual(db, app_factory):
    app = app_factory(
        annual_plan_amounts=frozenset({Decimal("190.00"), Decimal("490.00")})
    )
    upsert_charges(db, app, [_charge_ev("c-listed", "490.0")])
    (interval,) = db.execute(
        "select plan_interval from charges where app_id=%s and gid='c-listed'",
        (app.id,),
    ).fetchone()
    assert interval == "ANNUAL"


def test_external_ids_and_annual_prices_are_isolated_between_apps(db, app_factory):
    alpha = app_factory(
        slug="alpha", annual_plan_amounts=frozenset({Decimal("190.00")})
    )
    beta = app_factory(slug="beta", annual_plan_amounts=frozenset())
    event = _charge_ev("shared-charge", "190.00")

    assert upsert_raw_events(db, alpha, [event]) == 1
    assert upsert_raw_events(db, beta, [event]) == 1
    upsert_charges(db, alpha, [event])
    upsert_charges(db, beta, [event])

    rows = db.execute(
        """select app_id, plan_interval from charges
           where gid = 'shared-charge' order by app_id"""
    ).fetchall()
    assert rows == [(alpha.id, "ANNUAL"), (beta.id, "EVERY_30_DAYS")]

    transaction = {
        "id": "shared-transaction",
        "type": "APP_SUBSCRIPTION_SALE",
        "created_at": "2026-06-03T00:00:00Z",
        "shop_gid": "shared-shop",
        "charge_gid": "shared-charge",
        "billing_interval": "ANNUAL",
        "gross_amount": "190.00",
        "shopify_fee": "0.00",
        "net_amount": "184.49",
        "currency_code": "USD",
    }
    assert upsert_transactions(db, alpha, [transaction]) == 1
    assert upsert_transactions(db, beta, [transaction]) == 1
    assert db.execute(
        "select count(*) from transactions where id = 'shared-transaction'"
    ).fetchone()[0] == 2


def test_payout_earning_upsert_adds_settlement_later(db, test_app):
    earning = {
        "id": "earning-1",
        "event_type": "EARNING_CHARGE_RECURRING",
        "earning_type": "APP_SUBSCRIPTION",
        "occurred_at": "2026-08-07T10:00:00Z",
        "settlement_date": None,
        "shop_gid": "shop-1",
        "description": "Subscription",
        "gross_amount": "19.00",
        "shopify_fee": "0.00",
        "net_amount": "18.45",
        "currency_code": "USD",
    }
    assert upsert_payout_earnings(db, test_app, [earning]) == 1
    assert upsert_payout_earnings(
        db, test_app, [{**earning, "settlement_date": "2026-08-12"}]
    ) == 0
    assert db.execute(
        "select settlement_date, net_amount from payout_earnings "
        "where app_id=%s and id='earning-1'", (test_app.id,),
    ).fetchone() == (__import__("datetime").date(2026, 8, 12), Decimal("18.45"))
