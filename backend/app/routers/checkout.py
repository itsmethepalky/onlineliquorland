import base64
import hashlib
import hmac
import json
import math
import threading
import time
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Literal

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..auth import get_current_customer
from ..config import settings
from ..supabase import supabase_admin
from ..whatsapp_notifications import notify_admin_new_fonepay_order


router = APIRouter(
    prefix="/api/checkout",
    tags=["Checkout"],
)


FONEPAY_MERCHANT_ID = settings.FONEPAY_MERCHANT_ID
FONEPAY_MERCHANT_SECRET = settings.FONEPAY_MERCHANT_SECRET
FONEPAY_VERIFY_URL = settings.FONEPAY_VERIFY_URL
FONEPAY_MIN_INITIAL_PAYMENT = Decimal(
    settings.MINIMUM_INITIAL_PAYMENT or "300"
)
MAX_DELIVERY_DISTANCE_KM = Decimal("9")
EARTH_RADIUS_KM = 6371.0088

NOMINATIM_MIN_INTERVAL_SECONDS = 1.0
_nominatim_lock = threading.Lock()
_last_nominatim_request = 0.0


class CheckoutItem(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0)


class CheckoutRequest(BaseModel):
    phone: str = Field(min_length=7, max_length=30)
    shipping_address: str = Field(min_length=5, max_length=500)
    delivery_latitude: float = Field(ge=-90, le=90)
    delivery_longitude: float = Field(ge=-180, le=180)
    payment_method: Literal[
        "fonepay",
        "fonepay_partial",
        "fonepay_full",
    ] = "fonepay_full"
    initial_payment: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=12,
        decimal_places=2,
    )

    notes: str | None = Field(default=None, max_length=1000)

    # Customers may submit only a promo code.
    # Discount, delivery charge, and final total are always
    # calculated and verified server-side.
    promo_code: str | None = Field(
        default=None,
        max_length=100,
    )

    items: list[CheckoutItem] = Field(
        min_length=1,
        max_length=100,
    )


def _store_origin() -> tuple[float, float]:
    try:
        origin_latitude = float(settings.STORE_ORIGIN_LATITUDE)
        origin_longitude = float(settings.STORE_ORIGIN_LONGITUDE)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Delivery route service is not configured.",
        ) from exc

    if (
        not -90 <= origin_latitude <= 90
        or not -180 <= origin_longitude <= 180
    ):
        raise HTTPException(
            status_code=503,
            detail="Delivery route service is not configured.",
        )

    return origin_latitude, origin_longitude


def _nominatim_request(path: str, params: dict) -> httpx.Response:
    global _last_nominatim_request

    with _nominatim_lock:
        elapsed = time.monotonic() - _last_nominatim_request
        if elapsed < NOMINATIM_MIN_INTERVAL_SECONDS:
            time.sleep(NOMINATIM_MIN_INTERVAL_SECONDS - elapsed)

        headers = {
            "User-Agent": (
                "TheBlueMoonStore/1.0 "
                "(https://onlineliquorland.com"
                + (
                    f"; contact: {settings.STORE_CONTACT_EMAIL}"
                    if settings.STORE_CONTACT_EMAIL
                    else ""
                )
                + ")"
            ),
            "Accept": "application/json",
        }
        try:
            response = httpx.get(
                f"{settings.OSM_NOMINATIM_URL}{path}",
                params=params,
                headers=headers,
                timeout=6.0,
            )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=503,
                detail="Address search is temporarily unavailable.",
            ) from exc
        finally:
            _last_nominatim_request = time.monotonic()

    if response.is_error:
        raise HTTPException(
            status_code=503,
            detail="Address search is temporarily unavailable.",
        )
    return response


