"""Validated report windows with Amsterdam calendar semantics."""

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone

from app_dashboard.display_time import DISPLAY_TIMEZONE, local_day_bounds


PRESETS = (
    "today", "7d", "30d", "90d", "this_month", "last_month", "custom",
)
PRESET_LABELS = {
    "today": "Today",
    "7d": "7 days",
    "30d": "30 days",
    "90d": "90 days",
    "this_month": "This month",
    "last_month": "Last month",
    "custom": "Custom",
}
MAX_CUSTOM_DAYS = 731


@dataclass(frozen=True)
class PeriodSelection:
    preset: str
    start: datetime
    end: datetime
    previous_start: datetime
    previous_end: datetime
    display_start: date
    display_end: date
    input_start: str
    input_end: str
    error: str | None = None
    now: datetime | None = None

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    @property
    def is_future(self) -> bool:
        return bool(self.now and self.start >= self.now)

    def query_items(self) -> tuple[tuple[str, str], ...]:
        if self.preset != "custom":
            return (("period", self.preset),)
        return (
            ("period", "custom"),
            ("start", self.input_start),
            ("end", self.input_end),
        )


def _selection(
    preset: str,
    start: datetime,
    end: datetime,
    now: datetime,
    input_start: str = "",
    input_end: str = "",
) -> PeriodSelection:
    duration = end - start
    display_start = start.astimezone(DISPLAY_TIMEZONE).date()
    display_end = (end - timedelta(microseconds=1)).astimezone(
        DISPLAY_TIMEZONE
    ).date()
    return PeriodSelection(
        preset=preset,
        start=start,
        end=end,
        previous_start=start - duration,
        previous_end=start,
        display_start=display_start,
        display_end=display_end,
        input_start=input_start or display_start.isoformat(),
        input_end=input_end or display_end.isoformat(),
        now=now,
    )


def _invalid_custom(
    now: datetime, start_text: str, end_text: str, message: str
) -> PeriodSelection:
    return replace(
        resolve_period("30d", None, None, now=now),
        input_start=start_text,
        input_end=end_text,
        error=message,
    )


def resolve_period(
    preset: str | None,
    start_text: str | None,
    end_text: str | None,
    *,
    now: datetime | None = None,
) -> PeriodSelection:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local_now = now.astimezone(DISPLAY_TIMEZONE)
    key = preset if preset in PRESETS else "30d"

    if key == "today":
        start_utc, _ = local_day_bounds(local_now.date())
        return _selection(key, start_utc, now, now)

    if key in {"7d", "30d", "90d"}:
        days = int(key[:-1])
        return _selection(key, now - timedelta(days=days), now, now)

    if key == "this_month":
        local_start = datetime.combine(
            local_now.date().replace(day=1), time.min, tzinfo=DISPLAY_TIMEZONE
        )
        return _selection(key, local_start.astimezone(timezone.utc), now, now)

    if key == "last_month":
        this_month = local_now.date().replace(day=1)
        last_month = (this_month - timedelta(days=1)).replace(day=1)
        start_utc, _ = local_day_bounds(last_month)
        end_utc, _ = local_day_bounds(this_month)
        return _selection(key, start_utc, end_utc, now)

    raw_start, raw_end = start_text or "", end_text or ""
    try:
        start_date = date.fromisoformat(raw_start)
        end_date = date.fromisoformat(raw_end)
    except ValueError:
        return _invalid_custom(
            now, raw_start, raw_end, "Choose a valid start and end date."
        )

    if end_date < start_date:
        return _invalid_custom(
            now,
            raw_start,
            raw_end,
            "The end date must be on or after the start date.",
        )
    if (end_date - start_date).days + 1 > MAX_CUSTOM_DAYS:
        return _invalid_custom(
            now,
            raw_start,
            raw_end,
            "A custom period may span at most two years.",
        )

    start_utc, _ = local_day_bounds(start_date)
    _, end_utc = local_day_bounds(end_date)
    return _selection("custom", start_utc, end_utc, now, raw_start, raw_end)
