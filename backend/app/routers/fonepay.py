import base64
import hashlib
import hmac
import io
import json
import re
import secrets
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlsplit

import httpx
import qrcode
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import get_current_customer
from ..config import settings
from ..supabase import supabase_admin
from ..whatsapp_notifications import notify_admin_new_fonepay_order


router = APIRouter(tags=["Fonepay"])

FONEPAY_TIMEOUT_SECONDS = 15.0
FONEPAY_SUCCESS_STATUSES = {
    "00",
    "complete",
    "completed",
    "paid",
    "s00",
    "success",
    "successful",
}
FONEPAY_PENDING_STATUSES = {
    "initiated",
    "not_paid",
    "pending",
    "unpaid",
}
FONEPAY_FAILED_STATUSES = {
    "cancelled",
    "canceled",
    "expired",
    "failed",
    "failure",
}


class FonepayQrRequest(BaseModel):
    order_id: int = Field(gt=0)
    amount: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=12,
        decimal_places=2,
    )


class FonepayVerifyRequest(BaseModel):
    prn: str = Field(min_length=1, max_length=25)


def _require_fonepay_configuration() -> None:
    required = (
        settings.FONEPAY_MERCHANT_CODE,
        settings.FONEPAY_MERCHANT_SECRET,
        settings.FONEPAY_USERNAME,
        settings.FONEPAY_PASSWORD,
        settings.FONEPAY_API_BASE_URL,
    )
    if not all(value.strip() for value in required):
        raise HTTPException(
            status_code=503,
            detail="Fonepay Dynamic QR is not fully configured.",
        )

    parsed_base_url = urlsplit(settings.FONEPAY_API_BASE_URL)
    if parsed_base_url.scheme != "https" or not parsed_base_url.netloc:
        raise HTTPException(
            status_code=503,
            detail="Fonepay API base URL must use HTTPS.",
        )


def _fonepay_dv(message: str) -> str:
    secret = settings.FONEPAY_MERCHANT_SECRET.strip()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Fonepay signing secret is not configured.",
        )
    digest = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha512,
    )
    return digest.hexdigest()


def _qr_request_dv(
    amount: str,
    prn: str,
    merchant_code: str,
    remarks1: str,
    remarks2: str,
) -> str:
    return _fonepay_dv(
        ",".join((amount, prn, merchant_code, remarks1, remarks2))
    )


def _callback_dv(
    prn: str,
    amount: str,
    bid: str,
    uid: str,
    status: str,
    message: str,
    merchant_code: str,
) -> str:
    return _fonepay_dv(
        ",".join(
            (
                prn,
                amount,
                bid,
                uid,
                status,
                message,
                merchant_code,
            )
        )
    )


def _verification_dv(prn: str, merchant_code: str) -> str:
    return _fonepay_dv(f"{prn},{merchant_code}")


def _amount_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def _fonepay_url(path: str) -> str:
    return (
        f"{settings.FONEPAY_API_BASE_URL.rstrip('/')}/"
        f"{path.lstrip('/')}"
    )


def _decode_provider_response(response: httpx.Response) -> dict:
    if response.is_error:
        raise HTTPException(
            status_code=502,
            detail="Fonepay could not process the payment request.",
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="Fonepay returned an invalid response.",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=502,
            detail="Fonepay returned an invalid response.",
        )
    return payload


def _create_provider_qr(
    amount: Decimal,
    prn: str,
    order_no: str,
) -> dict:
    merchant_code = settings.FONEPAY_MERCHANT_CODE.strip()
    amount_value = _amount_text(amount)
    remarks1 = f"Online Liquor Land order {order_no}"[:160]
    remarks2 = "ONLINE LIQUOR LAND"
    payload = {
        "amount": amount_value,
        "prn": prn,
        "merchantCode": merchant_code,
        "remarks1": remarks1,
        "remarks2": remarks2,
        "dataValidation": _qr_request_dv(
            amount_value,
            prn,
            merchant_code,
            remarks1,
            remarks2,
        ),
        "username": settings.FONEPAY_USERNAME.strip(),
        "password": settings.FONEPAY_PASSWORD.strip(),
    }

    try:
        response = httpx.post(
            _fonepay_url(settings.FONEPAY_QR_GENERATE_PATH),
            json=payload,
            timeout=FONEPAY_TIMEOUT_SECONDS,
        )
    except httpx.RequestError as exc:
        print(
            "FONEPAY QR REQUEST ERROR:",
            type(exc).__name__,
            flush=True,
        )
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not reach Fonepay. Retry this order to check or "
                "regenerate its QR code."
            ),
        ) from exc

    result = _decode_provider_response(response)
    response_status = str(result.get("status", "")).strip().lower()
    response_code = str(result.get("statusCode", "")).strip().upper()
    successful = (
        result.get("success") is True
        or response_status in {"success", "successful"}
        or response_code in {"0", "00", "S00"}
    )
    qr_message = result.get("qrMessage")

    if not successful or not isinstance(qr_message, str) or not qr_message.strip():
        print(
            "FONEPAY QR GENERATION REJECTED:",
            json.dumps(
                {
                    "status": result.get("status"),
                    "statusCode": result.get("statusCode"),
                },
                default=str,
            ),
            flush=True,
        )
        raise HTTPException(
            status_code=502,
            detail="Fonepay did not return a usable payment QR code.",
        )

    return result


