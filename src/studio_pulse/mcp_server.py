"""MCP server exposing Studio Pulse data to Claude over stdio (entry point `studio-pulse-mcp`).

Tools are thin: they parse arguments, resolve names, call `analyze.py` and return flat JSON with
names instead of ids. Expected failures become `ToolError`, which the client receives as an
error message (never a raw exception). Every tool is read only.
"""

import functools
import logging
import unicodedata
from collections.abc import Callable
from datetime import date
from difflib import get_close_matches
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from studio_pulse import StudioPulseError, analyze
from studio_pulse.config import Settings, load_settings
from studio_pulse.dates import parse_date, parse_date_range, previous_working_day, today_in
from studio_pulse.models import BudgetStatus, GroupBy, GroupHours, MissingLog
from studio_pulse.sources import DataSource, get_source

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
Studio Pulse answers questions about time tracking and budgets of a creative agency \
(data from Productive). All numbers are computed by the server: quote them, do not recompute. \
Hours are rounded to 0.1. Dates accept YYYY-MM-DD, "today" or "yesterday"; "yesterday" is the \
calendar day before today, while missing_time_logs defaults to the previous working day \
(Friday on Mondays). Person, project and client names match case-insensitively by part of the \
name, with or without diacritics. Always state the date or date range used in the answer."""

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)

mcp = MCPServer("studio-pulse", instructions=INSTRUCTIONS)


# Output models (flat JSON for Claude)


class ProjectInfo(BaseModel):
    project: str
    client: str
    kind: str
    active: bool


class TimeEntryRow(BaseModel):
    date: date
    person: str
    project: str
    client: str
    hours: float
    billable_hours: float
    note: str | None
    invoiced: bool


class MissingLogsAnswer(BaseModel):
    date: date
    min_hours: float
    missing: list[MissingLog]


class HoursSummary(BaseModel):
    date_from: date
    date_to: date
    group_by: GroupBy
    total_hours: float
    billable_hours: float
    billable_percent: float
    groups: list[GroupHours]


class ClientUnbilled(BaseModel):
    client: str
    unbilled_hours: float


# Runtime context, replaced in tests


def _context() -> tuple[DataSource, Settings, date]:
    """Settings, data source and today's date, loaded on every call so changes apply at once."""
    settings = load_settings()
    return get_source(settings), settings, today_in(settings.timezone)


# Name matching


def _fold(text: str) -> str:
    """Lower case without diacritics, so "kovac" matches "Kováč"."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def _quoted(names: list[str]) -> str:
    return ", ".join(f'"{name}"' for name in names)


def match_name(query: str, options: list[str], kind: str) -> str:
    """The single option matching `query` (case- and diacritics-insensitive substring).

    An exact match wins over partial ones. Raises `ToolError` listing candidates when there is no
    match or more than one.
    """
    wanted = _fold(query.strip())
    unique = sorted(set(options))
    exact = [option for option in unique if _fold(option) == wanted]
    if exact:
        return exact[0]
    partial = [option for option in unique if wanted and wanted in _fold(option)]
    if len(partial) == 1:
        return partial[0]
    if partial:
        raise ToolError(f'{kind.capitalize()} "{query}" is ambiguous: {_quoted(partial)}.')

    folded = {_fold(option): option for option in unique}
    close = [folded[key] for key in get_close_matches(wanted, list(folded), n=5, cutoff=0.4)]
    hint = f"Did you mean: {_quoted(close)}?" if close else f"Known: {_quoted(unique)}."
    raise ToolError(f'No {kind} matches "{query}". {hint}')


# Tool registration


def _tool[F: Callable[..., Any]](fn: F) -> F:
    """Register `fn` as a read-only tool and turn `StudioPulseError` into a `ToolError` message."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except StudioPulseError as exc:
            raise ToolError(str(exc)) from None

    mcp.add_tool(wrapper, annotations=READ_ONLY)
    return wrapper  # type: ignore[return-value]


@_tool
def list_projects(active_only: bool = True) -> list[ProjectInfo]:
    """List the agency's projects with client and kind (3D, VR, WEB, CGI, INTERNAL).

    Use it to find exact project or client names. Example: "Which projects do we have for
    Hradná Invest?"
    """
    source, _, _ = _context()
    return [
        ProjectInfo(project=p.name, client=p.client, kind=p.kind, active=p.active)
        for p in source.projects()
        if p.active or not active_only
    ]


