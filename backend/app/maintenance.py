from datetime import datetime, time, timedelta, timezone
from time import monotonic

from fastapi import HTTPException

from .supabase import supabase_admin
from .supabase_retry import execute_read_with_retry


KATHMANDU = timezone(timedelta(hours=5, minutes=45), "Asia/Kathmandu")
_CACHE_SECONDS = 5
_schedules_cache = None
_schedules_cache_at = 0.0


def invalidate_maintenance_schedules_cache():
    global _schedules_cache, _schedules_cache_at
    _schedules_cache = None
    _schedules_cache_at = 0.0


def load_maintenance_schedules():
    global _schedules_cache, _schedules_cache_at
    now = monotonic()
    if (
        _schedules_cache is not None
        and now - _schedules_cache_at < _CACHE_SECONDS
    ):
        return [dict(schedule) for schedule in _schedules_cache]

    try:
        schedules = []
        page_size = 1000
        offset = 0
        while True:
            result = execute_read_with_retry(
                supabase_admin
                .table("store_maintenance_schedules")
                .select(
                    "id,mode,starts_at,daily_time,duration_minutes,message,"
                    "is_enabled,created_at,updated_at"
                )
                .order("id")
                .range(offset, offset + page_size - 1),
                operation="MAINTENANCE SCHEDULES",
            )
            page = result.data or []
            schedules.extend(dict(schedule) for schedule in page)
            if len(page) < page_size:
                break
            offset += page_size
    except Exception as exc:
        print("MAINTENANCE SCHEDULES LOAD ERROR:", repr(exc), flush=True)
        raise HTTPException(
            status_code=503,
            detail="Store availability is temporarily unknown.",
        ) from exc

    _schedules_cache = schedules
    _schedules_cache_at = now
    return [dict(schedule) for schedule in _schedules_cache]


def _parse_datetime(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("Invalid maintenance start time.")

    if parsed.tzinfo is None:
        raise ValueError("Maintenance start time must include a timezone.")

    return parsed.astimezone(timezone.utc)


def _parse_time(value):
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        return time.fromisoformat(value)
    raise ValueError("Invalid daily maintenance time.")


def maintenance_schedule_status(schedule, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Current time must include a timezone.")
    now = now.astimezone(timezone.utc)

    mode = schedule["mode"]
    duration = schedule.get("duration_minutes")
    duration = int(duration) if duration is not None else None
    starts_at = schedule.get("starts_at")
    daily_time = schedule.get("daily_time")
    current_start = None
    ends_at = None
    active = False

    if mode == "scheduled":
        if starts_at is None:
            raise ValueError("Maintenance start time is missing.")
        current_start = _parse_datetime(starts_at)
        if now >= current_start:
            ends_at = (
                current_start + timedelta(minutes=duration)
                if duration is not None
                else None
            )
            active = ends_at is None or now < ends_at
    elif mode == "daily":
        if daily_time is None or duration is None:
            raise ValueError("Daily maintenance schedule is incomplete.")
        daily_start = _parse_time(daily_time)
        local_now = now.astimezone(KATHMANDU)
        candidate_date = local_now.date()
        current_start = datetime.combine(
            candidate_date,
            daily_start,
            tzinfo=KATHMANDU,
        ).astimezone(timezone.utc)
        if current_start > now:
            candidate_date -= timedelta(days=1)
            current_start = datetime.combine(
                candidate_date,
                daily_start,
                tzinfo=KATHMANDU,
            ).astimezone(timezone.utc)
        ends_at = current_start + timedelta(minutes=duration)
        active = now < ends_at
    else:
        raise ValueError("Unknown maintenance schedule.")

    if not schedule.get("is_enabled", True):
        state = "disabled"
    elif active:
        state = "active"
    elif mode == "scheduled" and current_start > now:
        state = "scheduled"
    elif mode == "daily":
        state = "daily"
    else:
        state = "expired"

    return {
        "active": active and schedule.get("is_enabled", True),
        "state": state,
        "starts_at": current_start.isoformat() if current_start else None,
        "ends_at": ends_at.isoformat() if ends_at else None,
        "daily_time": str(daily_time) if daily_time else None,
        "duration_minutes": duration,
        "timezone": "Asia/Kathmandu",
    }


def maintenance_status(schedules, now=None):
    schedule_statuses = []
    for schedule in schedules:
        status = maintenance_schedule_status(schedule, now=now)
        schedule_statuses.append({
            **schedule,
            **status,
            "message": schedule.get("message") or "",
        })

    active_schedules = [
        schedule
        for schedule in schedule_statuses
        if schedule["active"]
    ]
    messages = [
        schedule["message"].strip()
        for schedule in active_schedules
        if schedule["message"].strip()
    ]

    return {
        "active": bool(active_schedules),
        "mode": "multiple" if active_schedules else "off",
        "message": "\n\n".join(messages),
        "starts_at": min(
            (schedule["starts_at"] for schedule in active_schedules),
            default=None,
        ),
        "ends_at": (
            None
            if any(schedule["ends_at"] is None for schedule in active_schedules)
            else max(
                (schedule["ends_at"] for schedule in active_schedules),
                default=None,
            )
        ),
        "schedules": schedule_statuses,
        "timezone": "Asia/Kathmandu",
    }


def get_maintenance_status(now=None):
    try:
        return maintenance_status(load_maintenance_schedules(), now=now)
    except HTTPException:
        raise
    except Exception as exc:
        print("MAINTENANCE SCHEDULE INVALID:", repr(exc), flush=True)
        raise HTTPException(
            status_code=503,
            detail="Store maintenance settings are invalid.",
        ) from exc
