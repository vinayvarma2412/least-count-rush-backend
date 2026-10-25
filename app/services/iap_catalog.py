"""Allowed IAP products and plan durations. Client plan_id is never trusted.

Product ids can be overridden without a deploy:

* ``IAP_ANDROID_PRODUCT_ID`` — the single Play subscription product
* ``IAP_IOS_PRODUCT_IDS`` — JSON object of App Store product id → plan id,
  merged on top of the built-in map
"""
import json
import os
from datetime import timedelta

ANDROID_PRODUCT_ID = "ad_free_monthly"

IOS_PRODUCT_TO_PLAN = {
    "ad_free_monthly": "monthly",
    "ad_free_3months": "quarterly",
    "ad_free_6months": "halfyearly",
    "ad_free_yearly": "yearly",
}

ANDROID_PLAN_IDS = {"monthly", "quarterly", "halfyearly", "yearly"}

# Play Console basePlanId values we have seen besides the canonical ids.
ANDROID_PLAN_ALIASES = {
    "monthly": "monthly",
    "p1m": "monthly",
    "1month": "monthly",
    "1-month": "monthly",
    "ad_free_monthly": "monthly",
    "quarterly": "quarterly",
    "p3m": "quarterly",
    "3months": "quarterly",
    "3-months": "quarterly",
    "ad_free_3months": "quarterly",
    "halfyearly": "halfyearly",
    "half-yearly": "halfyearly",
    "p6m": "halfyearly",
    "6months": "halfyearly",
    "6-months": "halfyearly",
    "ad_free_6months": "halfyearly",
    "yearly": "yearly",
    "p1y": "yearly",
    "12months": "yearly",
    "12-months": "yearly",
    "ad_free_yearly": "yearly",
    "adfreemonthly": "monthly",
    "adfree3months": "quarterly",
    "adfree6months": "halfyearly",
    "adfreeyearly": "yearly",
}


def canonicalize_plan_id(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip()
    if raw in ANDROID_PLAN_IDS:
        return raw
    lower = raw.lower()
    if lower in ANDROID_PLAN_ALIASES:
        return ANDROID_PLAN_ALIASES[lower]
    compact = lower.replace("-", "").replace("_", "")
    return ANDROID_PLAN_ALIASES.get(compact)

PLAN_DURATION = {
    "monthly": timedelta(days=30),
    "quarterly": timedelta(days=90),
    "halfyearly": timedelta(days=180),
    "yearly": timedelta(days=365),
}

GOOGLE_ACTIVE_STATES = {
    "SUBSCRIPTION_STATE_ACTIVE",
    "SUBSCRIPTION_STATE_CANCELED",
    "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
}


def get_android_product_id() -> str:
    value = os.getenv("IAP_ANDROID_PRODUCT_ID", ANDROID_PRODUCT_ID).strip()
    return value or ANDROID_PRODUCT_ID


def get_ios_product_map() -> dict[str, str]:
    """App Store product id → canonical plan id, with env overrides merged in."""
    merged = dict(IOS_PRODUCT_TO_PLAN)
    override = os.getenv("IAP_IOS_PRODUCT_IDS")
    if not override:
        return merged
    try:
        parsed = json.loads(override)
    except Exception:
        return merged
    if not isinstance(parsed, dict):
        return merged
    for product_id, plan_id in parsed.items():
        if not isinstance(product_id, str) or not isinstance(plan_id, str):
            continue
        product = product_id.strip()
        plan = plan_id.strip()
        if product and plan in PLAN_DURATION:
            merged[product] = plan
    return merged


def plan_id_for_product(platform: str, product_id: str, android_plan_id: str | None = None) -> str | None:
    if platform == "ios":
        return get_ios_product_map().get(product_id) or canonicalize_plan_id(product_id)
    if platform == "android":
        plan = canonicalize_plan_id(android_plan_id)
        if plan:
            return plan
        if product_id != get_android_product_id():
            return canonicalize_plan_id(product_id)
        return None
    return canonicalize_plan_id(product_id)


def is_allowed_product(platform: str, product_id: str) -> bool:
    if platform == "ios":
        return product_id in get_ios_product_map()
    if platform == "android":
        return product_id == get_android_product_id()
    return False
