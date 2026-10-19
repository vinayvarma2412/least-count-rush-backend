"""Allowed IAP products and plan durations. Client plan_id is never trusted."""
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


def plan_id_for_product(platform: str, product_id: str, android_plan_id: str | None = None) -> str | None:
    if platform == "ios":
        return IOS_PRODUCT_TO_PLAN.get(product_id) or canonicalize_plan_id(product_id)
    if platform == "android":
        plan = canonicalize_plan_id(android_plan_id)
        if plan:
            return plan
        if product_id != ANDROID_PRODUCT_ID:
            return canonicalize_plan_id(product_id)
        return None
    return canonicalize_plan_id(product_id)


def is_allowed_product(platform: str, product_id: str) -> bool:
    if platform == "ios":
        return product_id in IOS_PRODUCT_TO_PLAN
    if platform == "android":
        return product_id == ANDROID_PRODUCT_ID
    return False