def _calculate_delivery_distance_km(
    latitude: float,
    longitude: float,
) -> Decimal:
    origin_latitude, origin_longitude = _store_origin()

    routing_urls = [settings.OSM_ROUTING_URL]
    fallback_url = settings.OSM_ROUTING_FALLBACK_URL
    if fallback_url and fallback_url not in routing_urls:
        routing_urls.append(fallback_url)

    response = None
    for routing_url in routing_urls:
        try:
            candidate = httpx.get(
                (
                    f"{routing_url}/route/v1/foot/"
                    f"{origin_longitude},{origin_latitude};"
                    f"{longitude},{latitude}"
                ),
                params={
                    "alternatives": "true",
                    "overview": "false",
                    "steps": "false",
                },
                timeout=8.0,
            )
        except httpx.RequestError as exc:
            print(
                "OSM ROUTE REQUEST ERROR:",
                type(exc).__name__,
                flush=True,
            )
            continue

        if candidate.is_error:
            print(
                "OSM ROUTE SERVICE ERROR:",
                candidate.status_code,
                flush=True,
            )
            continue

        response = candidate
        break

    if response is None:
        raise HTTPException(
            status_code=503,
            detail="Unable to calculate the delivery route. Please try again.",
        )

    try:
        routes = response.json().get("routes") or []
        route_distances = [
            Decimal(str(route["distance"]))
            for route in routes
        ]
    except (
        AttributeError,
        IndexError,
        KeyError,
        InvalidOperation,
        TypeError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status_code=422,
            detail="No walking route is available for this address.",
        ) from exc

    if not route_distances or any(
        not distance.is_finite() or distance < 0
        for distance in route_distances
    ):
        raise HTTPException(
            status_code=422,
            detail="No walking route is available for this address.",
        )

    distance_meters = min(route_distances)
    return distance_meters / Decimal("1000")


def _delivery_rpc_arguments(
    payload: CheckoutRequest,
) -> dict:
    origin_latitude, origin_longitude = _store_origin()
    latitude_delta = math.radians(
        payload.delivery_latitude - origin_latitude
    )
    longitude_delta = math.radians(
        payload.delivery_longitude - origin_longitude
    )
    origin_latitude_radians = math.radians(origin_latitude)
    destination_latitude_radians = math.radians(
        payload.delivery_latitude
    )
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(origin_latitude_radians)
        * math.cos(destination_latitude_radians)
        * math.sin(longitude_delta / 2) ** 2
    )
    radius_distance_km = (
        EARTH_RADIUS_KM
        * 2
        * math.asin(math.sqrt(min(1.0, haversine)))
    )
    if radius_distance_km > float(MAX_DELIVERY_DISTANCE_KM):
        raise HTTPException(
            status_code=422,
            detail=(
                "This delivery address is outside our service area. "
                "We deliver within a 9 km radius of the store."
            ),
        )

    distance_km = _calculate_delivery_distance_km(
        payload.delivery_latitude,
        payload.delivery_longitude,
    )

    return {
        "p_delivery_distance_km": str(distance_km),
        "distance_km": distance_km,
    }


def _checkout_migration_missing(exc: Exception) -> str | None:
    error_text = str(exc).lower()
    if "pgrst202" not in error_text:
        return None
    if "create_store_order_with_delivery_location" in error_text:
        return (
            "Delivery location storage is not installed. "
            "Apply database migration 015 in Supabase."
        )
    if (
        "preview_store_checkout_with_delivery" in error_text
        or "create_store_order_with_delivery" in error_text
    ):
        return (
            "Route-based delivery pricing is not installed. "
            "Apply database migration 014 in Supabase."
        )
    return None


def _format_decimal(value: Decimal | str | float | int) -> str:
    return format(Decimal(str(value)), "f")


def _fonepay_signature(payload: dict) -> str:
    if not FONEPAY_MERCHANT_SECRET:
        raise RuntimeError("FONEPAY_MERCHANT_SECRET is not configured")

    canonical = "|".join(
        str(payload.get(key, "")) for key in [
            "merchant_id",
            "amount",
            "transaction_id",
            "order_id",
            "payment_type",
        ]
    )
    digest = hmac.new(
        FONEPAY_MERCHANT_SECRET.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest


def _verify_fonepay_response(data: dict) -> bool:
    signature = data.get("signature")
    if not signature:
        return False
    expected = _fonepay_signature(
        {
            "merchant_id": data.get("merchant_id", ""),
            "amount": data.get("amount", ""),
            "transaction_id": data.get("transaction_id", ""),
            "order_id": data.get("order_id", ""),
            "payment_type": data.get("payment_type", ""),
        }
    )
    return hmac.compare_digest(expected, signature)


def _decode_fonepay_response(encoded: str) -> dict:
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        data = json.loads(decoded)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid Fonepay payment response.",
        ) from exc

    if not isinstance(data, dict):
        raise HTTPException(
            status_code=400,
            detail="Invalid Fonepay payment response.",
        )

    return data


