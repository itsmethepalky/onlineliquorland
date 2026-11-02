import hashlib
import hmac
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from postgrest.types import ReturnMethod
from starlette.concurrency import run_in_threadpool

from ..config import settings
from ..supabase import supabase_admin


router = APIRouter(
    prefix="/api/webhooks",
    tags=["Webhooks"],
)

logger = logging.getLogger(__name__)
MAX_WEBHOOK_BODY_BYTES = 1_048_576
MAX_WEBHOOK_EVENTS = 1_000
DATABASE_BATCH_SIZE = 100
WEBHOOK_EVENTS_TABLE = "whatsapp_webhook_events"


def _secret(value: str) -> str:
    return value.strip()


def _verify_signature(body: bytes, signature: str) -> bool:
    app_secret = _secret(settings.WHATSAPP_APP_SECRET)
    if not app_secret:
        raise HTTPException(
            status_code=503,
            detail="WhatsApp webhook signature verification is not configured.",
        )

    match = re.fullmatch(r"sha256=([0-9a-fA-F]{64})", signature.strip())
    if not match:
        return False

    expected = hmac.new(
        app_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, match.group(1).lower())


def _validate_webhook_configuration() -> None:
    missing = []
    if not _secret(settings.WHATSAPP_APP_SECRET):
        missing.append("WHATSAPP_APP_SECRET")
    if not _secret(settings.WHATSAPP_PHONE_NUMBER_ID):
        missing.append("WHATSAPP_PHONE_NUMBER_ID")
    if not _secret(settings.WHATSAPP_BUSINESS_ACCOUNT_ID):
        missing.append("WHATSAPP_BUSINESS_ACCOUNT_ID")
    if missing:
        logger.error(
            "WhatsApp webhook is not configured; missing %s.",
            ", ".join(missing),
        )
        raise HTTPException(
            status_code=503,
            detail="WhatsApp webhook security is not fully configured.",
        )


