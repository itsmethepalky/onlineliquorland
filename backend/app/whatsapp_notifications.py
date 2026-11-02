import re
from decimal import Decimal

import httpx

from .config import settings
from .supabase import supabase_admin


WHATSAPP_TIMEOUT_SECONDS = 10.0
WHATSAPP_MESSAGE_LIMIT = 3500


def _money(value) -> str:
    return format(Decimal(str(value or 0)), ",.2f")


def _clean_text(value) -> str:
    return " ".join(str(value or "").split())


def _notification_messages(
    order: dict,
    customer: dict,
    items: list[dict],
    message_limit: int = WHATSAPP_MESSAGE_LIMIT,
) -> list[str]:
    total = Decimal(str(order.get("total_amount") or 0))
    paid = Decimal(str(order.get("amount_paid") or 0))
    remaining = max(total - paid, Decimal("0"))
    payment_label = "PAID" if remaining == 0 else "PARTIALLY PAID"

    lines = [
        f"New Fonepay order: {order.get('order_no') or order['id']}",
        f"Customer: {_clean_text(customer.get('name')) or 'Unknown'}",
        f"Phone: {_clean_text(order.get('phone') or customer.get('phone')) or 'Not provided'}",
        f"Address: {_clean_text(order.get('shipping_address')) or 'Not provided'}",
    ]

    latitude = order.get("delivery_latitude")
    longitude = order.get("delivery_longitude")
    if latitude is not None and longitude is not None:
        lines.append(
            "Map: https://www.google.com/maps?q="
            f"{latitude},{longitude}"
        )
    else:
        lines.append("Map: Location coordinates unavailable")

    lines.extend(
        [
            f"Payment: {payment_label}",
            f"Paid: NPR {_money(paid)}",
            f"Remaining: NPR {_money(remaining)}",
            f"Order total: NPR {_money(total)}",
            "Products:",
        ]
    )

    product_lines = []
    for item in items:
        product = item.get("product") or {}
        product_name = _clean_text(product.get("name")) or (
            f"Product {item.get('product_id') or ''}".strip()
        )
        quantity = int(item.get("quantity") or 0)
        unit_price = Decimal(str(item.get("unit_price") or 0))
        subtotal = Decimal(str(item.get("subtotal") or unit_price * quantity))
        product_lines.append(
            f"- {product_name} x {quantity} "
            f"@ NPR {_money(unit_price)} = NPR {_money(subtotal)}"
        )

    continuation = (
        f"Order {order.get('order_no') or order['id']} "
        "details continued:\n"
    )
    messages = []
    current = ""
    for line in [*lines, *product_lines]:
        remaining = line
        while remaining:
            separator = "\n" if current else ""
            available = message_limit - len(current) - len(separator)
            if available <= 0:
                messages.append(current)
                current = continuation
                continue

            current += separator + remaining[:available]
            remaining = remaining[available:]
            if remaining:
                messages.append(current)
                current = continuation

    if current:
        messages.append(current)
    return messages


def _load_order_details(order_id: int) -> tuple[dict, dict, list[dict]]:
    order_response = (
        supabase_admin
        .table("orders")
        .select(
            "id,order_no,customer_id,phone,shipping_address,total_amount,"
            "amount_paid,payment_status,delivery_latitude,delivery_longitude"
        )
        .eq("id", order_id)
        .maybe_single()
        .execute()
    )
    order = order_response.data
    if not order:
        raise RuntimeError("Finalized order was not found for notification.")

    customer_response = (
        supabase_admin
        .table("customers")
        .select("id,name,phone")
        .eq("id", order["customer_id"])
        .maybe_single()
        .execute()
    )
    customer = customer_response.data or {}

    items_response = (
        supabase_admin
        .table("order_items")
        .select("product_id,quantity,unit_price,subtotal")
        .eq("order_id", order_id)
        .execute()
    )
    items = items_response.data or []

    product_ids = list({
        item["product_id"]
        for item in items
        if item.get("product_id") is not None
    })
    products = {}
    if product_ids:
        products_response = (
            supabase_admin
            .table("products")
            .select("id,name")
            .in_("id", product_ids)
            .execute()
        )
        products = {
            product["id"]: product
            for product in products_response.data or []
        }

    for item in items:
        item["product"] = products.get(item.get("product_id"))

    return order, customer, items


def _send_cloud_api_message(message: str) -> None:
    token = settings.WHATSAPP_CLOUD_API_TOKEN.strip()
    phone_number_id = settings.WHATSAPP_PHONE_NUMBER_ID.strip()
    version = settings.WHATSAPP_GRAPH_API_VERSION.strip()
    recipient = re.sub(r"\D", "", settings.WHATSAPP_ADMIN_PHONE)

    if not re.fullmatch(r"v\d+\.\d+", version):
        raise RuntimeError("Invalid WhatsApp Graph API version configuration.")
    if not recipient or not 8 <= len(recipient) <= 15:
        raise RuntimeError("Invalid WhatsApp admin recipient configuration.")

    template_name = settings.WHATSAPP_TEMPLATE_NAME.strip()
    if template_name:
        payload = {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {
                    "code": settings.WHATSAPP_TEMPLATE_LANGUAGE.strip(),
                },
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {
                                "type": "text",
                                "text": message,
                            }
                        ],
                    }
                ],
            },
        }
    else:
        payload = {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": message,
            },
        }

    response = httpx.post(
        f"https://graph.facebook.com/{version}/{phone_number_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=WHATSAPP_TIMEOUT_SECONDS,
    )
    if response.is_error:
        raise RuntimeError(
            f"WhatsApp Cloud API rejected a message (HTTP {response.status_code})."
        )


def notify_admin_new_fonepay_order(
    transaction_id: str,
    order_id: int,
) -> None:
    token = settings.WHATSAPP_CLOUD_API_TOKEN.strip()
    phone_number_id = settings.WHATSAPP_PHONE_NUMBER_ID.strip()
    if not token or not phone_number_id:
        print(
            "WHATSAPP ORDER NOTIFICATION DISABLED: configure "
            "WHATSAPP_CLOUD_API_TOKEN and WHATSAPP_PHONE_NUMBER_ID.",
            flush=True,
        )
        return

    claimed = False
    try:
        claim_response = supabase_admin.rpc(
            "claim_fonepay_whatsapp_notification",
            {"p_transaction_id": transaction_id},
        ).execute()
        claimed = bool(claim_response.data)
        if not claimed:
            return

        order, customer, items = _load_order_details(order_id)
        template_name = settings.WHATSAPP_TEMPLATE_NAME.strip()
        message_limit = 900 if template_name else WHATSAPP_MESSAGE_LIMIT
        for message in _notification_messages(
            order,
            customer,
            items,
            message_limit=message_limit,
        ):
            _send_cloud_api_message(message)

        sent_response = supabase_admin.rpc(
            "mark_fonepay_whatsapp_notification_sent",
            {"p_transaction_id": transaction_id},
        ).execute()
        if not sent_response.data:
            raise RuntimeError("Could not mark WhatsApp notification as sent.")
    except Exception as exc:
        if claimed:
            try:
                supabase_admin.rpc(
                    "release_fonepay_whatsapp_notification",
                    {"p_transaction_id": transaction_id},
                ).execute()
            except Exception as release_error:
                print(
                    "WHATSAPP NOTIFICATION CLAIM RELEASE ERROR:",
                    type(release_error).__name__,
                    flush=True,
                )
        print(
            "WHATSAPP ORDER NOTIFICATION ERROR:",
            type(exc).__name__,
            str(exc),
            flush=True,
        )
