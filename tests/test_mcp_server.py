import asyncio
from datetime import date
from typing import Any

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from studio_pulse import StudioPulseError, mcp_server
from studio_pulse.config import Settings
from studio_pulse.mcp_server import (
    get_time_entries,
    hours_summary,
    list_projects,
    match_name,
    mcp,
    missing_time_logs,
    project_budget_status,
    unbilled_hours,
)
from studio_pulse.sources import MockSource

TUESDAY = date(2026, 11, 10)
MONDAY = date(2026, 11, 9)
TOOLS = {
    "list_projects",
    "get_time_entries",
    "missing_time_logs",
    "project_budget_status",
    "hours_summary",
    "unbilled_hours",
}


@pytest.fixture(autouse=True)
def mock_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock source with a fixed today (Tuesday) and the default thresholds."""
    settings = Settings(min_hours_per_day=6, budget_warning_percent=80)
    monkeypatch.setattr(
        mcp_server, "_context", lambda: (MockSource(today=TUESDAY), settings, TUESDAY)
    )


def call(name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Call a tool through an in-process MCP client, as Claude would."""

    async def run() -> CallToolResult:
        async with Client(mcp) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(run())


# Name matching


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Peter Kováč", "Peter Kováč"),
        ("peter", "Peter Kováč"),
        ("KOVAC", "Peter Kováč"),  # case and diacritics insensitive
        ("  kráľ ", "Zuzana Kráľová"),
    ],
)
def test_match_name(query: str, expected: str) -> None:
    names = ["Peter Kováč", "Zuzana Kráľová", "Martin Horák"]
    assert match_name(query, names, "person") == expected


def test_match_name_exact_wins_over_partial() -> None:
    assert match_name("Web", ["Web", "Web redesign"], "project") == "Web"


def test_match_name_ambiguous_lists_candidates() -> None:
    with pytest.raises(ToolError, match=r'Person "ma" is ambiguous: "Martin Horák", "Tomáš Baláž"'):
        match_name("ma", ["Martin Horák", "Tomáš Baláž", "Peter Kováč"], "person")


def test_match_name_no_match_suggests_close_options() -> None:
    with pytest.raises(ToolError, match=r'No person matches "Petr Kovacs"\. Did you mean: "Peter'):
        match_name("Petr Kovacs", ["Peter Kováč", "Zuzana Kráľová"], "person")


def test_match_name_no_match_lists_all_when_nothing_is_close() -> None:
    with pytest.raises(ToolError, match=r'Known: "Alfa", "Beta"\.'):
        match_name("xyz", ["Beta", "Alfa"], "client")


def test_match_name_empty_query_is_not_a_match() -> None:
    with pytest.raises(ToolError, match="No project matches"):
        match_name("  ", ["Alfa"], "project")


# list_projects


def test_list_projects() -> None:
    projects = list_projects()
    assert len(projects) == 5
    lesna = next(p for p in projects if p.project == "Lesná Brána, VR prehliadka")
    assert (lesna.client, lesna.kind, lesna.active) == ("Lesná Brána Development", "VR", True)


# get_time_entries


def test_get_time_entries_for_person() -> None:
    rows = get_time_entries("yesterday", "yesterday", person="kovac")
    assert [(r.date, r.person, r.project, r.hours, r.note) for r in rows] == [
        (MONDAY, "Peter Kováč", "Lesná Brána, VR prehliadka", 2.5, None),
        (MONDAY, "Peter Kováč", "Rezidencia Hradná, vizualizácie", 3.0, None),
        (MONDAY, "Peter Kováč", "Rezidencia Hradná, vizualizácie", 2.5, None),
    ]


def test_get_time_entries_for_project() -> None:
    rows = get_time_entries("2026-11-09", "2026-11-09", project="hr kampan")
    assert [(r.person, r.client, r.hours, r.billable_hours) for r in rows] == [
        ("Tomáš Baláž", "Nordica HR", 4.0, 4.0),
        ("Zuzana Kráľová", "Nordica HR", 2.0, 2.0),
    ]


def test_get_time_entries_whole_day() -> None:
    rows = get_time_entries("yesterday", "yesterday")
    assert sum(r.hours for r in rows) == pytest.approx(46.5)
    assert {r.date for r in rows} == {MONDAY}


def test_get_time_entries_today_is_empty() -> None:
    assert get_time_entries("today", "today") == []


# missing_time_logs


def test_missing_time_logs_defaults_to_previous_working_day() -> None:
    answer = missing_time_logs()
    assert answer.date == MONDAY
    assert answer.min_hours == 6
    assert [(m.person, m.logged_hours) for m in answer.missing] == [
        ("Jana Nováková", 0.0),
        ("Martin Horák", 3.5),
    ]


def test_missing_time_logs_explicit_date() -> None:
    assert missing_time_logs("2026-11-09") == missing_time_logs()
    assert len(missing_time_logs("today").missing) == 8  # nothing logged today yet