async def _read_limited_body(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_WEBHOOK_BODY_BYTES:
            raise HTTPException(
                status_code=413,
                detail="WhatsApp webhook payload is too large.",
            )
    return bytes(body)


def _event_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    try:
        seconds = int(str(value))
        return datetime.fromtimestamp(
            seconds,
            tz=timezone.utc,
        ).isoformat()
    except (OverflowError, OSError, TypeError, ValueError):
        return None


def _is_expected_asset(
    business_account_id: str | None,
    phone_number_id: str | None,
) -> bool:
    expected_business_account_id = _secret(
        settings.WHATSAPP_BUSINESS_ACCOUNT_ID
    )
    expected_phone_number_id = _secret(
        settings.WHATSAPP_PHONE_NUMBER_ID
    )
    if (
        expected_business_account_id
        and business_account_id != expected_business_account_id
    ):
        return False
    if (
        expected_phone_number_id
        and phone_number_id != expected_phone_number_id
    ):
        return False
    return True


def _build_event_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("object") != "whatsapp_business_account":
        raise HTTPException(
            status_code=400,
            detail="Unsupported WhatsApp webhook object.",
        )

    rows: list[dict[str, Any]] = []
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        business_account_id = entry.get("id")
        if not isinstance(business_account_id, str):
            business_account_id = None
        for change in entry.get("changes") or []:
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue

            metadata = value.get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {}
            phone_number_id = metadata.get("phone_number_id")
            if not isinstance(phone_number_id, str):
                phone_number_id = None
            if not _is_expected_asset(
                business_account_id,
                phone_number_id,
            ):
                logger.warning(
                    "Ignoring WhatsApp webhook event for an unconfigured asset."
                )
                continue

            contacts_by_id = {}
            for contact in value.get("contacts") or []:
                if isinstance(contact, dict) and isinstance(
                    contact.get("wa_id"),
                    str,
                ):
                    contacts_by_id[contact["wa_id"]] = contact

            for message in value.get("messages") or []:
                if not isinstance(message, dict):
                    continue
                message_id = message.get("id")
                if not isinstance(message_id, str) or not message_id:
                    logger.warning(
                        "Ignoring WhatsApp message without an id."
                    )
                    continue

                sender_wa_id = message.get("from")
                if not isinstance(sender_wa_id, str):
                    sender_wa_id = None
                message_type = message.get("type")
                if not isinstance(message_type, str):
                    message_type = None
                contact = (
                    contacts_by_id.get(sender_wa_id)
                    if sender_wa_id
                    else None
                )
                rows.append(
                    {
                        "event_key": f"message:{message_id}",
                        "event_type": "incoming_message",
                        "business_account_id": business_account_id,
                        "phone_number_id": phone_number_id,
                        "message_id": message_id,
                        "message_status": None,
                        "event_timestamp": _event_timestamp(
                            message.get("timestamp")
                        ),
                        "sender_wa_id": sender_wa_id,
                        "recipient_wa_id": None,
                        "message_type": message_type,
                        "payload": {
                            "message": message,
                            "contact": contact,
                        },
                    }
                )

            for status_update in value.get("statuses") or []:
                if not isinstance(status_update, dict):
                    continue
                message_id = status_update.get("id")
                status = status_update.get("status")
                recipient_wa_id = status_update.get("recipient_id")
                if (
                    not isinstance(message_id, str)
                    or not message_id
                    or not isinstance(status, str)
                    or not status
                ):
                    logger.warning(
                        "Ignoring WhatsApp status update with missing identifiers."
                    )
                    continue
                status_timestamp = status_update.get("timestamp")
                event_timestamp = _event_timestamp(status_timestamp)
                timestamp_key = (
                    str(status_timestamp)
                    if status_timestamp is not None
                    else "unknown"
                )
                rows.append(
                    {
                        "event_key": (
                            f"status:{message_id}:{status}:{timestamp_key}"
                        ),
                        "event_type": "message_status",
                        "business_account_id": business_account_id,
                        "phone_number_id": phone_number_id,
                        "message_id": message_id,
                        "message_status": status,
                        "event_timestamp": event_timestamp,
                        "sender_wa_id": None,
                        "recipient_wa_id": (
                            recipient_wa_id
                            if isinstance(recipient_wa_id, str)
                            else None
                        ),
                        "message_type": None,
                        "payload": {"status": status_update},
                    }
                )

    if len(rows) > MAX_WEBHOOK_EVENTS:
        raise HTTPException(
            status_code=413,
            detail="WhatsApp webhook contains too many events.",
        )
    return rows


def _store_events(rows: list[dict[str, Any]]) -> None:
    for offset in range(0, len(rows), DATABASE_BATCH_SIZE):
        batch = rows[offset:offset + DATABASE_BATCH_SIZE]
        (
            supabase_admin
            .table(WEBHOOK_EVENTS_TABLE)
            .upsert(
                batch,
                on_conflict="event_key",
                ignore_duplicates=True,
                returning=ReturnMethod.minimal,
            )
            .execute()
        )


@router.get(
    "/whatsapp",
    response_class=PlainTextResponse,
    include_in_schema=False,
)
def verify_whatsapp_webhook(
    mode: str | None = Query(default=None, alias="hub.mode"),
    verify_token: str | None = Query(
        default=None,
        alias="hub.verify_token",
    ),
    challenge: str | None = Query(
        default=None,
        alias="hub.challenge",
    ),
):
    configured_token = _secret(settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN)
    if not configured_token:
        raise HTTPException(
            status_code=503,
            detail="WhatsApp webhook verification is not configured.",
        )
    if (
        mode != "subscribe"
        or not verify_token
        or not hmac.compare_digest(
            verify_token.encode("utf-8"),
            configured_token.encode("utf-8"),
        )
        or challenge is None
    ):
        raise HTTPException(
            status_code=403,
            detail="WhatsApp webhook verification failed.",
        )
    return PlainTextResponse(challenge)


@router.post(
    "/whatsapp",
    include_in_schema=False,
)
async def receive_whatsapp_webhook(request: Request):
    _validate_webhook_configuration()
    body = await _read_limited_body(request)
    signature = request.headers.get("x-hub-signature-256", "")
    if not _verify_signature(body, signature):
        logger.warning("Rejected WhatsApp webhook with invalid signature.")
        raise HTTPException(
            status_code=401,
            detail="Invalid WhatsApp webhook signature.",
        )

    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid WhatsApp webhook JSON.",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail="Invalid WhatsApp webhook payload.",
        )

    rows = _build_event_rows(payload)
    if rows:
        try:
            await run_in_threadpool(_store_events, rows)
        except Exception as exc:
            logger.error(
                "Could not persist WhatsApp webhook events (%s).",
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="WhatsApp webhook events could not be stored.",
            ) from exc

    logger.info(
        "Accepted WhatsApp webhook with %d normalized event(s).",
        len(rows),
    )
    return {"status": "ok"}
