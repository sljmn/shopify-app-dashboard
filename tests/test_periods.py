from datetime import datetime, timezone

from app_dashboard.periods import resolve_period


NOW = datetime(2026, 8, 11, 14, 30, tzinfo=timezone.utc)


def test_default_is_the_last_30_days_ending_now():
    selected = resolve_period(None, None, None, now=NOW)

    assert selected.preset == "30d"
    assert selected.start == datetime(2026, 7, 12, 14, 30, tzinfo=timezone.utc)
    assert selected.end == NOW
    assert selected.previous_end == selected.start
    assert selected.previous_start == datetime(
        2026, 6, 12, 14, 30, tzinfo=timezone.utc
    )
    assert selected.error is None


def test_rolling_presets_have_the_requested_duration():
    assert resolve_period("7d", None, None, now=NOW).duration.days == 7
    assert resolve_period("90d", None, None, now=NOW).duration.days == 90


def test_today_uses_the_amsterdam_calendar_day_until_now():
    selected = resolve_period("today", None, None, now=NOW)

    assert selected.preset == "today"
    assert selected.start == datetime(2026, 8, 10, 22, 0, tzinfo=timezone.utc)
    assert selected.end == NOW
    assert selected.display_start.isoformat() == "2026-08-11"
    assert selected.display_end.isoformat() == "2026-08-11"
    assert selected.query_items() == (("period", "today"),)


def test_custom_dates_are_inclusive_amsterdam_calendar_days():
    selected = resolve_period("custom", "2026-03-29", "2026-03-29", now=NOW)

    assert selected.start == datetime(2026, 3, 28, 23, 0, tzinfo=timezone.utc)
    assert selected.end == datetime(2026, 3, 29, 22, 0, tzinfo=timezone.utc)
    assert selected.duration.total_seconds() == 23 * 60 * 60
    assert selected.display_start.isoformat() == "2026-03-29"
    assert selected.display_end.isoformat() == "2026-03-29"


def test_calendar_month_presets_use_local_boundaries():
    this_month = resolve_period("this_month", None, None, now=NOW)
    last_month = resolve_period("last_month", None, None, now=NOW)

    assert this_month.start == datetime(2026, 7, 31, 22, 0, tzinfo=timezone.utc)
    assert this_month.end == NOW
    assert last_month.start == datetime(2026, 6, 30, 22, 0, tzinfo=timezone.utc)
    assert last_month.end == datetime(2026, 7, 31, 22, 0, tzinfo=timezone.utc)


def test_invalid_custom_dates_fall_back_without_raising():
    selected = resolve_period("custom", "bad", "2026-08-01", now=NOW)

    assert selected.preset == "30d"
    assert selected.input_start == "bad"
    assert selected.input_end == "2026-08-01"
    assert selected.error == "Choose a valid start and end date."


def test_reversed_and_overlong_custom_ranges_are_rejected():
    reversed_range = resolve_period(
        "custom", "2026-08-10", "2026-08-01", now=NOW
    )
    overlong = resolve_period("custom", "2024-01-01", "2026-08-01", now=NOW)

    assert reversed_range.error == (
        "The end date must be on or after the start date."
    )
    assert overlong.error == "A custom period may span at most two years."


def test_a_wholly_future_custom_period_is_marked():
    selected = resolve_period("custom", "2026-08-14", "2026-08-15", now=NOW)

    assert selected.is_future is True


def test_unknown_preset_falls_back_to_30_days():
    selected = resolve_period("forever", None, None, now=NOW)

    assert selected.preset == "30d"
    assert selected.error is None