def _qr_data_uri(qr_message: str) -> str:
    image = qrcode.make(qr_message)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _provider_payment_status(prn: str, expected_amount: Decimal) -> tuple[str, dict]:
    _require_fonepay_configuration()
    merchant_code = settings.FONEPAY_MERCHANT_CODE.strip()
    payload = {
        "prn": prn,
        "merchantCode": merchant_code,
        "dataValidation": _verification_dv(prn, merchant_code),
        "username": settings.FONEPAY_USERNAME.strip(),
        "password": settings.FONEPAY_PASSWORD.strip(),
    }

    try:
        response = httpx.post(
            _fonepay_url(settings.FONEPAY_QR_VERIFY_PATH),
            json=payload,
            timeout=FONEPAY_TIMEOUT_SECONDS,
        )
    except httpx.RequestError as exc:
        print(
            "FONEPAY STATUS REQUEST ERROR:",
            type(exc).__name__,
            flush=True,
        )
        raise HTTPException(
            status_code=502,
            detail="Unable to verify the Fonepay payment right now.",
        ) from exc

    result = _decode_provider_response(response)
    response_prn = result.get("prn") or result.get("PRN")
    if not response_prn or str(response_prn) != prn:
        raise HTTPException(
            status_code=502,
            detail="Fonepay verified a different payment reference.",
        )

    response_merchant_code = (
        result.get("merchantCode") or result.get("merchant_code")
    )
    if (
        response_merchant_code
        and str(response_merchant_code) != merchant_code
    ):
        raise HTTPException(
            status_code=502,
            detail="Fonepay verified a different merchant.",
        )

    response_amount = result.get("amount", result.get("amt"))
    if response_amount is not None:
        try:
            verified_amount = Decimal(str(response_amount))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=502,
                detail="Fonepay returned an invalid payment amount.",
            ) from exc
        if (
            not verified_amount.is_finite()
            or verified_amount != expected_amount
        ):
            raise HTTPException(
                status_code=502,
                detail="Fonepay verified an unexpected payment amount.",
            )

    status_value = (
        result.get("paymentStatus")
        or result.get("transactionStatus")
    )
    status = str(status_value or "").strip().lower()
    if not status:
        generic_status = str(result.get("status") or "").strip().lower()
        if generic_status in FONEPAY_PENDING_STATUSES | FONEPAY_FAILED_STATUSES:
            status = generic_status

    if status in FONEPAY_SUCCESS_STATUSES:
        return "paid", result
    if status in FONEPAY_PENDING_STATUSES:
        return "pending", result
    if status in FONEPAY_FAILED_STATUSES:
        return "failed", result

    raise HTTPException(
        status_code=502,
        detail="Fonepay returned an unrecognized payment status.",
    )


def _load_payment_attempt(prn: str) -> dict:
    try:
        response = (
            supabase_admin
            .table("fonepay_qr_payments")
            .select(
                "prn,order_id,expected_amount,qr_message,status"
            )
            .eq("prn", prn)
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        print(
            "FONEPAY QR PAYMENT LOAD ERROR:",
            repr(exc),
            flush=True,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Dynamic QR payment storage is unavailable. "
                "Apply database migration 024_fonepay_dynamic_qr.sql."
            ),
        ) from exc

    if not response.data:
        raise HTTPException(
            status_code=404,
            detail="Fonepay payment reference was not found.",
        )
    return response.data


