"""Resolve date phrases against the simulator's today, Monday 2027-03-08.

The model quotes a phrase. This module is the only place that turns that phrase
into a calendar date. ``next <weekday>`` is the soonest such weekday strictly
after today. ``this <weekday>`` includes today. ``last <weekday>`` is the most
recent one strictly before today. A month and day without a year use 2027.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from .calendarutil import TODAY

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_WEEKDAY = r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_MONTH = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_RELATIVE = re.compile(rf"\b(next|this|last)\s+{_WEEKDAY}\b", re.IGNORECASE)
_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_MONTH_DAY = re.compile(rf"\b(?:{_WEEKDAY}\s+)?{_MONTH}\s+(\d{{1,2}})\b", re.IGNORECASE)
_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})\s+{_MONTH}\b", re.IGNORECASE)


class DateError(ValueError):
    """The phrase is not one unambiguous date."""


def parse_date_mention(mention: str, today: date = TODAY) -> date:
    text = " ".join(mention.strip().split())
    if not text:
        raise DateError("date mention is empty")
    found: list[date] = []
    found.extend(_relative_dates(text, today))
    found.extend(_explicit_dates(text))
    if not found:
        raise DateError(f"could not resolve date phrase {mention!r}")
    if any(item != found[0] for item in found[1:]):
        raise DateError(f"date phrase {mention!r} contains conflicting dates")
    return found[0]


def _relative_dates(text: str, today: date) -> list[date]:
    found = []
    for match in _RELATIVE.finditer(text):
        direction, name = match.group(1).lower(), match.group(2).lower()
        target = WEEKDAYS[name]
        if direction == "this":
            ahead = (target - today.weekday()) % 7
            found.append(today + timedelta(days=ahead))
        elif direction == "next":
            ahead = (target - today.weekday()) % 7
            if ahead == 0:
                ahead = 7
            found.append(today + timedelta(days=ahead))
        else:
            back = (today.weekday() - target) % 7
            if back == 0:
                back = 7
            found.append(today - timedelta(days=back))
    return found


def _explicit_dates(text: str) -> list[date]:
    found: list[date] = []
    occupied: list[tuple[int, int]] = []
    for match in _ISO.finditer(text):
        try:
            found.append(date.fromisoformat(match.group(1)))
        except ValueError as exc:
            raise DateError(f"invalid date {match.group(1)}") from exc
        occupied.append(match.span())
    for match in _MONTH_DAY.finditer(text):
        if _overlaps(match.span(), occupied):
            continue
        weekday_name, month_name, day_text = match.group(1), match.group(2), match.group(3)
        found.append(_month_day(month_name, day_text, weekday_name))
        occupied.append(match.span())
    for match in _DAY_MONTH.finditer(text):
        if _overlaps(match.span(), occupied):
            continue
        found.append(_month_day(match.group(2), match.group(1), None))
    return found


def _month_day(month_name: str, day_text: str, weekday_name: str | None) -> date:
    month = MONTHS[month_name.lower()]
    day = int(day_text)
    try:
        resolved = date(TODAY.year, month, day)
    except ValueError as exc:
        raise DateError(f"invalid calendar date {month_name} {day_text}") from exc
    if weekday_name:
        expected = WEEKDAYS[weekday_name.lower()]
        if resolved.weekday() != expected:
            raise DateError(f"{weekday_name} does not match {resolved.isoformat()}")
    return resolved


def _overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < other_end and other_start < end for other_start, other_end in occupied)
