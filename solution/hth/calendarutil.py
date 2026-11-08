"""Working-day calendar used by the enterprise tools.

The formulas match ``enterprise.py`` (``START``, ``FIRST_IDX``, ``LAST_IDX``,
``idx_to_date``, ``date_to_idx``). This module does not import the simulator.
"""

from __future__ import annotations

from datetime import date, timedelta

# Monday 2027-03-08. Working-day index 0. Tool dates run through index 19.
TODAY = date(2027, 3, 8)
FIRST_IDX = -10
LAST_IDX = 19


def idx_to_date(i: int) -> date:
    return TODAY + timedelta(days=7 * (i // 5) + i % 5)


def date_to_idx(d: date) -> int:
    days = (d - TODAY).days
    if days % 7 > 4:
        raise ValueError("weekend")
    return 5 * (days // 7) + days % 7


def is_working_day(d: date) -> bool:
    try:
        date_to_idx(d)
    except ValueError:
        return False
    return True


ABSOLUTE_HORIZON = idx_to_date(LAST_IDX)
TOOL_DAYS = tuple(idx_to_date(i) for i in range(0, LAST_IDX + 1))


def next_working_day(d: date) -> date:
    """First Monday–Friday strictly after ``d``."""
    n = d + timedelta(days=1)
    while not is_working_day(n):
        n += timedelta(days=1)
    return n


def destination_days_through(horizon_end: date) -> tuple[date, ...]:
    """Tool days from today through ``horizon_end``, never past the tool window."""
    if horizon_end < TODAY:
        raise ValueError("horizon_end is before today")
    if not is_working_day(horizon_end):
        raise ValueError("horizon_end must be a working day")
    end = min(horizon_end, ABSOLUTE_HORIZON)
    return tuple(d for d in TOOL_DAYS if d <= end)
