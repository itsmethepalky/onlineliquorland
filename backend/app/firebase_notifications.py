import httpx

from .config import settings


def notify_admin_new_order(order_id: int) -> None:
    """Request centralized, idempotent push delivery from Supabase."""
    function_url = (
        settings.SUPABASE_URL.rstrip("/")
        + "/functions/v1/notify-admin-new-order"
    )
    service_role_key = settings.SUPABASE_SERVICE_ROLE_KEY

    if not service_role_key:
        print(
            "ADMIN ORDER PUSH ERROR: Supabase service role is not configured",
            flush=True,
        )
        return

    try:
        response = httpx.post(
            function_url,
            headers={
                "Authorization": f"Bearer {service_role_key}",
                "apikey": service_role_key,
                "Content-Type": "application/json",
            },
            json={"order_id": order_id},
            timeout=10.0,
        )
    except httpx.RequestError as exc:
        print(
            "ADMIN ORDER PUSH REQUEST ERROR:",
            type(exc).__name__,
            flush=True,
        )
        return

    if not response.is_success:
        print(
            "ADMIN ORDER PUSH FUNCTION ERROR:",
            response.status_code,
            flush=True,
        )
        return

    try:
        result = response.json()
    except ValueError:
        print(
            "ADMIN ORDER PUSH FUNCTION ERROR: invalid response",
            flush=True,
        )
        return

    if result.get("failed"):
        print(
            "ADMIN ORDER PUSH PARTIAL FAILURE:",
            f"sent={result.get('sent', 0)}",
            f"failed={result['failed']}",
            flush=True,
        )