def _mark_payment_failed(prn: str) -> None:
    try:
        supabase_admin.table("fonepay_qr_payments").update(
            {"status": "failed"}
        ).eq("prn", prn).eq("status", "pending").execute()
    except Exception as exc:
        print("FONEPAY ATTEMPT UPDATE ERROR:", repr(exc), flush=True)
        raise HTTPException(
            status_code=503,
            detail="Unable to save the Fonepay payment result.",
        ) from exc


def _apply_verified_payment(
    prn: str,
    amount: Decimal,
    bid: str | None,
    uid: str | None,
) -> dict:
    try:
        response = supabase_admin.rpc(
            "apply_fonepay_qr_payment",
            {
                "p_prn": prn,
                "p_amount": _amount_text(amount),
                "p_bid": bid,
                "p_uid": uid,
            },
        ).execute()
    except Exception as exc:
        print(
            "FONEPAY QR PAYMENT APPLY ERROR:",
            repr(exc),
            flush=True,
        )
        raise HTTPException(
            status_code=503,
            detail="Payment was verified, but the order update failed.",
        ) from exc

    if not response.data:
        raise HTTPException(
            status_code=503,
            detail="Fonepay payment was verified but no order was updated.",
        )
    return response.data[0]


def _payment_result(order: dict) -> dict:
    return {
        "payment_status": order["payment_status"],
        "order_id": order["order_id"],
        "order_no": order["order_no"],
        "total_amount": str(order["total_amount"]),
        "amount_paid": str(order["amount_paid"]),
        "remaining_balance": str(order["remaining_balance"]),
    }


def _notify_paid_order(
    background_tasks: BackgroundTasks,
    prn: str,
    order: dict,
) -> None:
    background_tasks.add_task(
        notify_admin_new_fonepay_order,
        prn,
        int(order["order_id"]),
    )


@router.post("/api/checkout/fonepay-qr")
def create_fonepay_qr(
    payload: FonepayQrRequest,
    current=Depends(get_current_customer),
):
    _require_fonepay_configuration()
    customer_id = current["customer"]["id"]
    try:
        order_response = (
            supabase_admin
            .table("orders")
            .select(
                "id,order_no,customer_id,status,total_amount,amount_paid,"
                "payment_status,payment_method"
            )
            .eq("id", payload.order_id)
            .eq("customer_id", customer_id)
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        print("FONEPAY ORDER LOAD ERROR:", repr(exc), flush=True)
        raise HTTPException(
            status_code=503,
            detail="Unable to load the order for payment.",
        ) from exc

    order = order_response.data
    if not order:
        raise HTTPException(status_code=404, detail="Order was not found.")
    if order["payment_method"] != "fonepay":
        raise HTTPException(
            status_code=400,
            detail="This order is not configured for Fonepay payment.",
        )
    if order["status"] == "cancelled":
        raise HTTPException(
            status_code=409,
            detail="Cancelled orders cannot be paid.",
        )
    if order["payment_status"] == "paid":
        raise HTTPException(
            status_code=409,
            detail="This order has already been paid.",
        )

    total_amount = Decimal(str(order["total_amount"]))
    amount_paid = Decimal(str(order.get("amount_paid") or 0))
    outstanding_amount = total_amount - amount_paid
    if outstanding_amount <= 0:
        raise HTTPException(
            status_code=409,
            detail="This order has no outstanding balance.",
        )

    try:
        existing_response = (
            supabase_admin
            .table("fonepay_qr_payments")
            .select("prn,expected_amount,qr_message,status")
            .eq("order_id", payload.order_id)
            .eq("status", "pending")
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        print("FONEPAY QR ATTEMPT LOOKUP ERROR:", repr(exc), flush=True)
        raise HTTPException(
            status_code=503,
            detail=(
                "Dynamic QR payment storage is unavailable. "
                "Apply database migration 024_fonepay_dynamic_qr.sql."
            ),
        ) from exc

    attempt = existing_response.data
    amount_to_pay = (
        payload.amount
        if payload.amount is not None
        else (
            Decimal(str(attempt["expected_amount"]))
            if attempt
            else outstanding_amount
        )
    )
    if amount_to_pay > outstanding_amount:
        raise HTTPException(
            status_code=400,
            detail="Payment cannot exceed the outstanding order balance.",
        )
    if amount_to_pay < outstanding_amount and amount_to_pay < Decimal("300"):
        raise HTTPException(
            status_code=400,
            detail="Partial Fonepay payments must be at least NPR 300.",
        )

    if attempt:
        if Decimal(str(attempt["expected_amount"])) != amount_to_pay:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This order already has an outstanding QR payment "
                    "for a different amount. Complete or verify it first."
                ),
            )
        if attempt.get("qr_message"):
            return {
                "success": True,
                "prn": attempt["prn"],
                "order_id": order["id"],
                "order_no": order["order_no"],
                "amount": _amount_text(amount_to_pay),
                "qr_message": attempt["qr_message"],
                "qr_image": _qr_data_uri(attempt["qr_message"]),
            }
        prn = attempt["prn"]
    else:
        prn = f"OLL{secrets.token_hex(10)}"
        try:
            supabase_admin.table("fonepay_qr_payments").insert(
                {
                    "prn": prn,
                    "order_id": payload.order_id,
                    "expected_amount": _amount_text(amount_to_pay),
                    "status": "pending",
                }
            ).execute()
        except Exception as exc:
            print("FONEPAY QR ATTEMPT CREATE ERROR:", repr(exc), flush=True)
            if "fonepay_qr_one_pending_per_order" in repr(exc):
                raise HTTPException(
                    status_code=409,
                    detail="A payment QR is already being prepared for this order.",
                ) from exc
            raise HTTPException(
                status_code=503,
                detail=(
                    "Unable to start the payment attempt. "
                    "Apply database migration 024_fonepay_dynamic_qr.sql."
                ),
            ) from exc

    result = _create_provider_qr(
        amount_to_pay,
        prn,
        order["order_no"],
    )
    qr_message = result["qrMessage"].strip()
    try:
        supabase_admin.table("fonepay_qr_payments").update(
            {"qr_message": qr_message}
        ).eq("prn", prn).execute()
    except Exception as exc:
        print("FONEPAY QR RESPONSE SAVE ERROR:", repr(exc), flush=True)
        raise HTTPException(
            status_code=503,
            detail=(
                "Fonepay created a payment QR, but it could not be saved. "
                "Retry this order to retrieve the same payment reference."
            ),
        ) from exc

    return {
        "success": True,
        "prn": prn,
        "order_id": order["id"],
        "order_no": order["order_no"],
        "amount": _amount_text(amount_to_pay),
        "qr_message": qr_message,
        "qr_image": _qr_data_uri(qr_message),
    }


