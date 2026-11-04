import json
import os
from datetime import timedelta
from threading import Lock

from firebase_admin import credentials, get_app, initialize_app, messaging

from .supabase import supabase_admin


_FIREBASE_APP_NAME = "store-admin-push"
_firebase_lock = Lock()


def _firebase_app():
    try:
        return get_app(_FIREBASE_APP_NAME)
    except ValueError:
        pass

    service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if not service_account_json:
        raise RuntimeError("FIREBASE_SERVICE_ACCOUNT_JSON is not configured.")

    service_account = json.loads(service_account_json)
    with _firebase_lock:
        try:
            return get_app(_FIREBASE_APP_NAME)
        except ValueError:
            return initialize_app(
                credentials.Certificate(service_account),
                name=_FIREBASE_APP_NAME,
            )


def notify_admin_new_order(order_id: int) -> None:
    try:
        order_result = (
            supabase_admin
            .table("orders")
            .select("id,order_no,total_amount")
            .eq("id", order_id)
            .maybe_single()
            .execute()
        )
        order = order_result.data
        if not order:
            raise RuntimeError("New-order push could not find the order.")

        token_result = (
            supabase_admin
            .table("admin_push_tokens")
            .select("token,user_id")
            .execute()
        )
        registrations = token_result.data or []
        user_ids = list(dict.fromkeys(
            row["user_id"]
            for row in registrations
            if row.get("user_id")
        ))
        if not user_ids:
            return

        profile_result = (
            supabase_admin
            .table("profiles")
            .select("id,role,is_active")
            .in_("id", user_ids)
            .in_("role", ["admin", "staff"])
            .eq("is_active", True)
            .execute()
        )
        active_users = {
            profile["id"]
            for profile in (profile_result.data or [])
        }
        tokens = list(dict.fromkeys(
            row["token"]
            for row in (token_result.data or [])
            if row.get("token") and row.get("user_id") in active_users
        ))
        if not tokens:
            return

        order_no = str(order.get("order_no") or order_id)
        total_amount = str(order.get("total_amount") or "0")
        app = _firebase_app()
        invalid_tokens = []
        success_count = 0
        failure_count = 0
        for offset in range(0, len(tokens), 500):
            batch = tokens[offset:offset + 500]
            response = messaging.send_each_for_multicast(
                messaging.MulticastMessage(
                    data={
                        "type": "new_order",
                        "order_id": str(order_id),
                        "order_no": order_no,
                        "total_amount": total_amount,
                    },
                    tokens=batch,
                    android=messaging.AndroidConfig(
                        priority="high",
                        ttl=timedelta(hours=24),
                    ),
                ),
                app=app,
            )
            success_count += response.success_count
            failure_count += response.failure_count
            for token, result in zip(batch, response.responses):
                if result.success:
                    continue
                error_code = getattr(result.exception, "code", None)
                if error_code in {
                    "UNREGISTERED",
                    "registration-token-not-registered",
                    "INVALID_ARGUMENT",
                }:
                    invalid_tokens.append(token)

        if invalid_tokens:
            (
                supabase_admin
                .table("admin_push_tokens")
                .delete()
                .in_("token", invalid_tokens)
                .execute()
            )

        if failure_count:
            print(
                "ADMIN ORDER PUSH PARTIAL FAILURE:",
                f"sent={success_count}",
                f"failed={failure_count}",
                flush=True,
            )
    except Exception as exc:
        print(
            "ADMIN ORDER PUSH ERROR:",
            type(exc).__name__,
            flush=True,
        )
