from datetime import date

import pytest

from studio_pulse import StudioPulseError
from studio_pulse.dates import parse_date, parse_date_range, previous_working_day, today_in

# Week of 2026-11-09: Monday 9 ... Sunday 15
MONDAY = date(2026, 11, 9)
FRIDAY_BEFORE = date(2026, 11, 6)


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 11, 9), FRIDAY_BEFORE),  # Monday -> previous Friday
        (date(2026, 11, 10), date(2026, 11, 9)),  # Tuesday -> Monday
        (date(2026, 11, 11), date(2026, 11, 10)),  # Wednesday
        (date(2026, 11, 12), date(2026, 11, 11)),  # Thursday
        (date(2026, 11, 13), date(2026, 11, 12)),  # Friday -> Thursday
        (date(2026, 11, 14), date(2026, 11, 13)),  # Saturday -> Friday
        (date(2026, 11, 15), date(2026, 11, 13)),  # Sunday -> Friday
    ],
)
def test_previous_working_day(today: date, expected: date) -> None:
    assert previous_working_day(today) == expected


def test_previous_working_day_across_month_and_year() -> None:
    assert previous_working_day(date(2026, 6, 1)) == date(2026, 5, 29)  # Monday
    assert previous_working_day(date(2027, 1, 1)) == date(2026, 12, 31)  # Friday


def test_parse_date_keywords() -> None:
    assert parse_date("today", MONDAY) == MONDAY
    # calendar day before, not the previous working day
    assert parse_date(" Yesterday ", MONDAY) == date(2026, 11, 8)


def test_parse_date_iso() -> None:
    assert parse_date("2026-11-03", MONDAY) == date(2026, 11, 3)


@pytest.mark.parametrize("value", ["", "tomorrow", "3.11.2026", "2026-13-01", "2026-02-30"])
def test_parse_date_invalid(value: str) -> None:
    with pytest.raises(StudioPulseError, match="YYYY-MM-DD"):
        parse_date(value, MONDAY)


def test_parse_date_range() -> None:
    assert parse_date_range("2026-11-02", "yesterday", MONDAY) == (
        date(2026, 11, 2),
        date(2026, 11, 8),
    )
    assert parse_date_range("today", "today", MONDAY) == (MONDAY, MONDAY)


def test_parse_date_range_reversed() -> None:
    with pytest.raises(StudioPulseError, match="after"):
        parse_date_range("today", "2026-11-01", MONDAY)


def test_today_in_returns_date() -> None:
    assert isinstance(today_in("Europe/Bratislava"), date)
