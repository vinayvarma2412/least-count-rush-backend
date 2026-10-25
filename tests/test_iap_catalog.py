from app.services.iap_catalog import (
    get_android_product_id,
    get_ios_product_map,
    is_allowed_product,
    plan_id_for_product,
)


def test_builtin_products_stay_allowed_without_overrides(monkeypatch):
    monkeypatch.delenv("IAP_ANDROID_PRODUCT_ID", raising=False)
    monkeypatch.delenv("IAP_IOS_PRODUCT_IDS", raising=False)

    assert get_android_product_id() == "ad_free_monthly"
    assert is_allowed_product("android", "ad_free_monthly")
    assert not is_allowed_product("android", "coins_pack")
    assert get_ios_product_map()["ad_free_yearly"] == "yearly"
    assert plan_id_for_product("ios", "ad_free_3months") == "quarterly"


def test_env_overrides_extend_the_allowlist(monkeypatch):
    monkeypatch.setenv("IAP_ANDROID_PRODUCT_ID", "ad_free_v2")
    monkeypatch.setenv(
        "IAP_IOS_PRODUCT_IDS",
        '{"ad_free_yearly_v2": "yearly", "bad": "nope", "ad_free_monthly": "monthly"}',
    )

    assert get_android_product_id() == "ad_free_v2"
    assert is_allowed_product("android", "ad_free_v2")
    assert not is_allowed_product("android", "ad_free_monthly")
    ios = get_ios_product_map()
    assert ios["ad_free_yearly_v2"] == "yearly"
    assert ios["ad_free_3months"] == "quarterly"
    assert "bad" not in ios
    assert is_allowed_product("ios", "ad_free_yearly_v2")
    assert plan_id_for_product("ios", "ad_free_yearly_v2") == "yearly"


def test_blank_or_invalid_overrides_keep_the_defaults(monkeypatch):
    monkeypatch.setenv("IAP_ANDROID_PRODUCT_ID", "   ")
    monkeypatch.setenv("IAP_IOS_PRODUCT_IDS", "not-json")

    assert get_android_product_id() == "ad_free_monthly"
    assert get_ios_product_map()["ad_free_6months"] == "halfyearly"
    assert plan_id_for_product("android", "ad_free_monthly", "p1y") == "yearly"
