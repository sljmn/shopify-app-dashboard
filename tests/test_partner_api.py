import httpx
import pytest

from app_dashboard.partner_api import (
    PartnerClient,
    fetch_active_subscription,
    fetch_app_events,
    fetch_earnings,
    fetch_transactions,
)


def _client(payload, status=200, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request.read().decode())
        return httpx.Response(status, json=payload)

    return PartnerClient("tok", "1", transport=httpx.MockTransport(handler))


def test_maps_nodes_and_returns_cursor():
    payload = {"data": {"app": {"events": {
        "pageInfo": {"hasNextPage": True},
        "edges": [{"cursor": "cur1", "node": {
            "type": "RELATIONSHIP_INSTALLED",
            "occurredAt": "2026-06-01T00:00:00Z",
            "shop": {"id": "gid://partners/Shop/1", "myshopifyDomain": "x.myshopify.com",
                     "name": "X"}}}]}}}}
    events, cursor = fetch_app_events(_client(payload), app_id="2")
    assert cursor == "cur1"
    assert events[0]["type"] == "RELATIONSHIP_INSTALLED"
    assert events[0]["shop_gid"] == "gid://partners/Shop/1"
    assert events[0]["shop_domain"] == "x.myshopify.com"
    assert events[0]["charge"] is None


def test_app_events_passes_occurred_at_min():
    seen = []
    payload = {"data": {"app": {"events": {
        "pageInfo": {"hasNextPage": False}, "edges": [],
    }}}}

    fetch_app_events(
        _client(payload, seen=seen),
        app_id="2",
        occurred_at_min="2026-08-11T07:00:00+00:00",
    )

    assert '"occurredAtMin":"2026-08-11T07:00:00+00:00"' in seen[0]


def test_maps_inline_charge():
    payload = {"data": {"app": {"events": {
        "pageInfo": {"hasNextPage": False},
        "edges": [{"cursor": "cur1", "node": {
            "type": "SUBSCRIPTION_CHARGE_ACTIVATED",
            "occurredAt": "2026-06-02T00:00:00Z",
            "shop": {"id": "gid://partners/Shop/1", "myshopifyDomain": "x.myshopify.com",
                     "name": "X"},
            "charge": {"id": "gid://partners/AppSubscription/9",
                       "amount": {"amount": "19.0", "currencyCode": "USD"},
                       "billingOn": None, "name": "Pro", "test": False}}}]}}}}
    events, cursor = fetch_app_events(_client(payload), app_id="2")
    assert cursor is None            # hasNextPage false -> drained
    assert events[0]["charge_gid"] == "gid://partners/AppSubscription/9"
    assert events[0]["charge"]["amount"]["amount"] == "19.0"


def test_graphql_errors_raise_instead_of_keyerror():
    payload = {"data": None, "errors": [{"message": "Invalid API version"}]}
    with pytest.raises(RuntimeError, match="Invalid API version"):
        fetch_app_events(_client(payload), app_id="2")


def test_fetch_active_subscription_converts_gids_and_maps_current_state():
    seen = []
    payload = {"data": {"activeSubscription": {
        "billingPeriod": "EVERY_30_DAYS",
        "trialEndsAt": "2026-08-20T12:00:00Z",
        "cancelAtEndOfCycle": True,
        "legacySubscriptionId": "gid://shopify/AppSubscription/9",
        "items": [{
            "handle": "starter",
            "description": "Starter",
            "price": {"active": True, "currency": "USD"},
        }],
    }}}

    snapshot = fetch_active_subscription(
        _client(payload, seen=seen),
        app_id="gid://partners/App/2",
        shop_id="gid://partners/Shop/1",
    )

    assert '"appId":"gid://shopify/App/2"' in seen[0]
    assert '"shopId":"gid://shopify/Shop/1"' in seen[0]
    assert snapshot == {
        "legacy_subscription_id": "gid://shopify/AppSubscription/9",
        "billing_period": "EVERY_30_DAYS",
        "trial_ends_at": "2026-08-20T12:00:00Z",
        "cancel_at_end_of_cycle": True,
        "item_handle": "starter",
        "item_description": "Starter",
        "currency_code": "USD",
        "payload": payload["data"]["activeSubscription"],
    }


def test_fetch_active_subscription_preserves_a_nil_snapshot():
    assert fetch_active_subscription(
        _client({"data": {"activeSubscription": None}}),
        app_id="gid://partners/App/2",
        shop_id="gid://partners/Shop/1",
    ) is None


def test_active_subscription_graphql_errors_raise():
    payload = {"data": None, "errors": [{"message": "Access denied"}]}
    with pytest.raises(RuntimeError, match="Access denied"):
        fetch_active_subscription(
            _client(payload),
            app_id="gid://partners/App/2",
            shop_id="gid://partners/Shop/1",
        )


def _transactions(*nodes, has_next=False):
    return {"data": {"transactions": {
        "pageInfo": {"hasNextPage": has_next},
        "edges": [{"cursor": f"cur{i}", "node": n} for i, n in enumerate(nodes)],
    }}}


SUB_SALE = {
    "__typename": "AppSubscriptionSale",
    "id": "gid://partners/AppSubscriptionSale/764220980",
    "createdAt": "2026-08-07T07:02:51.000000Z",
    "chargeId": "gid://shopify/AppSubscription/31499911397",
    "billingInterval": "EVERY_30_DAYS",
    "shop": {"id": "gid://partners/Shop/1", "myshopifyDomain": "x.myshopify.com",
             "name": "X"},
    # The real shape, copied from a live response: shopifyFee is 0.00 (the
    # revenue share, which is 0% under $1M) while net is 2.9% below gross,
    # because the processing fee only ever shows up in that gap.
    "grossAmount": {"amount": "19.0", "currencyCode": "USD"},
    "shopifyFee": {"amount": "0.0", "currencyCode": "USD"},
    "netAmount": {"amount": "18.45", "currencyCode": "USD"},
}


