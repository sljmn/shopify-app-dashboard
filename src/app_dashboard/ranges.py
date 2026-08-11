"""Every window a reader can pick, and the one function that validates them.

Its own module so every HTML report validates time windows the same way. One
allowlist prevents two pages from silently interpreting the same query value
differently.

A range control is a number from the query string reaching a query. It is
validated against a fixed set and falls back to the default rather than being
clamped -- clamping an absurd value silently answers a question nobody asked,
while falling back leaves the page free to say which window it is showing.
"""

MONEY_MONTHS = (6, 12, 24)
TRAFFIC_DAYS = (30, 90, 180, 365)
CHURN_DAYS = (30, 90, 365)
TRIAL_DAYS = (7, 14, 30, 60)


def choice(value, allowed: tuple[int, ...], default: int | None) -> int | None:
    """One query parameter, or the default. Never anything else."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number in allowed else default
