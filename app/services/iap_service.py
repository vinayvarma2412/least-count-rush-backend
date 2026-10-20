"""Verify store purchases, persist every attempt, and grant ads-free from store expiry."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import IapTransaction, User
from app.services.iap_catalog import is_allowed_product, plan_id_for_product
from app.services.iap_store_clients import DefaultStoreVerifier, StoreVerificationResult, StoreVerifier

logger = logging.getLogger(__name__)

_store_verifier: StoreVerifier = DefaultStoreVerifier()
_REFRESH_MIN_INTERVAL = timedelta(hours=6)
_REFRESH_IF_WITHIN = timedelta(hours=36)


def set_store_verifier(verifier: StoreVerifier) -> None:
    global _store_verifier
    _store_verifier = verifier


class IapVerificationError(Exception):
    def __init__(self, message: str, status_code: int = 400, extra: Optional[dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.extra = extra or {}

    def http_detail(self):
        if not self.extra:
            return self.message
        return {"message": self.message, **self.extra}


def _sanitize_payload(raw: dict) -> dict:
    if not raw:
        return {}
    # Keep audit fields but drop oversized receipt blobs if present.
    clone = dict(raw)
    clone.pop("latest_receipt", None)
    clone.pop("latest_receipt_info", None)
    return clone


async def _other_account_error(db: AsyncSession, txn: IapTransaction) -> IapVerificationError:
    owner = await db.get(User, txn.user_idn)
    expires = txn.expires_dt
    if expires is None and owner is not None:
        expires = owner.ads_free_until
    extra = {
        "code": "purchase_linked_to_other_account",
        "owner_email": owner.email if owner else None,
        "owner_name": (owner.display_name or owner.user_name) if owner else None,
        "plan_id": txn.plan_id,
        "expires_dt": expires.isoformat() if expires else None,
        "platform": txn.platform,
    }
    return IapVerificationError(
        "This purchase is already linked to another account",
        status_code=409,
        extra=extra,
    )


async def _find_other_account_txn(
    db: AsyncSession,
    *,
    user_idn: int,
    platform: str,
    purchase_token: str,
    store_transaction_id: str,
) -> Optional[IapTransaction]:
    result = await db.execute(
        select(IapTransaction).where(
            IapTransaction.platform == platform,
            IapTransaction.verification_status == "verified",
            IapTransaction.user_idn != user_idn,
            or_(
                IapTransaction.store_transaction_id == store_transaction_id,
                IapTransaction.purchase_token == purchase_token,
            ),
        ).limit(1)
    )
    return result.scalar_one_or_none()


async def _record_transaction(
    db: AsyncSession,
    *,
    user_idn: int,
    platform: str,
    product_id: str,
    plan_id: Optional[str],
    purchase_token: str,
    store_transaction_id: str,
    original_transaction_id: Optional[str],
    status: str,
    source: str,
    environment: Optional[str],
    purchase_dt: Optional[datetime],
    expires_dt: Optional[datetime],
    acknowledged: bool,
    error_message: Optional[str],
    store_payload: Optional[dict],
) -> tuple[IapTransaction, bool]:
    result = await db.execute(
        select(IapTransaction).where(
            IapTransaction.platform == platform,
            IapTransaction.store_transaction_id == store_transaction_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        if existing.user_idn != user_idn and existing.verification_status == "verified":
            raise await _other_account_error(db, existing)
        existing.user_idn = user_idn
        existing.product_id = product_id
        existing.plan_id = plan_id
        existing.purchase_token = purchase_token
        existing.original_transaction_id = original_transaction_id
        existing.verification_status = status
        existing.source = source
        existing.environment = environment
        existing.purchase_dt = purchase_dt
        existing.expires_dt = expires_dt
        existing.acknowledged = acknowledged
        existing.error_message = error_message
        existing.store_payload = store_payload
        existing.upd_dt = datetime.now(timezone.utc)
        await db.flush()
        return existing, True

    row = IapTransaction(
        user_idn=user_idn,
        platform=platform,
        product_id=product_id,
        plan_id=plan_id,
        purchase_token=purchase_token,
        store_transaction_id=store_transaction_id,
        original_transaction_id=original_transaction_id,
        verification_status=status,
        source=source,
        environment=environment,
        purchase_dt=purchase_dt,
        expires_dt=expires_dt,
        acknowledged=acknowledged,
        error_message=error_message,
        store_payload=store_payload,
    )
    db.add(row)
    await db.flush()
    return row, False


async def verify_and_record(
    db: AsyncSession,
    user: User,
    *,
    platform: str,
    product_id: str,
    plan_id_hint: Optional[str],
    purchase_id: Optional[str],
    verification_data: str,
    source: str,
) -> dict:
    if platform not in ("android", "ios"):
        raise IapVerificationError("Unsupported platform")
    if source not in ("purchase", "restore", "renew", "verify"):
        source = "verify"
    if not verification_data or not verification_data.strip():
        raise IapVerificationError("Missing store verification data")
    if not is_allowed_product(platform, product_id):
        raise IapVerificationError("Unknown product")

    try:
        if platform == "android":
            store = await _store_verifier.verify_android(verification_data.strip(), product_id)
        else:
            store = await _store_verifier.verify_ios(
                verification_data.strip(), product_id, purchase_id
            )
    except Exception as e:
        logger.exception("Store verification crashed")
        raise IapVerificationError("Store verification failed") from e

    if not store.ok:
        digest = hashlib.sha256(verification_data.encode("utf-8")).hexdigest()[:40]
        await _record_transaction(
            db,
            user_idn=user.user_idn,
            platform=platform,
            product_id=product_id,
            plan_id=plan_id_for_product(platform, product_id, plan_id_hint),
            purchase_token=digest,
            store_transaction_id=purchase_id or f"rejected-{digest}",
            original_transaction_id=None,
            status="rejected",
            source=source,
            environment=None,
            purchase_dt=None,
            expires_dt=None,
            acknowledged=False,
            error_message=store.error,
            store_payload=_sanitize_payload(store.raw),
        )
        await db.commit()
        raise IapVerificationError(store.error or "Store verification failed")

    if not is_allowed_product(platform, store.product_id):
        raise IapVerificationError("Store product is not an ad-free subscription for this app")

    plan_id = store.plan_id or plan_id_for_product(platform, store.product_id, plan_id_hint)
    other = await _find_other_account_txn(
        db,
        user_idn=user.user_idn,
        platform=platform,
        purchase_token=store.purchase_token,
        store_transaction_id=store.store_transaction_id,
    )
    if other:
        raise await _other_account_error(db, other)
    status = "verified" if store.is_active else "expired"
    txn, already = await _record_transaction(
        db,
        user_idn=user.user_idn,
        platform=platform,
        product_id=store.product_id,
        plan_id=plan_id,
        purchase_token=store.purchase_token,
        store_transaction_id=store.store_transaction_id,
        original_transaction_id=store.original_transaction_id,
        status=status,
        source=source,
        environment=store.environment,
        purchase_dt=store.purchase_dt,
        expires_dt=store.expires_dt,
        acknowledged=store.acknowledged,
        error_message=None if store.is_active else "Subscription is not active",
        store_payload=_sanitize_payload(store.raw),
    )

    if store.is_active and platform == "android" and not store.acknowledged:
        ack = await _store_verifier.acknowledge_android(store.product_id, store.purchase_token)
        if ack:
            txn.acknowledged = True

    await db.commit()
    iap_free, iap_until = iap_entitlement_from_txn(txn)
    return {
        "verified": store.ok and store.is_active,
        "already_recorded": already,
        "ads_free": iap_free,
        "ads_free_until": iap_until,
        "product_id": store.product_id,
        "plan_id": plan_id,
        "transaction_idn": txn.iap_transaction_idn,
        "expires_dt": store.expires_dt,
        "store_transaction_id": store.store_transaction_id,
    }


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def latest_verified_transaction(db: AsyncSession, user_idn: int) -> Optional[IapTransaction]:
    result = await db.execute(
        select(IapTransaction)
        .where(
            IapTransaction.user_idn == user_idn,
            IapTransaction.verification_status == "verified",
        )
        .order_by(IapTransaction.expires_dt.desc(), IapTransaction.upd_dt.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def iap_entitlement_from_txn(
    txn: Optional[IapTransaction],
    now: Optional[datetime] = None,
) -> tuple[bool, Optional[datetime]]:
    if txn is None or txn.verification_status != "verified":
        return False, None
    until = _as_utc(txn.expires_dt)
    if until is None:
        return True, None
    current = now or datetime.now(timezone.utc)
    if until <= current:
        return False, until
    return True, until


def _should_refresh_from_store(user: User, txn: IapTransaction, now: datetime) -> bool:
    until = _as_utc(txn.expires_dt)
    if until is None or until <= now:
        return True
    if until <= now + _REFRESH_IF_WITHIN:
        return True
    checked = _as_utc(txn.upd_dt) or _as_utc(txn.crt_dt)
    if checked is None:
        return True
    return (now - checked) >= _REFRESH_MIN_INTERVAL


async def refresh_entitlement_from_store(db: AsyncSession, user: User) -> User:
    """Re-query Google/Apple with the saved token so renewals update iap_transactions."""
    txn = await latest_verified_transaction(db, user.user_idn)
    if txn is None or not txn.purchase_token:
        return user
    now = datetime.now(timezone.utc)
    if not _should_refresh_from_store(user, txn, now):
        return user
    purchase_id = txn.original_transaction_id or txn.store_transaction_id
    try:
        await verify_and_record(
            db,
            user,
            platform=txn.platform,
            product_id=txn.product_id,
            plan_id_hint=txn.plan_id,
            purchase_id=purchase_id,
            verification_data=txn.purchase_token,
            source="renew",
        )
    except IapVerificationError as e:
        logger.warning(
            "Entitlement store refresh failed user=%s platform=%s: %s",
            user.user_idn,
            txn.platform,
            e.message,
        )
    await db.refresh(user)
    return user


async def list_user_transactions(db: AsyncSession, user_idn: int, limit: int = 50) -> list[IapTransaction]:
    result = await db.execute(
        select(IapTransaction)
        .where(IapTransaction.user_idn == user_idn)
        .order_by(IapTransaction.crt_dt.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_all_transactions(
    db: AsyncSession,
    *,
    user_idn: Optional[int] = None,
    platform: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
) -> list[IapTransaction]:
    stmt = select(IapTransaction).order_by(IapTransaction.crt_dt.desc()).limit(limit)
    if user_idn is not None:
        stmt = stmt.where(IapTransaction.user_idn == user_idn)
    if platform:
        stmt = stmt.where(IapTransaction.platform == platform)
    if status:
        stmt = stmt.where(IapTransaction.verification_status == status)
    result = await db.execute(stmt)
    return list(result.scalars().all())
