from datetime import date, datetime, timezone

from app_dashboard.display_time import format_local_time, local_day_bounds


def test_formats_winter_timestamp_as_cet():
    value = datetime(2026, 1, 11, 11, 19, tzinfo=timezone.utc)

    assert format_local_time(value, "%Y-%m-%d %H:%M %Z") == (
        "2026-01-11 12:19 CET"
    )


def test_formats_summer_timestamp_as_cest():
    value = datetime(2026, 8, 11, 11, 19, tzinfo=timezone.utc)

    assert format_local_time(value, "%Y-%m-%d %H:%M %Z") == (
        "2026-08-11 13:19 CEST"
    )


def test_plain_calendar_date_does_not_shift_timezone():
    assert format_local_time(date(2026, 8, 11), "%Y-%m-%d") == "2026-08-11"


def test_local_day_bounds_follow_daylight_saving_time():
    winter_start, winter_end = local_day_bounds(date(2026, 1, 11))
    summer_start, summer_end = local_day_bounds(date(2026, 8, 11))

    assert winter_start.isoformat() == "2026-01-10T23:00:00+00:00"
    assert winter_end.isoformat() == "2026-01-11T23:00:00+00:00"
    assert summer_start.isoformat() == "2026-08-10T22:00:00+00:00"
    assert summer_end.isoformat() == "2026-08-11T22:00:00+00:00"
