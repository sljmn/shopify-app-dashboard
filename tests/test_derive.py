from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app_dashboard.ingest_raw import upsert_charges, upsert_raw_events
from app_dashboard.derive import derive_installation, derive_all_dirty, derive_installations

APP = None


@pytest.fixture(autouse=True)
def _owned_app(test_app):
    global APP
    APP = test_app


def _seed_charge(db, gid, amount, interval):
    db.execute("""insert into charges(app_id,gid,amount,currency_code,subscription_id,
                  plan_interval,plan_amount,flex_billing)
                  values (%s,%s,%s,'USD',%s,%s,%s,false)
                  on conflict (app_id,gid) do nothing""",
               (APP.id, gid, amount, gid, interval, amount)); db.commit()


def test_install_then_subscribe_emits_two_events_and_mrr(db):
    _seed_charge(db, "c1", Decimal("120.00"), "ANNUAL")
    upsert_raw_events(db, APP, [
        dict(id="r1", type="RELATIONSHIP_INSTALLED",
             occurred_at="2026-06-01T00:00:00Z", shop_gid="ai1",
             charge_gid=None, payload={}),
        dict(id="r2", type="SUBSCRIPTION_CHARGE_ACTIVATED",
             occurred_at="2026-06-02T00:00:00Z", shop_gid="ai1",
             charge_gid="c1", payload={"subscriptionId": "c1"}),
    ])
    emitted = derive_installation(db, APP.id, "ai1")
    assert emitted == ["installed", "subscribed"]
    row = db.execute("select monthly_amount, converted_at from subscriptions "
                     "where id=%s", ("c1",)).fetchone()
    assert row[0] == Decimal("10.00")           # 120 annual -> 10/mo
    assert row[1] is not None


