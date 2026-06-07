from __future__ import annotations

import os
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo


DEFAULT_APP_TIMEZONE = "Asia/Kolkata"


def get_app_timezone() -> ZoneInfo:
    tz_name = os.getenv("APP_TIMEZONE", DEFAULT_APP_TIMEZONE).strip() or DEFAULT_APP_TIMEZONE
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo(DEFAULT_APP_TIMEZONE)


def now_local() -> datetime:
    return datetime.now(get_app_timezone())


def today_local() -> date:
    return now_local().date()


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    tz = get_app_timezone()
    start_local = datetime.combine(day, time.min, tzinfo=tz)
    end_local = datetime.combine(day, time.max, tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def local_day_bounds_from_string(date_str: str) -> tuple[datetime, datetime]:
    return local_day_bounds(datetime.strptime(date_str, "%Y-%m-%d").date())
