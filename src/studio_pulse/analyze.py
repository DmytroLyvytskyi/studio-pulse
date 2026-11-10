"""Analysis rules: every number in reports and MCP answers comes from here.

Functions do no I/O apart from logging a warning when an entry references an unknown person or
project id. Inputs are in minutes; results are converted to hours and percentages rounded to 0.1
(half up) only when building the output models.
"""

import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from studio_pulse.config import Settings
from studio_pulse.models import (
    Budget,
    BudgetState,
    BudgetStatus,
    GroupBy,
    GroupHours,
    MissingLog,
    Person,
    Project,
    ProjectHours,
    ReportData,
    TimeEntry,
)
from studio_pulse.sources.base import DataSource

logger = logging.getLogger(__name__)

# "All time": budgets and unbilled hours count every entry logged from this day on.
# TODO: Productive may expose used budget time directly; revisit when the source is built.
HISTORY_START = date(2000, 1, 1)

STATUS_ORDER: dict[BudgetState, int] = {"over": 0, "warning": 1, "ok": 2, "no_budget": 3}


def _round1(numerator: int | float, denominator: int | float = 1) -> float:
    """`numerator / denominator` rounded half up to one decimal place."""
    value = Decimal(str(numerator)) / Decimal(str(denominator))
    return float(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def to_hours(minutes: int) -> float:
    """Minutes to hours rounded to 0.1 (half up)."""
    return _round1(minutes, 60)


def _percent(part: int, whole: int) -> float:
    return _round1(100 * part, whole)


def _warn_unknown(kind: str, ids: set[str]) -> None:
    """Log once per call which ids are missing from the person or project list."""
    if ids:
        logger.warning("Time entries reference unknown %s ids: %s", kind, ", ".join(sorted(ids)))


def missing_logs(
    entries: list[TimeEntry], people: list[Person], day: date, min_hours: float
) -> list[MissingLog]:
    """Active people who logged less than `min_hours` on `day`, fewest hours first."""
    logged: dict[str, int] = defaultdict(int)
    for entry in entries:
        if entry.date == day:
            logged[entry.person_id] += entry.minutes

    _warn_unknown("person", logged.keys() - {person.id for person in people})

    min_minutes = min_hours * 60
    missing = [
        (logged[person.id], person.name)
        for person in people
        if person.active and logged[person.id] < min_minutes
    ]
    return [
        MissingLog(person=name, logged_hours=to_hours(minutes), expected_hours=min_hours)
        for minutes, name in sorted(missing)
    ]


def _budget_state(used: int, budget: int | None, warning_percent: float) -> BudgetState:
    if budget is None:
        return "no_budget"
    if used > budget:
        return "over"
    if budget > 0 and used * 100 >= warning_percent * budget:
        return "warning"
    return "ok"


def budget_status(
    entries: list[TimeEntry],
    projects: list[Project],
    budgets: list[Budget],
    warning_percent: float,
) -> list[BudgetStatus]:
    """Used time against budget per project, over all given entries.

    `ok` below `warning_percent`, `warning` from `warning_percent` up to 100 %, `over` above
    100 %, `no_budget` when the project has no budget. Sorted by severity, then percent used.
    """
    used: dict[str, int] = defaultdict(int)
    for entry in entries:
        used[entry.project_id] += entry.minutes
    limits = {budget.project_id: budget.budget_minutes for budget in budgets}
    _warn_unknown("project", used.keys() - {project.id for project in projects})

    statuses = []
    for project in projects:
        minutes = used[project.id]
        limit = limits.get(project.id)
        statuses.append(
            BudgetStatus(
                project=project.name,
                client=project.client,
                budget_hours=None if limit is None else to_hours(limit),
                used_hours=to_hours(minutes),
                used_percent=_percent(minutes, limit) if limit else None,
                remaining_hours=None if limit is None else to_hours(limit - minutes),
                status=_budget_state(minutes, limit, warning_percent),
            )
        )
    return sorted(statuses, key=lambda s: (STATUS_ORDER[s.status], -(s.used_percent or 0)))


def _sum_by(
    entries: list[TimeEntry], key: Callable[[TimeEntry], str]
) -> dict[str, tuple[int, int]]:
    """(total minutes, billable minutes) per key."""
    total: dict[str, int] = defaultdict(int)
    billable: dict[str, int] = defaultdict(int)
    for entry in entries:
        total[key(entry)] += entry.minutes
        billable[key(entry)] += entry.billable_minutes
    return {name: (total[name], billable[name]) for name in total}


def hours_by_project(entries: list[TimeEntry], projects: list[Project]) -> list[ProjectHours]:
    """Total and billable hours per project with any time logged, largest total first."""
    by_id = {project.id: project for project in projects}
    sums = _sum_by(entries, lambda entry: entry.project_id)
    _warn_unknown("project", sums.keys() - by_id.keys())
    rows = []
    for project_id, (minutes, billable) in sums.items():
        project = by_id.get(project_id)
        rows.append(
            ProjectHours(
                project=project.name if project else project_id,
                client=project.client if project else "",
                total_hours=to_hours(minutes),
                billable_hours=to_hours(billable),
            )
        )
    return sorted(rows, key=lambda row: (-row.total_hours, row.project))


def hours_by_group(
    entries: list[TimeEntry], people: list[Person], projects: list[Project], group_by: GroupBy
) -> list[GroupHours]:
    """Total and billable hours per project, person or client name, largest total first.

    Unknown ids are kept under the id itself.
    """
    if group_by == "person":
        names = {person.id: person.name for person in people}
        _warn_unknown("person", {entry.person_id for entry in entries} - names.keys())
        sums = _sum_by(entries, lambda entry: names.get(entry.person_id, entry.person_id))
    else:
        field = "name" if group_by == "project" else "client"
        names = {project.id: getattr(project, field) for project in projects}
        _warn_unknown("project", {entry.project_id for entry in entries} - names.keys())
        sums = _sum_by(entries, lambda entry: names.get(entry.project_id, entry.project_id))

    rows = [
        GroupHours(
            name=name,
            total_hours=to_hours(minutes),
            billable_hours=to_hours(billable),
            billable_percent=_percent(billable, minutes) if minutes else 0.0,
        )
        for name, (minutes, billable) in sums.items()
    ]
    return sorted(rows, key=lambda row: (-row.total_hours, row.name))


def billable_percent(entries: list[TimeEntry]) -> float:
    """Billable minutes as a percentage of all minutes; 0 when nothing is logged."""
    total = sum(entry.minutes for entry in entries)
    if total == 0:
        return 0.0
    return _percent(sum(entry.billable_minutes for entry in entries), total)


def entries_without_note(entries: list[TimeEntry], people: list[Person]) -> dict[str, int]:
    """Number of entries with an empty note per person name, most first."""
    names = {person.id: person.name for person in people}
    counts: dict[str, int] = defaultdict(int)
    for entry in entries:
        if not (entry.note or "").strip():
            counts[names.get(entry.person_id, entry.person_id)] += 1
    _warn_unknown("person", {entry.person_id for entry in entries} - names.keys())
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def unbilled_hours(
    entries: list[TimeEntry], projects: list[Project], client: str | None = None
) -> dict[str, float]:
    """Billable hours not yet invoiced per client, most first.

    `client` keeps only that client (exact name; fuzzy matching is up to the caller).
    """
    clients = {project.id: project.client for project in projects}
    _warn_unknown("project", {entry.project_id for entry in entries} - clients.keys())
    minutes: dict[str, int] = defaultdict(int)
    for entry in entries:
        if entry.invoiced or entry.billable_minutes == 0:
            continue
        name = clients.get(entry.project_id, entry.project_id)
        if client is None or name == client:
            minutes[name] += entry.billable_minutes
    return {
        name: to_hours(value)
        for name, value in sorted(minutes.items(), key=lambda item: (-item[1], item[0]))
    }


def build_report(source: DataSource, day: date, settings: Settings) -> ReportData:
    """Everything the daily report shows for `day`. Budgets count all time logged up to `day`.

    TODO: `settings.low_billable_percent` is not applied here; the report rendering
    (milestones 6 and 7) must highlight `billable_percent` below it.
    """
    people = source.people()
    projects = source.projects()
    day_entries = source.time_entries(day, day)
    history = source.time_entries(HISTORY_START, day)

    statuses = budget_status(history, projects, source.budgets(), settings.budget_warning_percent)
    return ReportData(
        day=day,
        total_hours=to_hours(sum(entry.minutes for entry in day_entries)),
        billable_percent=billable_percent(day_entries),
        hours_by_project=hours_by_project(day_entries, projects),
        budget_alerts=[s for s in statuses if s.status in ("warning", "over")],
        missing_logs=missing_logs(day_entries, people, day, settings.min_hours_per_day),
        entries_without_note=entries_without_note(day_entries, people),
    )
