"""Slovak report text shared by all delivery adapters. Formatting only: no numbers are computed.

Hours and percentages come rounded from `analyze.py`; here they are only written with a decimal
comma (percentages as whole numbers, as in "82 %").
"""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from studio_pulse.models import BudgetStatus, ReportData

WEEKDAYS = ("pondelok", "utorok", "streda", "štvrtok", "piatok", "sobota", "nedeľa")
ALL_OK = "Všetko v poriadku, žiadne upozornenia."
BUDGETS = "Rozpočty"
MISSING_LOGS = "Chýbajúce logy"
WITHOUT_NOTE = "Bez poznámky"
BULLET = "• "


def number(value: float) -> str:
    """One decimal place with a decimal comma; a whole number without it ("46,5", "0")."""
    text = f"{value:.1f}".replace(".", ",")
    return text.removesuffix(",0")


def hours(value: float) -> str:
    return f"{number(value)} h"


def percent(value: float) -> str:
    """Whole percent, rounded half up ("81.7" -> "82 %")."""
    rounded = Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{rounded} %"


def short_date(day: date) -> str:
    return f"{day.day}. {day.month}."


def title(report: ReportData) -> str:
    """ "Studio Pulse · utorok 10. 11." (the day the report is produced)."""
    day = report.generated_on
    return f"Studio Pulse · {WEEKDAYS[day.weekday()]} {short_date(day)}"


def day_label(report: ReportData) -> str:
    """ "Včera" for the calendar day before, otherwise the weekday and date ("Piatok 6. 11.")."""
    if report.day == report.generated_on - timedelta(days=1):
        return "Včera"
    return f"{WEEKDAYS[report.day.weekday()].capitalize()} {short_date(report.day)}"


def summary(report: ReportData) -> str:
    """ "Včera: 46,5 h, z toho 82 % fakturovateľných." plus a note when below the threshold."""
    text = (
        f"{day_label(report)}: {hours(report.total_hours)}, "
        f"z toho {percent(report.billable_percent)} fakturovateľných."
    )
    if report.billable_low:
        text += f" Pod cieľom {percent(report.low_billable_percent)}."
    return text


def budget_line(status: BudgetStatus) -> str:
    """ "Projekt: 112 % (prekročené o 15,6 h)" or "Projekt: 91 % (zostáva 16,2 h)"."""
    remaining = status.remaining_hours or 0.0
    detail = f"prekročené o {hours(-remaining)}" if remaining < 0 else f"zostáva {hours(remaining)}"
    if status.used_percent is None:  # zero budget: no percentage
        return f"{status.project}: {detail}"
    return f"{status.project}: {percent(status.used_percent)} ({detail})"


def budget_lines(report: ReportData) -> list[str]:
    return [budget_line(status) for status in report.budget_alerts]


def missing_log_lines(report: ReportData) -> list[str]:
    return [f"{log.person}: {hours(log.logged_hours)}" for log in report.missing_logs]


def records(count: int) -> str:
    """Slovak plural: 1 záznam, 2 až 4 záznamy, 5 a viac záznamov."""
    if count == 1:
        return "1 záznam"
    if 2 <= count <= 4:
        return f"{count} záznamy"
    return f"{count} záznamov"


def without_note(report: ReportData) -> str | None:
    """ "Peter Kováč (3 záznamy), ..." or None when every entry has a note."""
    if not report.entries_without_note:
        return None
    return ", ".join(
        f"{person} ({records(count)})" for person, count in report.entries_without_note.items()
    )


def has_alerts(report: ReportData) -> bool:
    return bool(
        report.budget_alerts
        or report.missing_logs
        or report.entries_without_note
        or report.billable_low
    )


def plain_text(report: ReportData) -> str:
    """The whole report as plain text (console output, also the spec's reference layout)."""
    blocks = [title(report), summary(report)]
    if not has_alerts(report):
        blocks.append(ALL_OK)
    if report.budget_alerts:
        blocks.append("\n".join([BUDGETS, *(BULLET + line for line in budget_lines(report))]))
    if report.missing_logs:
        lines = missing_log_lines(report)
        blocks.append("\n".join([MISSING_LOGS, *(BULLET + line for line in lines)]))
    if note := without_note(report):
        blocks.append(f"{WITHOUT_NOTE}: {note}")
    return "\n\n".join(blocks)
