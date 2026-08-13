import pytest

from app_dashboard.content_profiles import ContentProfileError, get_content_profile, save_content_profile


def test_profile_stores_only_validated_evidence(db, test_app):
    save_content_profile(db, test_app.id, {
        "pillar_url":"https://newcraft.dev/apps/test", "shopify_listing_url":"https://apps.shopify.com/test",
        "default_language":"en", "supported_languages":"en, nl-NL",
        "facts":"Imports: Creates products from ISBN data\nScan: Supports barcode scanning",
        "allowed_claims":"Imports books", "forbidden_claims":"Never claim perfect metadata",
        "source_urls":"https://newcraft.dev/apps/test",
    })
    profile=get_content_profile(db,test_app.id)
    assert profile["default_language"] == "en"
    assert profile["facts"][0]["label"] == "Imports"


def test_profile_rejects_unverified_shapes(db, test_app):
    with pytest.raises(ContentProfileError):
        save_content_profile(db,test_app.id,{"supported_languages":"en","facts":"no separator"})
    with pytest.raises(ContentProfileError):
        save_content_profile(db,test_app.id,{"supported_languages":"en","pillar_url":"http://example.com"})
