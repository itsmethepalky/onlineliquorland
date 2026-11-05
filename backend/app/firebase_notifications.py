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
        known_errors = {
            "Push notification service is not configured.",
            "Could not load the order.",
            "Could not load push registrations.",
            "Could not verify admin registrations.",
            "Firebase is not configured correctly.",
            "Firebase authorization failed.",
        }
        try:
            response_body = response.json()
        except ValueError:
            response_body = None

        error_message = (
            response_body.get("error")
            if isinstance(response_body, dict)
            else None
        )
        if error_message not in known_errors:
            error_message = "unclassified function response"

        print(
            "ADMIN ORDER PUSH FUNCTION ERROR:",
            response.status_code,
            error_message,
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