def test_maps_transaction_and_keeps_real_id():
    rows, cursor = fetch_transactions(_client(_transactions(SUB_SALE)), app_id="2")
    assert cursor is None
    row = rows[0]
    # The Partner API id, verbatim -- not a composed key like app events need.
    assert row["id"] == "gid://partners/AppSubscriptionSale/764220980"
    assert row["type"] == "AppSubscriptionSale"
    assert row["shop_gid"] == "gid://partners/Shop/1"
    assert row["billing_interval"] == "EVERY_30_DAYS"
    assert (row["gross_amount"], row["shopify_fee"], row["net_amount"]) == \
        ("19.0", "0.0", "18.45")
    assert row["currency_code"] == "USD"


def test_adjustment_has_no_billing_interval():
    adjustment = {
        "__typename": "AppSaleAdjustment",
        "id": "gid://partners/AppSaleAdjustment/5",
        "createdAt": "2026-07-01T00:00:00Z",
        "chargeId": None,
        "shop": {"id": "gid://partners/Shop/1", "myshopifyDomain": "x.myshopify.com",
                 "name": "X"},
        "grossAmount": {"amount": "-19.0", "currencyCode": "USD"},
        "shopifyFee": {"amount": "0.0", "currencyCode": "USD"},
        "netAmount": {"amount": "-18.45", "currencyCode": "USD"},
    }
    rows, _ = fetch_transactions(_client(_transactions(adjustment)), app_id="2")
    assert rows[0]["billing_interval"] is None
    assert rows[0]["gross_amount"] == "-19.0"


def test_passes_created_at_min_and_paginates():
    seen = []
    client = _client(_transactions(SUB_SALE, has_next=True), seen=seen)
    rows, cursor = fetch_transactions(client, app_id="2",
                                      created_at_min="2026-08-01T00:00:00Z")
    assert cursor == "cur0"
    assert '"createdAtMin":"2026-08-01T00:00:00Z"' in seen[0]


def test_transactions_graphql_errors_raise():
    payload = {"data": None, "errors": [{"message": "Access denied"}]}
    with pytest.raises(RuntimeError, match="Access denied"):
        fetch_transactions(_client(payload), app_id="2")


def _earnings(*nodes, has_next=False):
    return {"data": {"events": {
        "pageInfo": {"hasNextPage": has_next},
        "edges": [{"cursor": f"earning-{i}", "node": node}
                  for i, node in enumerate(nodes)],
    }}}


EARNING = {
    "id": "gid://partners/Earning/11",
    "eventType": "EARNING_CHARGE_RECURRING",
    "earningType": "APP_SUBSCRIPTION",
    "occurredAt": "2026-08-07T07:02:51Z",
    "settlementDate": "2026-08-12",
    "description": "App subscription earning",
    "shop": {"id": "gid://partners/Shop/1", "myshopifyDomain": "x.myshopify.com",
             "name": "X"},
    "grossAmount": {"amount": "19.00", "currencyCode": "USD"},
    "shopifyFee": {"amount": "0.00", "currencyCode": "USD"},
    "netAmount": {"amount": "18.45", "currencyCode": "USD"},
}


def test_maps_earning_settlement_and_bounded_window():
    seen = []
    rows, cursor = fetch_earnings(
        _client(_earnings(EARNING, has_next=True), seen=seen),
        app_id="gid://partners/App/2",
        occurred_at_min="2026-01-01T00:00:00Z",
        occurred_at_max="2026-08-12T23:59:59Z",
    )

    assert cursor == "earning-0"
    assert '"appId":"gid://shopify/App/2"' in seen[0]
    assert rows[0]["settlement_date"] == "2026-08-12"
    assert rows[0]["net_amount"] == "18.45"
    assert rows[0]["currency_code"] == "USD"
    assert "subjectType: APP" in seen[0]
    assert '"occurredAtMax":"2026-08-12T23:59:59Z"' in seen[0]


def test_earning_can_be_unsettled():
    node = {**EARNING, "id": "gid://partners/Earning/12", "settlementDate": None}
    rows, _ = fetch_earnings(
        _client(_earnings(node)), app_id="gid://partners/App/2",
        occurred_at_min="2026-01-01T00:00:00Z",
        occurred_at_max="2026-08-12T23:59:59Z",
    )
    assert rows[0]["settlement_date"] is None


def test_earnings_graphql_errors_raise():
    with pytest.raises(RuntimeError, match="Access denied"):
        fetch_earnings(
            _client({"data": None, "errors": [{"message": "Access denied"}]}),
            app_id="gid://partners/App/2",
            occurred_at_min="2026-01-01T00:00:00Z",
            occurred_at_max="2026-08-12T23:59:59Z",
        )


def test_partner_client_retries_429_and_honours_retry_after():
    attempts = 0
    sleeps = []
    payload = {"data": {"app": {"events": {
        "pageInfo": {"hasNextPage": False}, "edges": [],
    }}}}

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, request=request)
        return httpx.Response(200, json=payload, request=request)

    client = PartnerClient(
        "tok", "retry-org", transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
    )

    events, cursor = fetch_app_events(client, app_id="2")

    assert events == [] and cursor is None
    assert attempts == 2
    assert sleeps == [2.0]
