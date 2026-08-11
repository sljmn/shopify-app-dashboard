"""Presentation-only timezone handling for dashboard timestamps."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

DISPLAY_TIMEZONE = ZoneInfo("Europe/Amsterdam")
DISPLAY_TIMEZONE_LABEL = "CET/CEST"


def format_local_time(value: datetime | date, pattern: str) -> str:
    """Format an instant in Dutch local time while leaving plain dates alone."""
    if not isinstance(value, datetime):
        return value.strftime(pattern)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(DISPLAY_TIMEZONE).strftime(pattern)


def local_day_bounds(value: date) -> tuple[datetime, datetime]:
    """Return UTC bounds for one Europe/Amsterdam calendar day."""
    start = datetime.combine(value, time.min, tzinfo=DISPLAY_TIMEZONE)
    end = datetime.combine(value + timedelta(days=1), time.min,
                           tzinfo=DISPLAY_TIMEZONE)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def local_today() -> date:
    return datetime.now(DISPLAY_TIMEZONE).date()