def _check_fonepay_status(transaction_id: str) -> dict:
    if not FONEPAY_MERCHANT_ID or not FONEPAY_MERCHANT_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Fonepay payment verification is not configured.",
        )

    response = httpx.get(
        FONEPAY_VERIFY_URL,
        params={
            "merchant_id": FONEPAY_MERCHANT_ID,
            "transaction_id": transaction_id,
        },
        timeout=15.0,
    )

    if response.is_error:
        raise HTTPException(
            status_code=502,
            detail="Unable to verify the Fonepay transaction.",
        )

    try:
        result = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="Invalid response from Fonepay.",
        ) from exc

    return result


def _verified_checkout_migration_missing(exc: Exception) -> bool:
    error_text = repr(exc).lower()
    return (
        "fonepay_checkout_intents" in error_text
        or "finalize_fonepay_checkout" in error_text
    ) and any(
        marker in error_text
        for marker in ("pgrst202", "pgrst205", "42p01", "does not exist")
    )


def _load_fonepay_checkout(transaction_id: str):
    try:
        response = (
            supabase_admin
            .table("fonepay_checkout_intents")
            .select("transaction_id,amount_to_pay,order_id")
            .eq("transaction_id", transaction_id)
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        print(
            "FONEPAY CHECKOUT LOAD ERROR:",
            repr(exc),
            flush=True,
        )

        if _verified_checkout_migration_missing(exc):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Verified-payment order creation is not installed. "
                    "Apply database migration 019_fonepay_verified_order_creation.sql."
                ),
            ) from exc

        raise HTTPException(
            status_code=503,
            detail="Unable to load the pending Fonepay checkout.",
        ) from exc

    if not response.data:
        raise HTTPException(
            status_code=404,
            detail="Fonepay checkout transaction not found.",
        )

    return response.data


def _finalize_fonepay_checkout(
    transaction_id: str,
    amount: Decimal,
):
    try:
        response = supabase_admin.rpc(
            "finalize_fonepay_checkout",
            {
                "p_transaction_id": transaction_id,
                "p_amount_paid": str(amount),
            },
        ).execute()

    except Exception as exc:
        print(
            "FONEPAY ORDER FINALIZATION ERROR:",
            repr(exc),
            flush=True,
        )

        if _verified_checkout_migration_missing(exc):
            detail = (
                "Verified-payment order creation is not installed. "
                "Apply database migration 019_fonepay_verified_order_creation.sql."
            )
        else:
            detail = (
                "Payment was verified, but the order could not be finalized. "
                "Please contact the store before trying to pay again."
            )

        raise HTTPException(
            status_code=503,
            detail=detail,
        ) from exc

    return response


@router.get("/address-search")
def address_search(
    q: str = Query(min_length=3, max_length=120),
    current=Depends(get_current_customer),
):
    response = _nominatim_request(
        "/search",
        {
            "q": q.strip(),
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": 5,
            "countrycodes": "np",
            **(
                {"email": settings.STORE_CONTACT_EMAIL}
                if settings.STORE_CONTACT_EMAIL
                else {}
            ),
        },
    )
    try:
        results = response.json()
        return [
            {
                "display_name": item["display_name"],
                "latitude": float(item["lat"]),
                "longitude": float(item["lon"]),
            }
            for item in results
            if item.get("display_name")
        ]
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Address search returned an invalid response.",
        ) from exc


@router.get("/address-reverse")
def reverse_delivery_address(
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    current=Depends(get_current_customer),
):
    response = _nominatim_request(
        "/reverse",
        {
            "lat": latitude,
            "lon": longitude,
            "format": "jsonv2",
            "zoom": 18,
            **(
                {"email": settings.STORE_CONTACT_EMAIL}
                if settings.STORE_CONTACT_EMAIL
                else {}
            ),
        },
    )
    try:
        result = response.json()
        address = result.get("display_name")
    except (AttributeError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Address lookup returned an invalid response.",
        ) from exc

    if not address:
        raise HTTPException(
            status_code=404,
            detail="No address found at that map location.",
        )
    return {"display_name": address}