def test_missing_time_logs_on_monday_checks_friday(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(min_hours_per_day=6)
    monkeypatch.setattr(
        mcp_server, "_context", lambda: (MockSource(today=MONDAY), settings, MONDAY)
    )
    answer = missing_time_logs()
    assert answer.date == date(2026, 11, 6)
    assert [m.person for m in answer.missing] == ["Jana Nováková", "Martin Horák"]


# project_budget_status


def test_project_budget_status_all_active_projects() -> None:
    result = project_budget_status()
    assert [(s.project, s.status, s.used_percent) for s in result] == [
        ("Rezidencia Hradná, vizualizácie", "over", 112.0),
        ("Lesná Brána, VR prehliadka", "warning", 91.0),
        ("Riverside Lofts, web", "ok", 54.0),
        ("HR kampaň, video", "ok", 30.0),
        ("Interné R&D", "no_budget", None),
    ]


def test_project_budget_status_one_project(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        [vr] = project_budget_status("vr")
    assert caplog.records == []  # other projects' entries are not "unknown ids"
    assert (vr.project, vr.status, vr.used_percent, vr.remaining_hours) == (
        "Lesná Brána, VR prehliadka",
        "warning",
        91.0,
        10.8,
    )
    [hradna] = project_budget_status("HRADNA")
    assert (hradna.status, hradna.used_percent, hradna.remaining_hours) == ("over", 112.0, -9.6)


# hours_summary


def test_hours_summary_by_person() -> None:
    summary = hours_summary("yesterday", "yesterday", "person")
    assert (summary.date_from, summary.date_to, summary.group_by) == (MONDAY, MONDAY, "person")
    assert (summary.total_hours, summary.billable_hours, summary.billable_percent) == (
        46.5,
        38.0,
        81.7,
    )
    names = [g.name for g in summary.groups]
    assert names[:2] == ["Peter Kováč", "Tomáš Baláž"]  # 8 h each
    assert "Jana Nováková" not in names


def test_hours_summary_by_client() -> None:
    summary = hours_summary("2026-11-09", "2026-11-09", "client")
    assert [(g.name, g.total_hours) for g in summary.groups] == [
        ("Hradná Invest", 12.0),
        ("Lesná Brána Development", 11.5),
        ("Riverside Lofts", 8.5),
        ("Studio", 8.5),
        ("Nordica HR", 6.0),
    ]


def test_tools_log_no_warnings_on_mock_data(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        list_projects()
        get_time_entries("2026-11-01", "today", person="peter", project="lesna")
        missing_time_logs()
        project_budget_status()
        project_budget_status("riverside")
        for group_by in ("project", "person", "client"):
            hours_summary("2026-11-01", "today", group_by)
        unbilled_hours()
        unbilled_hours("hradna")
    assert caplog.records == []


def test_hours_summary_defaults_to_project() -> None:
    summary = hours_summary("yesterday", "yesterday")
    assert summary.groups[0].name == "Rezidencia Hradná, vizualizácie"


# unbilled_hours


def test_unbilled_hours_all_clients() -> None:
    clients = {row.client for row in unbilled_hours()}
    assert clients == {"Lesná Brána Development", "Hradná Invest", "Riverside Lofts", "Nordica HR"}


def test_unbilled_hours_one_client() -> None:
    [row] = unbilled_hours("nordica")
    assert row.client == "Nordica HR"
    assert row.unbilled_hours > 0


def test_unbilled_hours_client_without_billable_work_reports_zero() -> None:
    assert [(r.client, r.unbilled_hours) for r in unbilled_hours("studio")] == [("Studio", 0.0)]


# Through the MCP protocol


def test_server_lists_all_tools_as_read_only_with_examples() -> None:
    async def run() -> list[Any]:
        async with Client(mcp) as client:
            return (await client.list_tools()).tools

    tools = asyncio.run(run())
    assert {tool.name for tool in tools} == TOOLS
    for tool in tools:
        assert "Example" in (tool.description or ""), tool.name
        assert tool.annotations is not None and tool.annotations.read_only_hint, tool.name


def test_tool_result_is_flat_json_with_names() -> None:
    result = call("missing_time_logs", {})
    assert not result.is_error
    assert result.structured_content == {
        "date": "2026-11-09",
        "min_hours": 6.0,
        "missing": [
            {"person": "Jana Nováková", "logged_hours": 0.0, "expected_hours": 6.0},
            {"person": "Martin Horák", "logged_hours": 3.5, "expected_hours": 6.0},
        ],
    }


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        ("missing_time_logs", {"date": "10.11.2026"}, "Invalid date '10.11.2026'. Use YYYY-MM-DD"),
        ("get_time_entries", {"date_from": "today", "date_to": "2026-11-01"}, "is after"),
        ("project_budget_status", {"project": "xyz"}, 'No project matches "xyz". Known:'),
        ("unbilled_hours", {"client": "Acme"}, 'No client matches "Acme"'),
        ("get_time_entries", {"date_from": "today", "date_to": "today", "person": "ma"}, "ambig"),
    ],
)
def test_errors_are_returned_as_messages(
    name: str, arguments: dict[str, Any], message: str
) -> None:
    result = call(name, arguments)
    assert result.is_error
    assert message in result.content[0].text


def test_invalid_group_by_is_rejected() -> None:
    result = call("hours_summary", {"date_from": "today", "date_to": "today", "group_by": "team"})
    assert result.is_error


def test_configuration_error_is_returned_as_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> None:
        raise StudioPulseError("Invalid configuration: timezone: unknown time zone 'Mars'")

    monkeypatch.setattr(mcp_server, "_context", broken)
    result = call("list_projects", {})
    assert result.is_error
    assert "Invalid configuration" in result.content[0].text


def test_unexpected_error_does_not_leak_details(monkeypatch: pytest.MonkeyPatch) -> None:
    def crash() -> None:
        raise RuntimeError("secret internals")

    monkeypatch.setattr(mcp_server, "_context", crash)
    result = call("list_projects", {})
    assert result.is_error
    assert "secret internals" not in result.content[0].text
