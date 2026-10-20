from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.future import select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.db_models import IapTransaction, User
from app.services import iap_service
from app.services.iap_store_clients import StoreVerificationResult


@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        user = User(
            user_idn=1,
            user_id="firebase-user-1",
            user_name="iap_user",
            display_name="IAP User",
            email="owner@example.com",
            user_type="authenticated",
            ads_free=False,
        )
        other = User(
            user_idn=2,
            user_id="firebase-user-2",
            user_name="iap_user_2",
            display_name="Other User",
            email="other@example.com",
            user_type="authenticated",
            ads_free=False,
        )
        session.add_all([user, other])
        await session.commit()

    async with async_session() as session:
        yield session

    await engine.dispose()


class FakeStore:
    def __init__(self, result: StoreVerificationResult):
        self.result = result
        self.ack_calls = []

    async def verify_android(self, purchase_token: str, product_id: str):
        return self.result

    async def verify_ios(self, verification_data: str, product_id: str, purchase_id=None):
        return self.result

    async def acknowledge_android(self, product_id: str, purchase_token: str):
        self.ack_calls.append((product_id, purchase_token))
        return True


def _active_android(expires_in_days=30) -> StoreVerificationResult:
    now = datetime.now(timezone.utc)
    return StoreVerificationResult(
        ok=True,
        product_id="ad_free_monthly",
        plan_id="yearly",
        purchase_token="gp-token-1",
        store_transaction_id="GPA.1234-5678",
        original_transaction_id="GPA.1234-5678",
        purchase_dt=now,
        expires_dt=now + timedelta(days=expires_in_days),
        is_active=True,
        environment="IN",
        acknowledged=False,
        raw={"subscriptionState": "SUBSCRIPTION_STATE_ACTIVE"},
    )


async def _user(db, user_idn=1) -> User:
    return await db.get(User, user_idn)


@pytest.mark.asyncio
async def test_verified_purchase_grants_ads_free_and_records_row(async_db):
    iap_service.set_store_verifier(FakeStore(_active_android()))
    user = await _user(async_db)
    result = await iap_service.verify_and_record(
        async_db,
        user,
        platform="android",
        product_id="ad_free_monthly",
        plan_id_hint="monthly",
        purchase_id="GPA.1234-5678",
        verification_data="gp-token-1",
        source="purchase",
    )
    assert result["verified"] is True
    assert result["ads_free"] is True
    assert result["plan_id"] == "yearly"
    assert result["already_recorded"] is False

    rows = (await async_db.execute(select(IapTransaction))).scalars().all()
    assert len(rows) == 1
    assert rows[0].verification_status == "verified"
    assert rows[0].store_transaction_id == "GPA.1234-5678"
    refreshed = await _user(async_db)
    assert refreshed.ads_free is False
    assert refreshed.ads_free_until is None


@pytest.mark.asyncio
async def test_subscribe_does_not_overwrite_manual_ads_free(async_db):
    iap_service.set_store_verifier(FakeStore(_active_android()))
    user = await _user(async_db)
    user.ads_free = True
    user.ads_free_until = None
    await async_db.commit()
    await iap_service.verify_and_record(
        async_db,
        user,
        platform="android",
        product_id="ad_free_monthly",
        plan_id_hint="yearly",
        purchase_id="GPA.1234-5678",
        verification_data="gp-token-1",
        source="purchase",
    )
    refreshed = await _user(async_db)
    assert refreshed.ads_free is True
    assert refreshed.ads_free_until is None