@router.post("/preview")
def preview_checkout(
    payload: CheckoutRequest,
    current=Depends(get_current_customer),
):
    """
    Preview checkout pricing without creating an order.

    The database function is authoritative for:
    - product prices
    - stock availability
    - delivery charge
    - promotion validation
    - product discount
    - free delivery
    - final total

    This endpoint performs no checkout side effects.
    """
    customer = current["customer"]

    items = [
        {
            "product_id": item.product_id,
            "quantity": item.quantity,
        }
        for item in payload.items
    ]

    # Fonepay is the only supported payment gateway.
    database_payment_method = "fonepay"
    delivery = _delivery_rpc_arguments(payload)

    try:
        response = supabase_admin.rpc(
            "preview_store_checkout_with_delivery",
            {
                "p_customer_id": customer["id"],
                "p_phone": payload.phone.strip(),
                "p_shipping_address": payload.shipping_address.strip(),
                "p_payment_method": database_payment_method,
                "p_items": items,
                "p_promo_code": (
                    payload.promo_code.strip()
                    if payload.promo_code
                    else None
                ),
                "p_delivery_distance_km": delivery[
                    "p_delivery_distance_km"
                ],
            },
        ).execute()

    except Exception as exc:
        error_text = str(exc).lower()

        print(
            "CHECKOUT PREVIEW ERROR:",
            repr(exc),
            flush=True,
        )

        migration_detail = _checkout_migration_missing(exc)
        if migration_detail:
            raise HTTPException(
                status_code=503,
                detail=migration_detail,
            ) from exc

        if "invalid payment method" in error_text:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Fonepay checkout pricing is not enabled in the database. "
                    "Apply migration 020_allow_fonepay_checkout_pricing.sql."
                ),
            ) from exc

        # These messages are generated by the authoritative
        # preview_store_checkout PostgreSQL function.
        if "invalid promo code" in error_text:
            raise HTTPException(
                status_code=400,
                detail="Invalid promo code.",
            ) from exc

        if "promo code is inactive" in error_text:
            raise HTTPException(
                status_code=400,
                detail="Promo code is inactive.",
            ) from exc

        if "promo code is not active yet" in error_text:
            raise HTTPException(
                status_code=400,
                detail="Promo code is not active yet.",
            ) from exc

        if "promo code has expired" in error_text:
            raise HTTPException(
                status_code=400,
                detail="Promo code has expired.",
            ) from exc

        if "promo code usage limit reached" in error_text:
            raise HTTPException(
                status_code=409,
                detail="Promo code usage limit reached.",
            ) from exc

        if "promo code customer usage limit reached" in error_text:
            raise HTTPException(
                status_code=409,
                detail="You have reached the usage limit for this promo.",
            ) from exc

        if "minimum order amount for this promo is" in error_text:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

        if "customer account not found" in error_text:
            raise HTTPException(
                status_code=401,
                detail="Customer account not found.",
            ) from exc

        if "insufficient stock for product" in error_text:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc

        if "product" in error_text and "no longer available" in error_text:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc

        if "credit limit exceeded" in error_text:
            raise HTTPException(
                status_code=409,
                detail="Customer credit limit exceeded.",
            ) from exc

        if "customer credit is not available" in error_text:
            raise HTTPException(
                status_code=400,
                detail="Customer credit is not available.",
            ) from exc

        if "store delivery charge is invalid" in error_text:
            raise HTTPException(
                status_code=503,
                detail="Store delivery charge is temporarily unavailable.",
            ) from exc

        raise HTTPException(
            status_code=500,
            detail="Unable to calculate checkout pricing.",
        ) from exc

    if not response.data:
        raise HTTPException(
            status_code=500,
            detail="Checkout pricing preview returned no data.",
        )

    result = response.data[0]

    return {
        "subtotal": result.get("subtotal", 0),
        "discount": result.get("discount", 0),
        "original_delivery_charge": result.get(
            "original_delivery_charge",
            result.get("delivery_charge", 0),
        ),
        "delivery_discount": result.get(
            "delivery_discount",
            0,
        ),
        "delivery_charge": result.get(
            "delivery_charge",
            0,
        ),
        "promo_code": result.get("promo_code"),
        "total_amount": result.get("total_amount", 0),
        "delivery_distance_km": str(delivery["distance_km"]),
    }


