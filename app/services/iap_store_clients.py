"""Google Play and App Store verification clients."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

import httpx
import jwt

from app.config import settings
from app.services.iap_catalog import (
    GOOGLE_ACTIVE_STATES,
    plan_id_for_product,
)

logger = logging.getLogger(__name__)

APPLE_PROD = "https://api.storekit.itunes.apple.com"
APPLE_SANDBOX = "https://api.storekit-sandbox.itunes.apple.com"
APPLE_VERIFY_PROD = "https://buy.itunes.apple.com/verifyReceipt"
APPLE_VERIFY_SANDBOX = "https://sandbox.itunes.apple.com/verifyReceipt"


def _ms_to_dt(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str) and value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        if isinstance(value, str) and "T" in value:
            return datetime.fromisoformat(value)
        millis = int(value)
        if millis > 10_000_000_000:
            millis = millis / 1000.0
        return datetime.fromtimestamp(millis, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def extract_google_purchase_token(raw: str) -> str:
    """Accept a raw Play token or the Billing Library originalJson blob."""
    token = (raw or "").strip()
    if token.startswith("{") and "purchaseToken" in token:
        try:
            data = json.loads(token)
            nested = data.get("purchaseToken")
            if nested:
                return str(nested).strip()
        except json.JSONDecodeError:
            return token
    return token


def play_api_error_message(exc: Exception) -> str:
    status = None
    detail = str(exc)
    try:
        from googleapiclient.errors import HttpError

        if isinstance(exc, HttpError):
            status = exc.resp.status
            try:
                body = json.loads(exc.content.decode("utf-8"))
                detail = (body.get("error") or {}).get("message") or detail
            except Exception:
                pass
    except ImportError:
        pass
    logger.warning("Google Play API error status=%s detail=%s", status, detail)
    if status in (401, 403):
        return (
            "Google Play API permission denied. In Play Console invite the service "
            "account (Users and permissions) with View financial data / View app "
            "information, and enable the Google Play Android Developer API on the GCP project."
        )
    if status == 404:
        return (
            "Google Play did not find this subscription. Confirm the package name, "
            "that a build is uploaded to a Play testing track, and Restore Purchase."
        )
    if status == 400:
        return "Google Play rejected this purchase token. Complete the buy in the Play Store, then tap Restore Purchase."
    return "Google Play could not verify this purchase"


def normalize_pem(raw: str) -> str:
    """Turn env-escaped or header-stripped private key text into a PEM block."""
    key = (raw or "").strip().strip('"').strip("'")
    key = key.replace("\\n", "\n").replace("\\r", "").replace("\r\n", "\n")
    if "BEGIN" in key and "END" in key:
        if not key.endswith("\n"):
            key += "\n"
        return key
    body = "".join(key.split())
    if not body:
        return ""
    lines = [body[i : i + 64] for i in range(0, len(body), 64)]
    return "-----BEGIN PRIVATE KEY-----\n" + "\n".join(lines) + "\n-----END PRIVATE KEY-----\n"


def decode_jws_payload_unverified(jws: str) -> dict:
    """Extract claims from a JWS. Signature is not trusted; Apple/Google APIs must confirm."""
    parts = jws.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except Exception:
        return {}


@dataclass
class StoreVerificationResult:
    ok: bool
    product_id: str = ""
    plan_id: Optional[str] = None
    purchase_token: str = ""
    store_transaction_id: str = ""
    original_transaction_id: Optional[str] = None
    purchase_dt: Optional[datetime] = None
    expires_dt: Optional[datetime] = None
    is_active: bool = False
    environment: Optional[str] = None
    acknowledged: bool = False
    raw: dict = field(default_factory=dict)
    error: Optional[str] = None


class StoreVerifier(Protocol):
    async def verify_android(
        self, purchase_token: str, product_id: str
    ) -> StoreVerificationResult: ...

    async def verify_ios(
        self,
        verification_data: str,
        product_id: str,
        purchase_id: Optional[str] = None,
    ) -> StoreVerificationResult: ...

    async def acknowledge_android(self, product_id: str, purchase_token: str) -> bool: ...


class GooglePlayVerifier:
    def __init__(self):
        self._service = None
        self._init_error: Optional[str] = None
        self._init_service()

    def _credentials_info(self) -> Optional[dict]:
        raw = (settings.iap_google_service_account_json or "").strip()
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                self._init_error = "IAP_GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON"
                return None
        path = (settings.iap_google_service_account_file or "").strip()
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return None

    def _init_service(self):
        info = self._credentials_info()
        if not info:
            if not self._init_error:
                self._init_error = "Google Play service account is not configured"
            return
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            creds = service_account.Credentials.from_service_account_info(
                info,
                scopes=["https://www.googleapis.com/auth/androidpublisher"],
            )
            self._service = build(
                "androidpublisher", "v3", credentials=creds, cache_discovery=False
            )
            self._init_error = None
        except Exception as e:
            self._init_error = f"Failed to init Google Play API: {e}"
            logger.error(self._init_error)

    async def verify_android(
        self, purchase_token: str, product_id: str
    ) -> StoreVerificationResult:
        if self._service is None:
            self._init_service()
        if self._service is None:
            return StoreVerificationResult(ok=False, error=self._init_error)

        token = extract_google_purchase_token(purchase_token)
        package = settings.iap_google_package_name
        logger.info(
            "Google Play verify package=%s product=%s token_len=%s",
            package,
            product_id,
            len(token),
        )

        def _fetch_v2():
            return (
                self._service.purchases()
                .subscriptionsv2()
                .get(packageName=package, token=token)
                .execute()
            )

        def _fetch_v1():
            return (
                self._service.purchases()
                .subscriptions()
                .get(packageName=package, subscriptionId=product_id, token=token)
                .execute()
            )

        payload = None
        used_v1 = False
        last_error: Optional[Exception] = None
        try:
            payload = await asyncio.to_thread(_fetch_v2)
        except Exception as e:
            last_error = e
            logger.warning("Google Play subscriptions v2 failed: %s", e)
            try:
                payload = await asyncio.to_thread(_fetch_v1)
                used_v1 = True
            except Exception as e2:
                last_error = e2
                logger.warning("Google Play subscriptions v1 failed: %s", e2)

        if payload is None:
            return StoreVerificationResult(
                ok=False,
                error=play_api_error_message(last_error) if last_error else "Google Play could not verify this purchase",
            )

        if used_v1:
            return self._result_from_v1(payload, token, product_id)
        return self._result_from_v2(payload, token, product_id)

    def _result_from_v2(
        self, payload: dict, purchase_token: str, product_id: str
    ) -> StoreVerificationResult:
        line_items = payload.get("lineItems") or []
        item = line_items[0] if line_items else {}
        store_product = item.get("productId") or product_id
        offer = item.get("offerDetails") or {}
        base_plan = offer.get("basePlanId")
        state = payload.get("subscriptionState")
        expiry = _ms_to_dt(item.get("expiryTime"))
        start = _ms_to_dt(payload.get("startTime"))
        order_id = payload.get("latestOrderId") or purchase_token
        ack = payload.get("acknowledgementState") == "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED"
        now = datetime.now(timezone.utc)
        is_active = state in GOOGLE_ACTIVE_STATES and (expiry is None or expiry > now)
        return StoreVerificationResult(
            ok=True,
            product_id=store_product,
            plan_id=plan_id_for_product("android", store_product, base_plan),
            purchase_token=purchase_token,
            store_transaction_id=str(order_id),
            original_transaction_id=str(order_id).rsplit("..", 1)[0] if order_id else purchase_token,
            purchase_dt=start,
            expires_dt=expiry,
            is_active=bool(is_active),
            environment=payload.get("regionCode"),
            acknowledged=ack,
            raw=payload,
        )

    def _result_from_v1(
        self, payload: dict, purchase_token: str, product_id: str
    ) -> StoreVerificationResult:
        expiry = _ms_to_dt(payload.get("expiryTimeMillis"))
        start = _ms_to_dt(payload.get("startTimeMillis"))
        order_id = payload.get("orderId") or purchase_token
        payment_state = payload.get("paymentState")
        now = datetime.now(timezone.utc)
        is_active = (expiry is None or expiry > now) and payment_state in (1, 2, None)
        return StoreVerificationResult(
            ok=True,
            product_id=product_id,
            plan_id=plan_id_for_product("android", product_id),
            purchase_token=purchase_token,
            store_transaction_id=str(order_id),
            original_transaction_id=str(order_id).rsplit("..", 1)[0] if order_id else purchase_token,
            purchase_dt=start,
            expires_dt=expiry,
            is_active=bool(is_active),
            environment=None,
            acknowledged=payload.get("acknowledgementState") == 1,
            raw=payload,
        )

    async def acknowledge_android(self, product_id: str, purchase_token: str) -> bool:
        if self._service is None:
            return False

        def _ack():
            return (
                self._service.purchases()
                .subscriptions()
                .acknowledge(
                    packageName=settings.iap_google_package_name,
                    subscriptionId=product_id,
                    token=purchase_token,
                    body={},
                )
                .execute()
            )

        try:
            await asyncio.to_thread(_ack)
            return True
        except Exception as e:
            logger.warning("Google Play acknowledge failed: %s", e)
            return False


class AppleStoreVerifier:
    def _private_key(self) -> str:
        path = (settings.iap_apple_private_key_file or "").strip()
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return normalize_pem(f.read())
        return normalize_pem(settings.iap_apple_private_key or "")

    def _app_store_jwt(self) -> Optional[str]:
        pem = self._private_key()
        if not (
            settings.iap_apple_issuer_id
            and settings.iap_apple_key_id
            and pem
        ):
            return None
        now = int(datetime.now(timezone.utc).timestamp())
        try:
            return jwt.encode(
                {
                    "iss": settings.iap_apple_issuer_id,
                    "iat": now,
                    "exp": now + 1800,
                    "aud": "appstoreconnect-v1",
                    "bid": settings.iap_apple_bundle_id,
                },
                pem,
                algorithm="ES256",
                headers={"alg": "ES256", "kid": settings.iap_apple_key_id, "typ": "JWT"},
            )
        except Exception as e:
            logger.error("Failed to sign App Store JWT (check IAP_APPLE_PRIVATE_KEY PEM): %s", e)
            return None

    async def verify_ios(
        self,
        verification_data: str,
        product_id: str,
        purchase_id: Optional[str] = None,
    ) -> StoreVerificationResult:
        transaction_id = purchase_id
        if verification_data.count(".") == 2:
            claims = decode_jws_payload_unverified(verification_data)
            transaction_id = transaction_id or claims.get("transactionId")
        lookup_error = "Could not determine Apple transaction id"
        if transaction_id and self._app_store_jwt():
            result = await self._get_transaction_info(str(transaction_id))
            if result.ok:
                return result
            lookup_error = result.error or lookup_error
            if result.error and "not configured" in result.error:
                return result
        if settings.iap_apple_shared_secret:
            return await self._verify_receipt(verification_data, product_id, purchase_id)
        if not self._app_store_jwt():
            return StoreVerificationResult(
                ok=False,
                error="App Store API credentials are not configured",
            )
        return StoreVerificationResult(ok=False, error=lookup_error)

    async def _get_transaction_info(self, transaction_id: str) -> StoreVerificationResult:
        token = self._app_store_jwt()
        if not token:
            return StoreVerificationResult(ok=False, error="App Store API credentials are not configured")
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=20.0) as client:
            for base, env_name in ((APPLE_PROD, "Production"), (APPLE_SANDBOX, "Sandbox")):
                url = f"{base}/inApps/v1/transactions/{transaction_id}"
                try:
                    resp = await client.get(url, headers=headers)
                except httpx.HTTPError as e:
                    logger.warning("Apple transaction lookup failed: %s", e)
                    continue
                if resp.status_code == 404:
                    continue
                if resp.status_code >= 400:
                    logger.warning("Apple transaction lookup %s: %s", resp.status_code, resp.text[:300])
                    continue
                body = resp.json()
                signed = body.get("signedTransactionInfo") or ""
                claims = decode_jws_payload_unverified(signed)
                if not claims:
                    return StoreVerificationResult(ok=False, error="Apple returned an empty transaction")
                return self._result_from_apple_claims(claims, env_name, body)
        return StoreVerificationResult(ok=False, error="Apple could not find this transaction")

    def _result_from_apple_claims(
        self, claims: dict, environment: str, raw: dict
    ) -> StoreVerificationResult:
        bundle = claims.get("bundleId")
        if bundle and bundle != settings.iap_apple_bundle_id:
            return StoreVerificationResult(
                ok=False,
                error="Apple bundle id does not match this app",
                raw=claims,
            )
        product_id = claims.get("productId") or ""
        expires = _ms_to_dt(claims.get("expiresDate"))
        purchased = _ms_to_dt(claims.get("purchaseDate"))
        now = datetime.now(timezone.utc)
        revoked = claims.get("revocationDate") is not None
        is_active = (not revoked) and (expires is None or expires > now)
        txn_id = str(claims.get("transactionId") or "")
        original = str(claims.get("originalTransactionId") or txn_id)
        return StoreVerificationResult(
            ok=True,
            product_id=product_id,
            plan_id=plan_id_for_product("ios", product_id),
            purchase_token=original or txn_id,
            store_transaction_id=txn_id or original,
            original_transaction_id=original,
            purchase_dt=purchased,
            expires_dt=expires,
            is_active=is_active,
            environment=claims.get("environment") or environment,
            raw={"claims": claims, "api": raw},
        )

    async def _verify_receipt(
        self,
        receipt_data: str,
        product_id: str,
        purchase_id: Optional[str],
    ) -> StoreVerificationResult:
        payload = {
            "receipt-data": receipt_data,
            "password": settings.iap_apple_shared_secret,
            "exclude-old-transactions": True,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(APPLE_VERIFY_PROD, json=payload)
            data = resp.json()
            if data.get("status") == 21007:
                resp = await client.post(APPLE_VERIFY_SANDBOX, json=payload)
                data = resp.json()
        if data.get("status") != 0:
            return StoreVerificationResult(
                ok=False,
                error=f"Apple receipt status {data.get('status')}",
                raw={"status": data.get("status")},
            )
        infos = data.get("latest_receipt_info") or data.get("receipt", {}).get("in_app") or []
        chosen = None
        for info in infos:
            if purchase_id and str(info.get("transaction_id")) == str(purchase_id):
                chosen = info
                break
            if info.get("product_id") == product_id:
                chosen = info
        if chosen is None and infos:
            chosen = max(infos, key=lambda i: int(i.get("purchase_date_ms") or 0))
        if not chosen:
            return StoreVerificationResult(ok=False, error="Apple receipt has no matching product")
        bundle = (data.get("receipt") or {}).get("bundle_id")
        if bundle and bundle != settings.iap_apple_bundle_id:
            return StoreVerificationResult(ok=False, error="Apple bundle id does not match this app")
        expires = _ms_to_dt(chosen.get("expires_date_ms"))
        purchased = _ms_to_dt(chosen.get("purchase_date_ms"))
        now = datetime.now(timezone.utc)
        product = chosen.get("product_id") or product_id
        txn_id = str(chosen.get("transaction_id") or "")
        original = str(chosen.get("original_transaction_id") or txn_id)
        env = "Sandbox" if data.get("receipt", {}).get("receipt_type") == "ProductionSandbox" else "Production"
        return StoreVerificationResult(
            ok=True,
            product_id=product,
            plan_id=plan_id_for_product("ios", product),
            purchase_token=original or txn_id,
            store_transaction_id=txn_id or original,
            original_transaction_id=original,
            purchase_dt=purchased,
            expires_dt=expires,
            is_active=expires is None or expires > now,
            environment=env,
            raw={"receipt_status": 0, "transaction": chosen},
        )


class DefaultStoreVerifier:
    def __init__(self):
        self.google = GooglePlayVerifier()
        self.apple = AppleStoreVerifier()

    async def verify_android(
        self, purchase_token: str, product_id: str
    ) -> StoreVerificationResult:
        return await self.google.verify_android(purchase_token, product_id)

    async def verify_ios(
        self,
        verification_data: str,
        product_id: str,
        purchase_id: Optional[str] = None,
    ) -> StoreVerificationResult:
        return await self.apple.verify_ios(verification_data, product_id, purchase_id)

    async def acknowledge_android(self, product_id: str, purchase_token: str) -> bool:
        return await self.google.acknowledge_android(product_id, purchase_token)