@pytest.mark.asyncio
async def test_duplicate_transaction_is_idempotent(async_db):
    iap_service.set_store_verifier(FakeStore(_active_android()))
    user = await _user(async_db)
    first = await iap_service.verify_and_record(
        async_db,
        user,
        platform="android",
        product_id="ad_free_monthly",
        plan_id_hint="yearly",
        purchase_id="GPA.1234-5678",
        verification_data="gp-token-1",
        source="purchase",
    )
    second = await iap_service.verify_and_record(
        async_db,
        user,
        platform="android",
        product_id="ad_free_monthly",
        plan_id_hint="yearly",
        purchase_id="GPA.1234-5678",
        verification_data="gp-token-1",
        source="restore",
    )
    assert first["transaction_idn"] == second["transaction_idn"]
    assert second["already_recorded"] is True
    rows = (await async_db.execute(select(IapTransaction))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_purchase_linked_to_another_user_is_rejected(async_db):
    iap_service.set_store_verifier(FakeStore(_active_android()))
    user1 = await _user(async_db, 1)
    await iap_service.verify_and_record(
        async_db,
        user1,
        platform="android",
        product_id="ad_free_monthly",
        plan_id_hint="yearly",
        purchase_id="GPA.1234-5678",
        verification_data="gp-token-1",
        source="purchase",
    )
    user2 = await _user(async_db, 2)
    with pytest.raises(iap_service.IapVerificationError) as exc:
        await iap_service.verify_and_record(
            async_db,
            user2,
            platform="android",
            product_id="ad_free_monthly",
            plan_id_hint="yearly",
            purchase_id="GPA.1234-5678",
            verification_data="gp-token-1",
            source="restore",
        )
    assert exc.value.status_code == 409
    assert exc.value.extra["code"] == "purchase_linked_to_other_account"
    assert exc.value.extra["owner_email"] == "owner@example.com"
    assert exc.value.extra["plan_id"] == "yearly"
    user2 = await _user(async_db, 2)
    assert user2.ads_free is False


@pytest.mark.asyncio
async def test_invalid_store_result_is_recorded_as_rejected(async_db):
    iap_service.set_store_verifier(
        FakeStore(StoreVerificationResult(ok=False, error="token invalid"))
    )
    user = await _user(async_db)
    with pytest.raises(iap_service.IapVerificationError):
        await iap_service.verify_and_record(
            async_db,
            user,
            platform="ios",
            product_id="ad_free_yearly",
            plan_id_hint="yearly",
            purchase_id="1001",
            verification_data="not-a-real-receipt",
            source="purchase",
        )
    rows = (await async_db.execute(select(IapTransaction))).scalars().all()
    assert len(rows) == 1
    assert rows[0].verification_status == "rejected"
    assert rows[0].error_message == "token invalid"
    user = await _user(async_db)
    assert user.ads_free is False


@pytest.mark.asyncio
async def test_client_plan_hint_is_ignored_when_store_returns_plan(async_db):
    iap_service.set_store_verifier(FakeStore(_active_android()))
    user = await _user(async_db)
    result = await iap_service.verify_and_record(
        async_db,
        user,
        platform="android",
        product_id="ad_free_monthly",
        plan_id_hint="monthly",
        purchase_id="GPA.1234-5678",
        verification_data="gp-token-1",
        source="purchase",
    )
    assert result["plan_id"] == "yearly"


@pytest.mark.asyncio
async def test_unknown_product_rejected_without_store_call(async_db):
    called = SimpleNamespace(n=0)

    class Guard(FakeStore):
        async def verify_android(self, purchase_token, product_id):
            called.n += 1
            return self.result

    iap_service.set_store_verifier(Guard(_active_android()))
    user = await _user(async_db)
    with pytest.raises(iap_service.IapVerificationError, match="Unknown product"):
        await iap_service.verify_and_record(
            async_db,
            user,
            platform="android",
            product_id="coins_pack",
            plan_id_hint=None,
            purchase_id="x",
            verification_data="token",
            source="purchase",
        )
    assert called.n == 0


def test_canonicalize_plan_id_aliases():
    from app.services.iap_catalog import canonicalize_plan_id

    assert canonicalize_plan_id("quarterly") == "quarterly"
    assert canonicalize_plan_id("3months") == "quarterly"
    assert canonicalize_plan_id("ad_free_6months") == "halfyearly"
    assert canonicalize_plan_id("p1y") == "yearly"


def test_pick_google_line_item_uses_latest_expiry():
    from datetime import datetime, timezone
    from app.services.iap_store_clients import pick_google_line_item

    older = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    newer = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
    item = pick_google_line_item(
        {
            "lineItems": [
                {
                    "productId": "ad_free_monthly",
                    "expiryTime": str(older),
                    "offerDetails": {"basePlanId": "quarterly"},
                },
                {
                    "productId": "ad_free_monthly",
                    "expiryTime": str(newer),
                    "offerDetails": {"basePlanId": "halfyearly"},
                },
            ]
        }
    )
    assert item["offerDetails"]["basePlanId"] == "halfyearly"


def test_extract_google_purchase_token_from_json_and_raw():
    from app.services.iap_store_clients import extract_google_purchase_token

    raw = "opaque-play-token"
    assert extract_google_purchase_token(raw) == "opaque-play-token"
    blob = '{"orderId":"GPA.1","packageName":"com.mrunknown.least_count_game","productId":"ad_free_monthly","purchaseToken":"nested-token"}'
    assert extract_google_purchase_token(blob) == "nested-token"


def test_normalize_pem_expands_escaped_newlines():
    from app.services.iap_store_clients import normalize_pem

    raw = "-----BEGIN PRIVATE KEY-----\\nABC\\n-----END PRIVATE KEY-----\\n"
    pem = normalize_pem(raw)
    assert "BEGIN PRIVATE KEY-----" in pem
    assert "\\n" not in pem
    assert pem.endswith("\n")


def test_normalize_pem_wraps_headerless_body():
    from app.services.iap_store_clients import normalize_pem

    pem = normalize_pem("MIGH")
    assert pem.startswith("-----BEGIN PRIVATE KEY-----")
    assert "MIGH" in pem


@pytest.mark.asyncio
async def test_store_crash_becomes_verification_error(async_db):
    class Boom:
        async def verify_ios(self, *args, **kwargs):
            raise ValueError("MalformedFraming")

        async def verify_android(self, *args, **kwargs):
            raise ValueError("MalformedFraming")

        async def acknowledge_android(self, *args, **kwargs):
            return False

    iap_service.set_store_verifier(Boom())
    user = await _user(async_db)
    with pytest.raises(iap_service.IapVerificationError, match="Store verification failed"):
        await iap_service.verify_and_record(
            async_db,
            user,
            platform="ios",
            product_id="ad_free_yearly",
            plan_id_hint="yearly",
            purchase_id="1001",
            verification_data="token",
            source="purchase",
        )


@pytest.mark.asyncio
async def test_refresh_entitlement_extends_expiry_after_renewal(async_db):
    now = datetime.now(timezone.utc)
    iap_service.set_store_verifier(FakeStore(_active_android(expires_in_days=30)))
    user = await _user(async_db)
    await iap_service.verify_and_record(
        async_db,
        user,
        platform="android",
        product_id="ad_free_monthly",
        plan_id_hint="yearly",
        purchase_id="GPA.1234-5678",
        verification_data="gp-token-1",
        source="purchase",
    )
    user = await _user(async_db)
    txn = await iap_service.latest_verified_transaction(async_db, user.user_idn)
    assert txn is not None
    txn.expires_dt = now - timedelta(days=1)
    await async_db.commit()

    renewed = now + timedelta(days=30)
    iap_service.set_store_verifier(
        FakeStore(
            StoreVerificationResult(
                ok=True,
                product_id="ad_free_monthly",
                plan_id="yearly",
                purchase_token="gp-token-1",
                store_transaction_id="GPA.1234-5678..1",
                original_transaction_id="GPA.1234-5678",
                purchase_dt=now,
                expires_dt=renewed,
                is_active=True,
                environment="IN",
                acknowledged=True,
                raw={"subscriptionState": "SUBSCRIPTION_STATE_ACTIVE"},
            )
        )
    )
    user = await _user(async_db)
    await iap_service.refresh_entitlement_from_store(async_db, user)
    user = await _user(async_db)
    assert user.ads_free is False
    txn = await iap_service.latest_verified_transaction(async_db, user.user_idn)
    assert txn is not None
    until = txn.expires_dt
    assert until is not None
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    assert until > now


@pytest.mark.asyncio
async def test_refresh_entitlement_skips_store_when_still_valid(async_db):
    called = SimpleNamespace(n=0)

    class Guard(FakeStore):
        async def verify_android(self, purchase_token, product_id):
            called.n += 1
            return self.result

    iap_service.set_store_verifier(Guard(_active_android(expires_in_days=200)))
    user = await _user(async_db)
    await iap_service.verify_and_record(
        async_db,
        user,
        platform="android",
        product_id="ad_free_monthly",
        plan_id_hint="yearly",
        purchase_id="GPA.1234-5678",
        verification_data="gp-token-1",
        source="purchase",
    )
    assert called.n == 1
    user = await _user(async_db)
    await iap_service.refresh_entitlement_from_store(async_db, user)
    assert called.n == 1
