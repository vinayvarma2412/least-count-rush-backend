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
        return IOS_PRODUCT_TO_PLAN.get(product_id)
    if platform == "android":
        if product_id != ANDROID_PRODUCT_ID:
            return None
        if android_plan_id in ANDROID_PLAN_IDS:
            return android_plan_id
        return None
    return None


def is_allowed_product(platform: str, product_id: str) -> bool:
    if platform == "ios":
        return product_id in IOS_PRODUCT_TO_PLAN
    if platform == "android":
        return product_id == ANDROID_PRODUCT_ID
    return False
