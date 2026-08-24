"""
FCM (Firebase Cloud Messaging) service.

Uses firebase-admin SDK with the existing serviceAccountKey.json to:
  - Send to a single device token
  - Multicast to multiple tokens (up to 500 per batch)
  - Send to a topic (e.g. "all_users")
  - Dispatch a stored Notification row from the DB
"""
import os
import logging
from typing import Optional

import firebase_admin
from firebase_admin import credentials, messaging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Firebase Admin SDK initialisation
# ---------------------------------------------------------------------------

def _init_firebase_admin() -> bool:
    """Initialise the Firebase Admin SDK (idempotent)."""
    try:
        firebase_admin.get_app()
        return True  # already initialised
    except ValueError:
        pass

    # Prefer GOOGLE_APPLICATION_CREDENTIALS_JSON or BASE64 env var
    cred_b64 = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_BASE64")
    cred_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")

    try:
        if cred_b64 or cred_json:
            import json
            import base64
            content = base64.b64decode(cred_b64).decode('utf-8') if cred_b64 else (cred_json or "")
            cert_dict = json.loads(content)
            cred = credentials.Certificate(cert_dict)
            firebase_admin.initialize_app(cred)
            logger.info("firebase_admin_initialized", extra={"source": "env_json_or_b64"})
            return True
        else:
            sa_key_path = os.environ.get(
                "GOOGLE_APPLICATION_CREDENTIALS",
                os.path.join(os.path.dirname(__file__), "..", "..", "serviceAccountKey.json"),
            )
            sa_key_path = os.path.normpath(sa_key_path)

            cred = credentials.Certificate(sa_key_path)
            firebase_admin.initialize_app(cred)
            logger.info("firebase_admin_initialized", extra={"key_path": sa_key_path})
            return True
    except Exception as exc:
        logger.error("firebase_admin_init_failed", extra={"error": str(exc)})
        return False


_ADMIN_READY = _init_firebase_admin()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def send_to_token(
    token: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> bool:
    """Send a notification to a single FCM registration token."""
    if not _ADMIN_READY:
        logger.warning("fcm_not_ready_skip_send_to_token")
        return False
    try:
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={str(k): str(v) for k, v in (data or {}).items()},
            token=token,
            android=messaging.AndroidConfig(priority="high"),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(sound="default", content_available=True)
                )
            ),
        )
        response = messaging.send(message)
        logger.info("fcm_sent_to_token", extra={"message_id": response})
        return True
    except Exception as exc:
        logger.error("fcm_send_to_token_failed", extra={"error": str(exc)})
        return False


def send_to_tokens(
    tokens: list[str],
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> tuple[int, int]:
    """
    Multicast to up to 500 tokens at once.

    Returns (success_count, failure_count).
    """
    if not _ADMIN_READY or not tokens:
        return 0, 0 if not tokens else len(tokens)

    # Deduplicate to prevent sending multiple notifications to the same device
    tokens = list(set(tokens))

    # FCM multicast limit is 500 tokens per call
    success_total, failure_total = 0, 0
    for i in range(0, len(tokens), 500):
        batch = tokens[i : i + 500]
        try:
            message = messaging.MulticastMessage(
                notification=messaging.Notification(title=title, body=body),
                data={str(k): str(v) for k, v in (data or {}).items()},
                tokens=batch,
                android=messaging.AndroidConfig(priority="high"),
                apns=messaging.APNSConfig(
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(sound="default", content_available=True)
                    )
                ),
            )
            resp = messaging.send_each_for_multicast(message)
            success_total += resp.success_count
            failure_total += resp.failure_count
            logger.info(
                "fcm_multicast_batch",
                extra={
                    "batch_size": len(batch),
                    "success": resp.success_count,
                    "failure": resp.failure_count,
                },
            )
        except Exception as exc:
            logger.error("fcm_multicast_batch_failed", extra={"error": str(exc)})
            failure_total += len(batch)

    return success_total, failure_total


def send_to_topic(
    topic: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> bool:
    """Send a notification to a Firebase topic (e.g. 'all_users')."""
    if not _ADMIN_READY:
        logger.warning("fcm_not_ready_skip_send_to_topic")
        return False
    try:
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={str(k): str(v) for k, v in (data or {}).items()},
            topic=topic,
            android=messaging.AndroidConfig(priority="high"),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(sound="default", content_available=True)
                )
            ),
        )
        response = messaging.send(message)
        logger.info("fcm_sent_to_topic", extra={"topic": topic, "message_id": response})
        return True
    except Exception as exc:
        logger.error("fcm_send_to_topic_failed", extra={"error": str(exc)})
        return False


async def dispatch_notification(notification_idn: int, db: AsyncSession) -> bool:
    """
    Load a Notification row from DB, fire FCM, update status.

    Returns True if FCM delivery succeeded.
    """
    from app.models.db_models import Notification, NotifStatusEnum, UserDevice
    from datetime import datetime, timezone

    result = await db.execute(
        select(Notification).where(Notification.notification_idn == notification_idn)
    )
    notif = result.scalar_one_or_none()
    if not notif:
        logger.error("dispatch_notification_not_found", extra={"idn": notification_idn})
        return False

    title = notif.title
    body = notif.description or ""
    data = {"type": "broadcast", "notification_idn": str(notification_idn)}
    ok = False

    if notif.receiver_user_idn:
        # Targeted: fetch all active FCM tokens for this user
        tokens_result = await db.execute(
            select(UserDevice.fcm_token).where(
                UserDevice.user_idn == notif.receiver_user_idn,
                UserDevice.entity_active == True,
                UserDevice.fcm_token.isnot(None),
            )
        )
        tokens = [row[0] for row in tokens_result.all() if row[0]]
        if tokens:
            data["type"] = "targeted"
            success, _ = send_to_tokens(tokens, title, body, data)
            ok = success > 0
        else:
            logger.warning(
                "dispatch_notification_no_tokens",
                extra={"user_idn": notif.receiver_user_idn},
            )
    elif notif.receiver_user_topic:
        ok = send_to_topic(notif.receiver_user_topic, title, body, data)

    # Update notification status
    notif.status = NotifStatusEnum.sent if ok else NotifStatusEnum.failed
    notif.upd_dt = datetime.now(timezone.utc)
    await db.commit()

    return ok