@_tool
def get_time_entries(
    date_from: str, date_to: str, person: str | None = None, project: str | None = None
) -> list[TimeEntryRow]:
    """Individual time entries in a date range (inclusive), optionally for one person or project.

    Example: "What did Peter work on yesterday?" or "Who logged time on the VR tour this week?"
    """
    source, _, today = _context()
    start, end = parse_date_range(date_from, date_to, today)
    people = {p.id: p.name for p in source.people()}
    projects = {p.id: p for p in source.projects()}
    person_name = match_name(person, list(people.values()), "person") if person else None
    project_name = (
        match_name(project, [p.name for p in projects.values()], "project") if project else None
    )

    rows = []
    for entry in source.time_entries(start, end):
        name = people.get(entry.person_id, entry.person_id)
        info = projects.get(entry.project_id)
        project_label = info.name if info else entry.project_id
        if person_name and name != person_name:
            continue
        if project_name and project_label != project_name:
            continue
        rows.append(
            TimeEntryRow(
                date=entry.date,
                person=name,
                project=project_label,
                client=info.client if info else "",
                hours=analyze.to_hours(entry.minutes),
                billable_hours=analyze.to_hours(entry.billable_minutes),
                note=entry.note,
                invoiced=entry.invoiced,
            )
        )
    return sorted(rows, key=lambda row: (row.date, row.person, row.project))


@_tool
def missing_time_logs(date: str | None = None) -> MissingLogsAnswer:
    """Active people who logged less than the daily minimum (config, 6 h by default) on a day.

    Defaults to the previous working day (Friday on Mondays). Example: "Who didn't log their
    time yesterday?"
    """
    source, settings, today = _context()
    day = parse_date(date, today) if date else previous_working_day(today)
    entries = source.time_entries(day, day)
    min_hours = settings.min_hours_per_day
    return MissingLogsAnswer(
        date=day,
        min_hours=min_hours,
        missing=analyze.missing_logs(entries, source.people(), day, min_hours),
    )


@_tool
def project_budget_status(project: str | None = None) -> list[BudgetStatus]:
    """Budget use per project over all time logged up to today: hours used, percent, remaining.

    Status: ok, warning (from the configured percent, 80 % by default), over (above 100 %),
    no_budget. Omit `project` for all active projects. Example: "How is the budget of the VR tour
    project doing?"
    """
    source, settings, today = _context()
    projects = source.projects()
    if project:
        name = match_name(project, [p.name for p in projects], "project")
        projects = [p for p in projects if p.name == name]
    else:
        projects = [p for p in projects if p.active]
    selected = {p.id for p in projects}
    entries = [
        entry
        for entry in source.time_entries(analyze.HISTORY_START, today)
        if entry.project_id in selected
    ]
    return analyze.budget_status(
        entries, projects, source.budgets(), settings.budget_warning_percent
    )


@_tool
def hours_summary(
    date_from: str, date_to: str, group_by: Literal["project", "person", "client"] = "project"
) -> HoursSummary:
    """Total and billable hours in a date range (inclusive), grouped by project, person or client.

    Example: "How many hours did we log per client last week?" or "What was each person's
    billable ratio this month?"
    """
    source, _, today = _context()
    start, end = parse_date_range(date_from, date_to, today)
    entries = source.time_entries(start, end)
    return HoursSummary(
        date_from=start,
        date_to=end,
        group_by=group_by,
        total_hours=analyze.to_hours(sum(entry.minutes for entry in entries)),
        billable_hours=analyze.to_hours(sum(entry.billable_minutes for entry in entries)),
        billable_percent=analyze.billable_percent(entries),
        groups=analyze.hours_by_group(entries, source.people(), source.projects(), group_by),
    )


@_tool
def unbilled_hours(client: str | None = None) -> list[ClientUnbilled]:
    """Billable hours not yet invoiced, per client, over all time logged up to today.

    Omit `client` for all clients. Example: "How many billable hours of Hradná Invest are not
    invoiced yet?"
    """
    source, _, today = _context()
    projects = source.projects()
    name = match_name(client, [p.client for p in projects], "client") if client else None
    entries = source.time_entries(analyze.HISTORY_START, today)
    hours = analyze.unbilled_hours(entries, projects, client=name)
    if name and name not in hours:
        hours = {name: 0.0}
    return [ClientUnbilled(client=key, unbilled_hours=value) for key, value in hours.items()]


def main() -> None:
    """Run the server over stdio. Logs go to stderr; stdout carries the MCP protocol."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    mcp.run("stdio")


if __name__ == "__main__":
    main()