@router.post("/create")
def create_checkout_order(
    payload: CheckoutRequest,
    current=Depends(get_current_customer),
):
    customer = current["customer"]

    items = [
        {
            "product_id": item.product_id,
            "quantity": item.quantity,
        }
        for item in payload.items
    ]

    if payload.payment_method not in {
        "fonepay",
        "fonepay_partial",
        "fonepay_full",
    }:
        raise HTTPException(
            status_code=400,
            detail="Only Fonepay payments are supported.",
        )

    if not all(
        value.strip()
        for value in (
            settings.FONEPAY_MERCHANT_CODE,
            FONEPAY_MERCHANT_SECRET,
            settings.FONEPAY_USERNAME,
            settings.FONEPAY_PASSWORD,
        )
    ):
        raise HTTPException(
            status_code=503,
            detail="Fonepay payment is not configured.",
        )

    delivery = _delivery_rpc_arguments(payload)
    promo_code = (
        payload.promo_code.strip()
        if payload.promo_code
        else None
    )

    try:
        response = supabase_admin.rpc(
            "preview_store_checkout_with_delivery",
            {
                "p_customer_id": customer["id"],
                "p_phone": payload.phone.strip(),
                "p_shipping_address": payload.shipping_address.strip(),
                "p_payment_method": "fonepay",
                "p_items": items,
                "p_promo_code": promo_code,
                "p_delivery_distance_km": delivery[
                    "p_delivery_distance_km"
                ],
            },
        ).execute()

    except Exception as exc:
        print(
            "CHECKOUT PAYMENT PREPARATION ERROR:",
            repr(exc),
            flush=True,
        )
        error_text = str(exc).lower()
        migration_detail = _checkout_migration_missing(exc)

        if migration_detail:
            detail = migration_detail
            status_code = 503

        elif "pgrst202" in error_text and "preview_store_checkout_with_delivery" in error_text:
            detail = (
                "Route-based checkout preview is not installed. "
                "Apply database migration 015 in Supabase."
            )
            status_code = 503

        elif "insufficient stock" in error_text:
            detail = (
                "One or more products do not have enough stock."
            )
            status_code = 409

        elif "no longer available" in error_text:
            detail = (
                "One or more products are no longer available."
            )
            status_code = 409

        elif "cart is empty" in error_text:
            detail = "Your cart is empty."
            status_code = 400

        elif "customer account" in error_text:
            detail = (
                "Customer account could not be verified."
            )
            status_code = 403

        elif "invalid promo code" in error_text:
            detail = "The promo code is invalid."
            status_code = 400

        elif "promo code is inactive" in error_text:
            detail = "This promo code is currently inactive."
            status_code = 400

        elif "promo code has expired" in error_text:
            detail = "This promo code has expired."
            status_code = 400

        elif "promo code has not started" in error_text:
            detail = "This promo code is not active yet."
            status_code = 400

        elif "promo code usage limit reached" in error_text:
            detail = "This promo code has reached its usage limit."
            status_code = 409

        elif "promo code customer usage limit reached" in error_text:
            detail = (
                "You have already used this promo code the maximum "
                "number of times."
            )
            status_code = 409

        elif "minimum order amount" in error_text:
            detail = str(exc)
            status_code = 400

        else:
            detail = "Unable to prepare checkout for payment."
            status_code = 503

        raise HTTPException(
            status_code=status_code,
            detail=detail,
        ) from exc

    if not response.data:
        raise HTTPException(
            status_code=500,
            detail="Checkout pricing preview returned no data.",
        )

    result = response.data[0]
    total_amount = Decimal(str(result["total_amount"]))

    if total_amount <= 0:
        raise HTTPException(
            status_code=400,
            detail="Invalid order amount.",
        )

    if payload.payment_method in {"fonepay_partial", "fonepay"}:
        requested_amount = (
            payload.initial_payment
            or FONEPAY_MIN_INITIAL_PAYMENT
        )
        if requested_amount < FONEPAY_MIN_INITIAL_PAYMENT:
            raise HTTPException(
                status_code=400,
                detail="Initial payment must be at least NPR 300.",
            )
        if requested_amount > total_amount:
            raise HTTPException(
                status_code=400,
                detail="The initial payment cannot exceed the total order amount.",
                )
        requested_amount_to_pay = requested_amount
    else:
        requested_amount_to_pay = total_amount

    try:
        order_response = supabase_admin.rpc(
            "create_store_order_with_delivery_location",
            {
                "p_customer_id": customer["id"],
                "p_phone": payload.phone.strip(),
                "p_shipping_address": payload.shipping_address.strip(),
                "p_payment_method": "fonepay",
                "p_notes": payload.notes.strip() if payload.notes else None,
                "p_items": items,
                "p_promo_code": promo_code,
                "p_delivery_distance_km": delivery[
                    "p_delivery_distance_km"
                ],
                "p_delivery_latitude": payload.delivery_latitude,
                "p_delivery_longitude": payload.delivery_longitude,
            },
        ).execute()
    except Exception as exc:
        print(
            "FONEPAY PENDING ORDER CREATE ERROR:",
            repr(exc),
            flush=True,
        )

        error_text = str(exc).lower()
        if "insufficient stock" in error_text:
            detail = "One or more products do not have enough stock."
            status_code = 409
        elif "no longer available" in error_text:
            detail = "One or more products are no longer available."
            status_code = 409
        elif "promo code" in error_text:
            detail = "The promotion is no longer available. Review your cart and try again."
            status_code = 409
        elif (
            "pgrst202" in error_text
            and "create_store_order_with_delivery_location" in error_text
        ):
            detail = (
                "The delivery-location order function is not installed. "
                "Apply database migration 016 in Supabase."
            )
            status_code = 503
        elif (
            "fonepay" in error_text
            and (
                "payment_method" in error_text
                or "payment method" in error_text
            )
        ):
            detail = (
                "Fonepay checkout is not enabled in the database. "
                "Apply database migration 020 in Supabase."
            )
            status_code = 503
        else:
            detail = "Unable to create your pending order."
            status_code = 503

        raise HTTPException(
            status_code=status_code,
            detail=detail,
        ) from exc

    if not order_response.data:
        raise HTTPException(
            status_code=500,
            detail="Order creation returned no order.",
        )

    created_order = order_response.data[0]
    actual_total = Decimal(str(created_order["total_amount"]))
    amount_to_pay = min(requested_amount_to_pay, actual_total)
    payment_type = (
        "partial"
        if amount_to_pay < actual_total
        else "full"
    )

    return {
        "success": True,
        "payment_required": True,
        "payment_gateway": "fonepay",
        "order_id": created_order["order_id"],
        "order_no": created_order["order_no"],
        "order": {
            "subtotal": created_order["subtotal"],
            "discount": created_order["discount"],
            "delivery_charge": created_order["delivery_charge"],
            "promo_code": created_order.get("promo_code"),
            "delivery_distance_km": str(delivery["distance_km"]),
            "total_amount": actual_total,
            "amount_paid": Decimal("0.00"),
            "remaining_balance": actual_total,
            "payment_type": payment_type,
            "initial_payment_amount": amount_to_pay,
        },
        "amount_to_pay": _format_decimal(amount_to_pay),
    }