def test_annual_charge_from_the_events_feed_lands_as_monthly_mrr(db):
    # End-to-end guard on the annual-plan bug: an annual subscriber counted at
    # the full yearly price every month, overstating Active MRR by 12x for them.
    events = [
        dict(id="r1", type="RELATIONSHIP_INSTALLED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
        dict(id="r2", type="SUBSCRIPTION_CHARGE_ACTIVATED",
             occurred_at="2026-06-02T00:00:00Z", shop_gid="ai1", charge_gid="c-annual",
             payload={"subscriptionId": "c-annual"},
             charge={"id": "c-annual", "amount": {"amount": "190.0", "currencyCode": "USD"},
                     "billingOn": "2027-06-02T00:00:00Z", "name": "Pro", "test": False}),
    ]
    upsert_raw_events(db, APP, events)
    upsert_charges(db, APP, events)
    derive_installation(db, APP.id, "ai1")
    (monthly,) = db.execute(
        "select monthly_amount from subscriptions where id='c-annual'"
    ).fetchone()
    assert monthly == Decimal("15.83")


def test_derivation_is_idempotent(db):
    _seed_charge(db, "c1", Decimal("29.00"), "EVERY_30_DAYS")
    upsert_raw_events(db, APP, [
        dict(id="r1", type="RELATIONSHIP_INSTALLED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
    ])
    derive_installation(db, APP.id, "ai1")
    derive_installation(db, APP.id, "ai1")   # re-run
    n = db.execute("select count(*) from app_events where shop_gid=%s",
                   ("ai1",)).fetchone()[0]
    assert n == 1


def test_cancel_sets_churn_and_negative_netchange(db):
    _seed_charge(db, "c1", Decimal("29.00"), "EVERY_30_DAYS")
    upsert_raw_events(db, APP, [
        dict(id="r1", type="SUBSCRIPTION_CHARGE_ACTIVATED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="ai1", charge_gid="c1", payload={"subscriptionId":"c1"}),
        dict(id="r2", type="SUBSCRIPTION_CHARGE_CANCELED", occurred_at="2026-06-20T00:00:00Z",
             shop_gid="ai1", charge_gid="c1", payload={"subscriptionId":"c1"}),
    ])
    emitted = derive_installation(db, APP.id, "ai1")
    assert emitted[-1] == "unsubscribed"
    nc = db.execute("select net_change from app_events where type='unsubscribed'").fetchone()[0]
    assert nc == Decimal("-29.00")
    churn = db.execute("select churned_at from subscriptions where id='c1'").fetchone()[0]
    assert churn is not None


def test_winback_after_churn_emits_resubscribed_not_upgrade_downgrade(db):
    # install -> subscribe (charge A) -> cancel A -> subscribe again (charge B,
    # a new subscription id). This is an ordinary win-back, not an upgrade/
    # downgrade off a churned subscription's stale current_amount.
    _seed_charge(db, "cA", Decimal("29.00"), "EVERY_30_DAYS")
    _seed_charge(db, "cB", Decimal("49.00"), "EVERY_30_DAYS")
    upsert_raw_events(db, APP, [
        dict(id="r1", type="RELATIONSHIP_INSTALLED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
        dict(id="r2", type="SUBSCRIPTION_CHARGE_ACTIVATED", occurred_at="2026-06-02T00:00:00Z",
             shop_gid="ai1", charge_gid="cA", payload={"subscriptionId": "cA"}),
        dict(id="r3", type="SUBSCRIPTION_CHARGE_CANCELED", occurred_at="2026-06-10T00:00:00Z",
             shop_gid="ai1", charge_gid="cA", payload={"subscriptionId": "cA"}),
        dict(id="r4", type="SUBSCRIPTION_CHARGE_ACTIVATED", occurred_at="2026-06-15T00:00:00Z",
             shop_gid="ai1", charge_gid="cB", payload={"subscriptionId": "cB"}),
    ])
    emitted = derive_installation(db, APP.id, "ai1")
    assert emitted[-1] == "resubscribed"
    converted_at = db.execute(
        "select converted_at from subscriptions where id='cB'"
    ).fetchone()[0]
    assert converted_at is not None


def test_derive_all_dirty_counts_only_new_events(db):
    _seed_charge(db, "c1", Decimal("29.00"), "EVERY_30_DAYS")
    upsert_raw_events(db, APP, [
        dict(id="r1", type="RELATIONSHIP_INSTALLED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
    ])
    first = derive_all_dirty(db, APP.id, "2026-01-01T00:00:00Z")
    assert first == {"installed": 1}
    # nothing new since -> second pass over the same window should count nothing
    second = derive_all_dirty(db, APP.id, "2026-01-01T00:00:00Z")
    assert second == {}


def test_activation_with_missing_charge_warns_and_does_not_raise(db, caplog):
    # no _seed_charge call: the SUBSCRIPTION_CHARGE_ACTIVATED below references
    # a charge_gid that was never synced into `charges`, matching the real
    # production gap (Partner API query only pulls charge { id }).
    upsert_raw_events(db, APP, [
        dict(id="r1", type="RELATIONSHIP_INSTALLED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
        dict(id="r2", type="SUBSCRIPTION_CHARGE_ACTIVATED", occurred_at="2026-06-02T00:00:00Z",
             shop_gid="ai1", charge_gid="missing", payload={"subscriptionId": "missing"}),
    ])
    with caplog.at_level("WARNING"):
        emitted = derive_installation(db, APP.id, "ai1")
    assert emitted == ["installed"]  # activation skipped, install still processed
    assert any("missing" in r.message for r in caplog.records)


def test_test_charges_are_excluded_from_derivation(db):
    db.execute("""insert into charges(app_id,gid,amount,currency_code,subscription_id,
                  plan_interval,plan_amount,flex_billing,test)
                  values (%s,'ct','19.00','USD','ct','EVERY_30_DAYS','19.00',false,true)""",
               (APP.id,))
    db.commit()
    upsert_raw_events(db, APP, [
        dict(id="r1", type="RELATIONSHIP_INSTALLED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
        dict(id="r2", type="SUBSCRIPTION_CHARGE_ACTIVATED", occurred_at="2026-06-02T00:00:00Z",
             shop_gid="ai1", charge_gid="ct", payload={"subscriptionId": "ct"}),
    ])
    emitted = derive_installation(db, APP.id, "ai1")
    assert emitted == ["installed"]      # test charge contributes no MRR event
    assert db.execute("select count(*) from subscriptions").fetchone()[0] == 0


def test_derive_installations_isolates_a_failing_install(db, monkeypatch):
    upsert_raw_events(db, APP, [
        dict(id="r1", type="RELATIONSHIP_INSTALLED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="good", charge_gid=None, payload={}),
        dict(id="r2", type="RELATIONSHIP_INSTALLED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="bad", charge_gid=None, payload={}),
    ])
    import app_dashboard.derive as derive_mod
    original = derive_mod.derive_installation

    def flaky(conn, app_id, shop_gid):
        if shop_gid == "bad":
            raise RuntimeError("boom")
        return original(conn, app_id, shop_gid)

    monkeypatch.setattr(derive_mod, "derive_installation", flaky)
    counts = derive_mod.derive_installations(db, APP.id, ["good", "bad"])
    assert counts == {"installed": 1}
    n = db.execute(
        "select count(*) from app_events where shop_gid = 'good'"
    ).fetchone()[0]
    assert n == 1


def test_plan_change_gives_the_new_subscription_a_converted_at(db):
    """The exact shape seen on prod: Shopify does not edit a subscription when a
    merchant switches plans, it activates a NEW AppSubscription and cancels the
    old one, so the feed reads subscribed -> upgraded -> unsubscribed within a
    day. The replacement used to be stored with converted_at NULL, which hid it
    from mrr_trend, mrr_movements, retention_cohorts and paying_at. Live MRR
    went missing from every chart while still counting toward the Active MRR
    tile, so the two disagreed."""
    _seed_charge(db, "old", Decimal("9.00"), "EVERY_30_DAYS")
    _seed_charge(db, "new", Decimal("19.00"), "EVERY_30_DAYS")
    upsert_raw_events(db, APP, [
        dict(id="r1", type="RELATIONSHIP_INSTALLED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
        dict(id="r2", type="SUBSCRIPTION_CHARGE_ACTIVATED",
             occurred_at="2026-06-03T00:00:00Z", shop_gid="ai1", charge_gid="old",
             payload={}),
        dict(id="r3", type="SUBSCRIPTION_CHARGE_ACTIVATED",
             occurred_at="2026-06-04T00:00:00Z", shop_gid="ai1", charge_gid="new",
             payload={}),
        dict(id="r4", type="SUBSCRIPTION_CHARGE_CANCELED",
             occurred_at="2026-06-04T01:00:00Z", shop_gid="ai1", charge_gid="old",
             payload={}),
    ])
    assert derive_installation(db, APP.id, "ai1") == \
        ["installed", "subscribed", "upgraded", "unsubscribed"]

    subs = dict(db.execute(
        "select id, converted_at from subscriptions where shop_gid='ai1'").fetchall())
    assert subs["old"] is not None
    assert subs["new"] is not None, "the replacement subscription needs a converted_at"

    # The replacement is live and the superseded one is churned...
    live = db.execute(
        "select id, monthly_amount from subscriptions "
        "where shop_gid='ai1' and churned_at is null").fetchall()
    assert live == [("new", Decimal("19.00"))]
    # ...so cancelling the old subscription must not zero the shop out.
    from app_dashboard.stats import mrr_at
    assert mrr_at(db, "2026-07-01Z") == Decimal("19.00")


def test_cancelling_a_superseded_subscription_does_not_zero_the_shop(db):
    """The scalar version treated any cancel as "this shop stopped paying", so
    the net_change on the trailing cancel of a plan change was the whole shop
    rather than the subscription that actually ended."""
    _seed_charge(db, "old", Decimal("9.00"), "EVERY_30_DAYS")
    _seed_charge(db, "new", Decimal("19.00"), "EVERY_30_DAYS")
    upsert_raw_events(db, APP, [
        dict(id="r1", type="RELATIONSHIP_INSTALLED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
        dict(id="r2", type="SUBSCRIPTION_CHARGE_ACTIVATED",
             occurred_at="2026-06-03T00:00:00Z", shop_gid="ai1", charge_gid="old",
             payload={}),
        dict(id="r3", type="SUBSCRIPTION_CHARGE_ACTIVATED",
             occurred_at="2026-06-04T00:00:00Z", shop_gid="ai1", charge_gid="new",
             payload={}),
        dict(id="r4", type="SUBSCRIPTION_CHARGE_CANCELED",
             occurred_at="2026-06-04T01:00:00Z", shop_gid="ai1", charge_gid="old",
             payload={}),
    ])
    derive_installation(db, APP.id, "ai1")
    changes = dict(db.execute(
        "select type, net_change from app_events where shop_gid='ai1' "
        "and type in ('subscribed','upgraded','unsubscribed')").fetchall())
    assert changes["subscribed"] == Decimal("9.00")
    # A replacement activation is a commercial state change from 9 to 19, not a
    # temporary period at 28. The late cancellation concerns the already-replaced
    # subscription and therefore moves no money.
    assert changes["upgraded"] == Decimal("10.00")
    assert changes["unsubscribed"] == Decimal("0.00")
    assert sum(changes.values()) == Decimal("19.00")


def test_a_real_full_cancellation_still_zeroes_the_shop(db):
    _seed_charge(db, "c1", Decimal("19.00"), "EVERY_30_DAYS")
    upsert_raw_events(db, APP, [
        dict(id="r1", type="RELATIONSHIP_INSTALLED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
        dict(id="r2", type="SUBSCRIPTION_CHARGE_ACTIVATED",
             occurred_at="2026-06-03T00:00:00Z", shop_gid="ai1", charge_gid="c1",
             payload={}),
        dict(id="r3", type="SUBSCRIPTION_CHARGE_CANCELED",
             occurred_at="2026-07-03T00:00:00Z", shop_gid="ai1", charge_gid="c1",
             payload={}),
    ])
    derive_installation(db, APP.id, "ai1")
    from app_dashboard.stats import mrr_at
    assert mrr_at(db, "2026-06-15Z") == Decimal("19.00")
    assert mrr_at(db, "2026-08-01Z") == Decimal("0")


def test_replay_does_not_push_converted_at_forward(db):
    """derive_installation replays the whole history every run, so writing
    converted_at unconditionally would move a subscription's conversion date on
    every sync."""
    _seed_charge(db, "c1", Decimal("19.00"), "EVERY_30_DAYS")
    upsert_raw_events(db, APP, [
        dict(id="r1", type="RELATIONSHIP_INSTALLED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
        dict(id="r2", type="SUBSCRIPTION_CHARGE_ACTIVATED",
             occurred_at="2026-06-03T00:00:00Z", shop_gid="ai1", charge_gid="c1",
             payload={}),
        # Same subscription activating again (a resumed charge).
        dict(id="r3", type="SUBSCRIPTION_CHARGE_ACTIVATED",
             occurred_at="2026-09-03T00:00:00Z", shop_gid="ai1", charge_gid="c1",
             payload={}),
    ])
    derive_installation(db, APP.id, "ai1")
    first = db.execute("select converted_at from subscriptions where id='c1'").fetchone()[0]
    derive_installation(db, APP.id, "ai1")
    assert db.execute(
        "select converted_at from subscriptions where id='c1'").fetchone()[0] == first
    # Compared as an instant, not a formatted local date: timestamptz renders in
    # the DB session's timezone, so a midnight-UTC value reads as the previous
    # day on a machine west of UTC.
    assert first == datetime(2026, 6, 3, tzinfo=timezone.utc)


# --- Adversarial orderings ------------------------------------------------
#
# The per-subscription-id rewrite is recent and its failure mode is silent: a
# wrong `live` dict produces a plausible number, not an exception. These are the
# hostile shapes the Partner API feed can actually produce.


def test_uninstall_churns_a_subscription_shopify_never_cancelled(db):
    """Shopify usually sends a cancel or expiry alongside an uninstall, but does
    not promise one. Without churned_at the subscription stayed live in every
    figure that reads subscriptions without joining shops (the MRR chart,
    mrr_at, paying_at, retention cohorts) while the Active MRR tile had already
    dropped it -- two tiles disagreeing over one dataset."""
    _seed_charge(db, "c1", Decimal("19.00"), "EVERY_30_DAYS")
    upsert_raw_events(db, APP, [
        dict(id="r1", type="RELATIONSHIP_INSTALLED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
        dict(id="r2", type="SUBSCRIPTION_CHARGE_ACTIVATED", occurred_at="2026-06-02T00:00:00Z",
             shop_gid="ai1", charge_gid="c1", payload={}),
        dict(id="r3", type="RELATIONSHIP_UNINSTALLED", occurred_at="2026-07-05T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
    ])
    derive_installation(db, APP.id, "ai1")

    churned = db.execute("select churned_at from subscriptions where id='c1'").fetchone()[0]
    assert churned == datetime(2026, 7, 5, tzinfo=timezone.utc)
    from app_dashboard.stats import mrr_at
    assert mrr_at(db, "2026-06-15Z") == Decimal("19.00")
    assert mrr_at(db, "2026-08-01Z") == Decimal("0")


def test_reinstall_onto_the_same_subscription_id_un_churns_it(db):
    """The other half of churning on uninstall: if the same subscription id
    activates again, it is live again. A plain coalesce on churned_at would keep
    it churned forever and lose real money from every figure."""
    _seed_charge(db, "c1", Decimal("19.00"), "EVERY_30_DAYS")
    upsert_raw_events(db, APP, [
        dict(id="r1", type="RELATIONSHIP_INSTALLED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
        dict(id="r2", type="SUBSCRIPTION_CHARGE_ACTIVATED", occurred_at="2026-06-02T00:00:00Z",
             shop_gid="ai1", charge_gid="c1", payload={}),
        dict(id="r3", type="RELATIONSHIP_UNINSTALLED", occurred_at="2026-06-20T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
        dict(id="r4", type="RELATIONSHIP_REACTIVATED", occurred_at="2026-07-01T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
        dict(id="r5", type="SUBSCRIPTION_CHARGE_ACTIVATED", occurred_at="2026-07-02T00:00:00Z",
             shop_gid="ai1", charge_gid="c1", payload={}),
    ])
    derive_installation(db, APP.id, "ai1")

    row = db.execute(
        "select converted_at, churned_at from subscriptions where id='c1'").fetchone()
    assert row[1] is None, "the resurrected subscription is live again"
    # ...and its conversion date is still the first one, not the second.
    assert row[0] == datetime(2026, 6, 2, tzinfo=timezone.utc)
    from app_dashboard.stats import mrr_at
    assert mrr_at(db, "2026-07-10Z") == Decimal("19.00")

    # Known limit, asserted so it stays known: `subscriptions` holds ONE
    # converted_at/churned_at pair per id, so it cannot express "live, then
    # lapsed, then live again". The June 20 - July 1 gap is therefore invisible
    # and history reads as continuous. Getting the *current* state right is the
    # trade that matters -- every headline figure on the dashboard is current
    # state, and leaving the subscription churned would drop live money from all
    # of them. Representing the gap needs a subscription_periods table, which no
    # production row currently justifies: zero charge ids have ever activated
    # twice, because Shopify mints a new AppSubscription each time.
    assert mrr_at(db, "2026-06-25Z") == Decimal("19.00")


def test_a_cancel_arriving_before_its_activation_does_not_go_negative(db):
    """Events are replayed in occurred_at order, but the feed can hand us a
    cancel for a subscription this replay has never seen -- a charge that
    activated before the ingest window opened. It must come off as zero rather
    than driving MRR below it."""
    _seed_charge(db, "c1", Decimal("19.00"), "EVERY_30_DAYS")
    upsert_raw_events(db, APP, [
        dict(id="r1", type="SUBSCRIPTION_CHARGE_CANCELED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="ai1", charge_gid="c1", payload={}),
    ])
    emitted = derive_installation(db, APP.id, "ai1")
    assert emitted == ["unsubscribed"]
    nc = db.execute(
        "select net_change from app_events where type='unsubscribed'").fetchone()[0]
    assert nc == Decimal("0")
    from app_dashboard.stats import mrr_at
    assert mrr_at(db, "2026-07-01Z") == Decimal("0")


def test_a_frozen_charge_leaves_mrr_and_stays_out(db):
    """A frozen subscription stops contributing until Shopify unfreezes it."""
    _seed_charge(db, "c1", Decimal("19.00"), "EVERY_30_DAYS")
    upsert_raw_events(db, APP, [
        dict(id="r1", type="RELATIONSHIP_INSTALLED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
        dict(id="r2", type="SUBSCRIPTION_CHARGE_ACTIVATED", occurred_at="2026-06-02T00:00:00Z",
             shop_gid="ai1", charge_gid="c1", payload={}),
        dict(id="r3", type="SUBSCRIPTION_CHARGE_FROZEN", occurred_at="2026-06-20T00:00:00Z",
             shop_gid="ai1", charge_gid="c1", payload={}),
    ])
    assert derive_installation(db, APP.id, "ai1") == [
        "installed", "subscribed", "subscription_frozen"
    ]
    from app_dashboard.stats import mrr_at
    assert mrr_at(db, "2026-07-01Z") == Decimal("0")


def test_an_unfrozen_charge_restores_mrr_without_a_new_activation(db):
    _seed_charge(db, "c1", Decimal("19.00"), "EVERY_30_DAYS")
    upsert_raw_events(db, APP, [
        dict(id="r1", type="RELATIONSHIP_INSTALLED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
        dict(id="r2", type="SUBSCRIPTION_CHARGE_ACTIVATED", occurred_at="2026-06-02T00:00:00Z",
             shop_gid="ai1", charge_gid="c1", payload={}),
        dict(id="r3", type="SUBSCRIPTION_CHARGE_FROZEN", occurred_at="2026-06-20T00:00:00Z",
             shop_gid="ai1", charge_gid="c1", payload={}),
        dict(id="r4", type="SUBSCRIPTION_CHARGE_UNFROZEN", occurred_at="2026-07-01T00:00:00Z",
             shop_gid="ai1", charge_gid="c1", payload={}),
    ])

    assert derive_installation(db, APP.id, "ai1") == [
        "installed", "subscribed", "subscription_frozen", "subscription_unfrozen"
    ]
    row = db.execute(
        "select monthly_amount, churned_at from subscriptions where id='c1'"
    ).fetchone()
    assert row == (Decimal("19.00"), None)
    assert db.execute(
        "select sum(net_change) from app_events where shop_gid='ai1'"
    ).fetchone()[0] == Decimal("19.00")


def test_unfreeze_does_not_reopen_an_app_that_is_still_deactivated(db):
    _seed_charge(db, "c1", Decimal("19.00"), "EVERY_30_DAYS")
    upsert_raw_events(db, APP, [
        dict(id="r1", type="RELATIONSHIP_INSTALLED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
        dict(id="r2", type="SUBSCRIPTION_CHARGE_ACTIVATED", occurred_at="2026-06-02T00:00:00Z",
             shop_gid="ai1", charge_gid="c1", payload={}),
        dict(id="r3", type="RELATIONSHIP_DEACTIVATED", occurred_at="2026-06-20T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
        dict(id="r4", type="SUBSCRIPTION_CHARGE_FROZEN", occurred_at="2026-06-20T00:00:01Z",
             shop_gid="ai1", charge_gid="c1", payload={}),
        dict(id="r5", type="SUBSCRIPTION_CHARGE_UNFROZEN", occurred_at="2026-07-01T00:00:00Z",
             shop_gid="ai1", charge_gid="c1", payload={}),
    ])

    derive_installation(db, APP.id, "ai1")
    assert db.execute("select churned_at from subscriptions where id='c1'").fetchone()[0] is not None
    assert db.execute("select net_change from app_events where platform_event_id='r5'").fetchone()[0] == 0


def test_a_declined_charge_is_visible_as_abandoned_but_never_enters_mrr(db):
    _seed_charge(db, "c1", Decimal("19.00"), "EVERY_30_DAYS")
    upsert_raw_events(db, APP, [
        dict(id="r1", type="RELATIONSHIP_INSTALLED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
        dict(id="r2", type="SUBSCRIPTION_CHARGE_DECLINED", occurred_at="2026-06-02T00:00:00Z",
             shop_gid="ai1", charge_gid="c1", payload={}),
    ])
    assert derive_installation(db, APP.id, "ai1") == ["installed", "charge_abandoned"]
    assert db.execute("select count(*) from subscriptions").fetchone()[0] == 0
    assert db.execute(
        "select net_change from app_events where type='charge_abandoned'"
    ).fetchone()[0] == Decimal("0")


def test_expired_charge_is_backdated_but_not_before_install(db):
    _seed_charge(db, "c1", Decimal("19.00"), "EVERY_30_DAYS")
    upsert_raw_events(db, APP, [
        dict(id="r1", type="RELATIONSHIP_INSTALLED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
        dict(id="r2", type="SUBSCRIPTION_CHARGE_EXPIRED", occurred_at="2026-06-02T00:00:00Z",
             shop_gid="ai1", charge_gid="c1", payload={}),
    ])
    derive_installation(db, APP.id, "ai1")
    occurred_at = db.execute(
        "select occurred_at from app_events where type='charge_abandoned'"
    ).fetchone()[0]
    assert occurred_at == datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_cancel_correlated_with_replacement_is_removed_on_replay(db):
    _seed_charge(db, "old", Decimal("9.00"), "EVERY_30_DAYS")
    _seed_charge(db, "new", Decimal("19.00"), "EVERY_30_DAYS")
    upsert_raw_events(db, APP, [
        dict(id="r1", type="SUBSCRIPTION_CHARGE_ACTIVATED",
             occurred_at="2026-06-01T00:00:00Z", shop_gid="ai1", charge_gid="old", payload={}),
        dict(id="r2", type="SUBSCRIPTION_CHARGE_CANCELED",
             occurred_at="2026-06-02T00:00:00Z", shop_gid="ai1", charge_gid="old", payload={}),
    ])
    derive_installation(db, APP.id, "ai1")
    assert db.execute("select count(*) from app_events where platform_event_id='r2'").fetchone()[0] == 1

    upsert_raw_events(db, APP, [
        dict(id="r3", type="SUBSCRIPTION_CHARGE_ACTIVATED",
             occurred_at="2026-06-02T00:00:30Z", shop_gid="ai1", charge_gid="new", payload={}),
    ])
    emitted = derive_installation(db, APP.id, "ai1")

    assert emitted == ["subscribed", "upgraded"]
    assert db.execute("select count(*) from app_events where platform_event_id='r2'").fetchone()[0] == 0
    assert db.execute("select net_change from app_events where platform_event_id='r3'").fetchone()[0] == Decimal("10.00")


def test_a_duplicate_platform_event_id_is_recorded_once(db):
    """raw_app_events ids are the dedupe key for app_events. A redelivered event
    must not double-count its net_change into the movement buckets."""
    _seed_charge(db, "c1", Decimal("19.00"), "EVERY_30_DAYS")
    upsert_raw_events(db, APP, [
        dict(id="r1", type="SUBSCRIPTION_CHARGE_ACTIVATED", occurred_at="2026-06-02T00:00:00Z",
             shop_gid="ai1", charge_gid="c1", payload={}),
    ])
    upsert_raw_events(db, APP, [   # same id redelivered
        dict(id="r1", type="SUBSCRIPTION_CHARGE_ACTIVATED", occurred_at="2026-06-02T00:00:00Z",
             shop_gid="ai1", charge_gid="c1", payload={}),
    ])
    derive_installation(db, APP.id, "ai1")
    assert db.execute("select count(*) from raw_app_events").fetchone()[0] == 1
    assert db.execute("select count(*) from app_events").fetchone()[0] == 1
    from app_dashboard.stats import mrr_at
    assert mrr_at(db, "2026-07-01Z") == Decimal("19.00")


def test_derivation_keeps_overlapping_external_ids_in_separate_apps(db, app_factory):
    alpha = app_factory(
        slug="alpha", annual_plan_amounts=frozenset({Decimal("190.00")})
    )
    beta = app_factory(slug="beta", annual_plan_amounts=frozenset())
    events = [
        dict(
            id="shared-install",
            type="RELATIONSHIP_INSTALLED",
            occurred_at="2026-06-01T00:00:00Z",
            shop_gid="shared-shop",
            charge_gid=None,
            payload={},
        ),
        dict(
            id="shared-activation",
            type="SUBSCRIPTION_CHARGE_ACTIVATED",
            occurred_at="2026-06-02T00:00:00Z",
            shop_gid="shared-shop",
            charge_gid="shared-subscription",
            payload={},
            charge={
                "id": "shared-subscription",
                "amount": {"amount": "190.00", "currencyCode": "USD"},
                "test": False,
            },
        ),
    ]
    for app in (alpha, beta):
        upsert_raw_events(db, app, events)
        upsert_charges(db, app, events)
        derive_installation(db, app.id, "shared-shop")

    assert db.execute(
        """select app_id, monthly_amount from subscriptions
           where id='shared-subscription' order by app_id"""
    ).fetchall() == [
        (alpha.id, Decimal("15.83")),
        (beta.id, Decimal("190.00")),
    ]
    assert db.execute(
        "select count(*) from app_events where platform_event_id='shared-activation'"
    ).fetchone()[0] == 2
