from app_dashboard.shops import upsert_shop_state


def test_install_creates_shop_row(db, test_app):
    upsert_shop_state(db, test_app.id, "ai1", install_state="installed", at="2026-06-01T00:00:00Z")
    r = db.execute("select install_state, installed_at from shops "
                   "where app_id=%s and shop_gid='ai1'", (test_app.id,)).fetchone()
    assert r[0] == "installed" and r[1] is not None


def test_state_update_preserves_identity_fields(db, test_app):
    upsert_shop_state(db, test_app.id, "ai1", install_state="installed", at="2026-06-01T00:00:00Z")
    db.execute("update shops set email='m@shop.com', industry='Apparel', "
               "country='US' where app_id=%s and shop_gid='ai1'", (test_app.id,)); db.commit()
    upsert_shop_state(db, test_app.id, "ai1", install_state="uninstalled", at="2026-06-10T00:00:00Z")
    r = db.execute("select email, industry, country, install_state, uninstalled_at "
                   "from shops where app_id=%s and shop_gid='ai1'", (test_app.id,)).fetchone()
    assert r[:3] == ("m@shop.com", "Apparel", "US")   # identity preserved
    assert r[3] == "uninstalled" and r[4] is not None


def test_same_shop_gid_can_have_independent_state_per_app(db, app_factory):
    alpha = app_factory(slug="alpha")
    beta = app_factory(slug="beta")
    upsert_shop_state(
        db, alpha.id, "shared", install_state="installed", at="2026-06-01T00:00:00Z"
    )
    upsert_shop_state(
        db, beta.id, "shared", install_state="uninstalled", at="2026-06-10T00:00:00Z"
    )
    assert db.execute(
        "select app_id, install_state from shops where shop_gid='shared' order by app_id"
    ).fetchall() == [(alpha.id, "installed"), (beta.id, "uninstalled")]