@router.get(
    "/fonepay/success",
    response_class=HTMLResponse,
)
def fonepay_success(
    background_tasks: BackgroundTasks,
    transaction_id: str | None = None,
    amount: str | None = None,
):
    if not transaction_id:
        return HTMLResponse(
            _result_page(
                False,
                "Fonepay did not return a payment response.",
            ),
            status_code=400,
        )

    checkout = _load_fonepay_checkout(transaction_id)

    status_payload = _check_fonepay_status(transaction_id)
    if str(status_payload.get("status", "")).lower() not in {"success", "complete", "paid"}:
        return HTMLResponse(
            _result_page(
                False,
                "Fonepay has not confirmed this payment.",
            ),
            status_code=400,
        )

    verified_transaction_id = status_payload.get("transaction_id")
    if (
        verified_transaction_id
        and str(verified_transaction_id) != transaction_id
    ):
        raise HTTPException(
            status_code=502,
            detail="Fonepay verified a different transaction.",
        )

    try:
        paid_amount = Decimal(str(status_payload["amount"]))
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Fonepay verification did not return a valid payment amount.",
        ) from exc

    try:
        expected_amount = Decimal(str(checkout["amount_to_pay"]))
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail="Stored Fonepay checkout amount is invalid.",
        ) from exc

    if (
        not paid_amount.is_finite()
        or paid_amount <= 0
        or paid_amount != expected_amount
    ):
        raise HTTPException(
            status_code=502,
            detail="Fonepay verified an unexpected payment amount.",
        )

    if amount:
        try:
            callback_amount = Decimal(str(amount))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid Fonepay payment amount.",
            ) from exc

        if callback_amount != paid_amount:
            raise HTTPException(
                status_code=400,
                detail="Fonepay payment amount does not match verification.",
            )

    finalized = _finalize_fonepay_checkout(
        transaction_id,
        paid_amount,
    )
    if not finalized.data:
        raise HTTPException(
            status_code=503,
            detail="Fonepay payment was verified but no order was finalized.",
        )

    order = finalized.data[0]
    background_tasks.add_task(
        notify_admin_new_fonepay_order,
        transaction_id,
        int(order["order_id"]),
    )
    remaining_balance = Decimal(str(order["remaining_balance"]))

    return HTMLResponse(
        _result_page(
            True,
            (
                "Partial payment successful. "
                f"Remaining balance: NPR "
                f"{_format_decimal(remaining_balance)}."
                if remaining_balance > 0
                else "Payment successful."
            ),
            order_no=order["order_no"],
        )
    )