async def _read_callback_payload(request: Request) -> dict:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > 16_384:
                raise HTTPException(
                    status_code=413,
                    detail="Fonepay callback payload is too large.",
                )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid Fonepay callback request.",
            ) from exc

    content_type = request.headers.get("content-type", "").lower()
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 16_384:
            raise HTTPException(
                status_code=413,
                detail="Fonepay callback payload is too large.",
            )

    try:
        if "application/json" in content_type:
            def reject_duplicate_keys(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("Duplicate callback field")
                    result[key] = value
                return result

            payload = json.loads(
                body,
                parse_float=str,
                parse_int=str,
                object_pairs_hook=reject_duplicate_keys,
            )
        elif "application/x-www-form-urlencoded" in content_type:
            pairs = parse_qsl(
                body.decode("utf-8"),
                keep_blank_values=True,
                max_num_fields=16,
            )
            if len({key for key, _ in pairs}) != len(pairs):
                raise ValueError("Duplicate callback field")
            payload = dict(pairs)
        elif not body and request.query_params:
            payload = dict(request.query_params)
        else:
            raise HTTPException(
                status_code=415,
                detail="Unsupported Fonepay callback content type.",
            )
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid Fonepay callback payload.",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail="Invalid Fonepay callback payload.",
        )
    return {str(key): str(value) for key, value in payload.items()}


