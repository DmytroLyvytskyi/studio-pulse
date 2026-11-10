"""Date helpers. Pure functions; the current day is passed in or derived from a time zone."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from studio_pulse import StudioPulseError

FRIDAY = 4
SATURDAY = 5


def today_in(timezone: str) -> date:
    """Current calendar day in the given IANA time zone (e.g. "Europe/Bratislava")."""
    return datetime.now(ZoneInfo(timezone)).date()


def previous_working_day(today: date) -> date:
    """Monday, Saturday and Sunday -> previous Friday; Tuesday to Friday -> previous day.

    Public holidays are ignored (known limitation).
    """
    weekday = today.weekday()  # Monday = 0
    if weekday == 0:
        return today - timedelta(days=3)
    if weekday >= SATURDAY:
        return today - timedelta(days=weekday - FRIDAY)
    return today - timedelta(days=1)


def parse_date(value: str, today: date) -> date:
    """Parse `YYYY-MM-DD`, `today` or `yesterday` (the calendar day before `today`)."""
    text = value.strip().lower()
    if text == "today":
        return today
    if text == "yesterday":
        return today - timedelta(days=1)
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise StudioPulseError(
            f"Invalid date {value!r}. Use YYYY-MM-DD, 'today' or 'yesterday'."
        ) from None


def parse_date_range(date_from: str, date_to: str, today: date) -> tuple[date, date]:
    """Parse both ends of an inclusive range and check that it is not reversed."""
    start = parse_date(date_from, today)
    end = parse_date(date_to, today)
    if start > end:
        raise StudioPulseError(
            f"Invalid date range: {start.isoformat()} is after {end.isoformat()}."
        )
    return start, end
