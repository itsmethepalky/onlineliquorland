import httpx

from .config import settings


def notify_customer_order_status(
    order_id: int,
    order_status: str,
    customer_id: int | None = None,
    order_no: str | None = None,
) -> None:
    function_url = (
        settings.SUPABASE_URL.rstrip("/")
        + "/functions/v1/notify-customer-order-status"
    )
    service_role_key = settings.SUPABASE_SERVICE_ROLE_KEY

    if not service_role_key:
        print(
            "CUSTOMER ORDER PUSH ERROR: Supabase service role is not configured",
            flush=True,
        )
        return

    payload = {"order_id": order_id, "status": order_status}
    if customer_id is not None and order_no is not None:
        payload.update(
            {
                "customer_id": customer_id,
                "order_no": order_no,
            }
        )

    try:
        response = httpx.post(
            function_url,
            headers={
                "Authorization": f"Bearer {service_role_key}",
                "apikey": service_role_key,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10.0,
        )
    except httpx.RequestError as exc:
        print(
            "CUSTOMER ORDER PUSH REQUEST ERROR:",
            type(exc).__name__,
            flush=True,
        )
        return

    if not response.is_success:
        print(
            "CUSTOMER ORDER PUSH FUNCTION ERROR:",
            response.status_code,
            flush=True,
        )
        return

    try:
        result = response.json()
    except ValueError:
        print(
            "CUSTOMER ORDER PUSH FUNCTION ERROR: invalid response",
            flush=True,
        )
        return

    print(
        "CUSTOMER ORDER PUSH RESULT:",
        f"sent={result.get('sent', 0)}",
        f"failed={result.get('failed', 0)}",
        flush=True,
    )