@router.post("/api/payments/fonepay-callback")
async def fonepay_callback(
    request: Request,
    background_tasks: BackgroundTasks,
):
    _require_fonepay_configuration()
    callback = await _read_callback_payload(request)
    required_fields = (
        "prn",
        "amount",
        "bid",
        "uid",
        "status",
        "msg",
        "merchantCode",
        "dv",
    )
    if any(field not in callback for field in required_fields):
        raise HTTPException(
            status_code=400,
            detail="Fonepay callback is missing required fields.",
        )

    prn = callback["prn"]
    amount_text = callback["amount"]
    if not prn or len(prn) > 25:
        raise HTTPException(
            status_code=400,
            detail="Invalid Fonepay payment reference.",
        )
    merchant_code = settings.FONEPAY_MERCHANT_CODE.strip()
    if callback["merchantCode"] != merchant_code:
        raise HTTPException(
            status_code=401,
            detail="Invalid Fonepay callback merchant.",
        )
    if not re.fullmatch(r"[a-fA-F0-9]{128}", callback["dv"]):
        raise HTTPException(
            status_code=401,
            detail="Invalid Fonepay callback signature.",
        )

    expected_dv = _callback_dv(
        prn,
        amount_text,
        callback["bid"],
        callback["uid"],
        callback["status"],
        callback["msg"],
        callback["merchantCode"],
    )
    if not hmac.compare_digest(expected_dv, callback["dv"].strip().lower()):
        raise HTTPException(
            status_code=401,
            detail="Invalid Fonepay callback signature.",
        )

    try:
        callback_amount = Decimal(amount_text)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid Fonepay callback amount.",
        ) from exc

    attempt = _load_payment_attempt(prn)
    expected_amount = Decimal(str(attempt["expected_amount"]))
    if not callback_amount.is_finite() or callback_amount != expected_amount:
        raise HTTPException(
            status_code=400,
            detail="Fonepay callback amount does not match the order.",
        )
    if attempt["status"] == "paid":
        return {"success": True, "payment_status": "paid"}
    if attempt["status"] != "pending":
        return {"success": True, "payment_status": attempt["status"]}

    callback_status = callback["status"].strip().lower()
    if callback_status in FONEPAY_FAILED_STATUSES:
        _mark_payment_failed(prn)
        return {"success": True, "payment_status": "failed"}
    if callback_status not in FONEPAY_SUCCESS_STATUSES:
        return {"success": True, "payment_status": "pending"}

    provider_status, _ = _provider_payment_status(
        prn,
        expected_amount,
    )
    if provider_status != "paid":
        return {"success": True, "payment_status": provider_status}

    result = _apply_verified_payment(
        prn,
        expected_amount,
        callback.get("bid"),
        callback.get("uid"),
    )
    _notify_paid_order(background_tasks, prn, result)
    return {"success": True, **_payment_result(result)}


@router.post("/api/payments/fonepay-verify")
def verify_fonepay_payment(
    payload: FonepayVerifyRequest,
    background_tasks: BackgroundTasks,
    current=Depends(get_current_customer),
):
    _require_fonepay_configuration()
    attempt = _load_payment_attempt(payload.prn)
    try:
        order_response = (
            supabase_admin
            .table("orders")
            .select("id,customer_id")
            .eq("id", attempt["order_id"])
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        print("FONEPAY VERIFY ORDER LOAD ERROR:", repr(exc), flush=True)
        raise HTTPException(
            status_code=503,
            detail="Unable to verify the order owner.",
        ) from exc

    order = order_response.data
    if not order or order["customer_id"] != current["customer"]["id"]:
        raise HTTPException(
            status_code=404,
            detail="Fonepay payment reference was not found.",
        )

    if attempt["status"] == "paid":
        paid_order_response = (
            supabase_admin
            .table("orders")
            .select(
                "id,order_no,total_amount,amount_paid,payment_status"
            )
            .eq("id", attempt["order_id"])
            .maybe_single()
            .execute()
        )
        if not paid_order_response.data:
            raise HTTPException(
                status_code=503,
                detail="The paid order could not be loaded.",
            )
        paid_order = paid_order_response.data
        result = {
            "order_id": paid_order["id"],
            "order_no": paid_order["order_no"],
            "total_amount": paid_order["total_amount"],
            "amount_paid": paid_order["amount_paid"],
            "remaining_balance": Decimal(str(paid_order["total_amount"]))
            - Decimal(str(paid_order["amount_paid"])),
            "payment_status": paid_order["payment_status"],
        }
        return {"success": True, "status": "paid", **_payment_result(result)}

    if attempt["status"] != "pending":
        return {"success": True, "status": attempt["status"]}

    expected_amount = Decimal(str(attempt["expected_amount"]))
    provider_status, provider_result = _provider_payment_status(
        payload.prn,
        expected_amount,
    )
    if provider_status == "pending":
        return {"success": True, "status": "pending"}
    if provider_status == "failed":
        _mark_payment_failed(payload.prn)
        return {"success": True, "status": "failed"}

    result = _apply_verified_payment(
        payload.prn,
        expected_amount,
        str(provider_result.get("bid") or "") or None,
        str(provider_result.get("uid") or "") or None,
    )
    _notify_paid_order(background_tasks, payload.prn, result)
    return {"success": True, "status": "paid", **_payment_result(result)}