@router.get(
    "/fonepay/failure",
    response_class=HTMLResponse,
)
def fonepay_failure():
    return HTMLResponse(
        _result_page(
            False,
            "The Fonepay payment was cancelled or could not be completed.",
        )
    )


def _result_page(
    success: bool,
    message: str,
    order_no: str | None = None,
) -> str:
    title = (
        "Payment Successful"
        if success
        else "Payment Not Completed"
    )

    color = (
        "#237a57"
        if success
        else "#a33b32"
    )

    order_text = ""

    if order_no:
        order_text = (
            f"<p><strong>Order:</strong> "
            f"{order_no}</p>"
        )

    return f"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport"
      content="width=device-width,initial-scale=1">
<title>{title} — ONLINE LIQUOR LAND</title>
<style>
body {{
    margin: 0;
    min-height: 100vh;
    display: grid;
    place-items: center;
    background: #f5f1e8;
    font-family: system-ui, sans-serif;
    color: #172033;
}}
.card {{
    width: min(92vw, 520px);
    box-sizing: border-box;
    padding: 36px;
    background: white;
    border-radius: 18px;
    text-align: center;
    box-shadow: 0 12px 40px rgba(0,0,0,.10);
}}
.icon {{
    font-size: 48px;
    color: {color};
}}
h1 {{
    margin: 12px 0;
}}
p {{
    line-height: 1.6;
}}
a {{
    display: inline-block;
    margin-top: 18px;
    padding: 12px 20px;
    border-radius: 10px;
    background: #172033;
    color: white;
    text-decoration: none;
}}
</style>
</head>
<body>
<div class="card">
    <div class="icon">
        {"✓" if success else "!"}
    </div>
    <h1>{title}</h1>
    <p>{message}</p>
    {order_text}
    <a href="/orders">View My Orders</a>
</div>
{"<script>try { localStorage.removeItem('blueMoonCart'); sessionStorage.removeItem('blueMoonDeliveryLocation'); } catch (error) { console.warn('Could not clear the completed checkout from this browser.'); }</script>" if success else ""}
</body>
</html>
"""
