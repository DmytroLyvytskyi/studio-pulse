from datetime import date

import pytest

from studio_pulse.analyze import (
    billable_percent,
    budget_status,
    build_report,
    entries_without_note,
    hours_by_group,
    hours_by_project,
    missing_logs,
    to_hours,
    unbilled_hours,
)
from studio_pulse.config import Settings
from studio_pulse.models import Budget, Person, Project, ReportData, TimeEntry
from studio_pulse.sources import MockSource

TUESDAY = date(2026, 11, 10)
MONDAY = date(2026, 11, 9)

PEOPLE = [
    Person(id="a", name="Anna"),
    Person(id="b", name="Boris"),
    Person(id="c", name="Cyril", active=False),
]
PROJECTS = [
    Project(id="x", name="Projekt X", client="Klient X", kind="3D"),
    Project(id="y", name="Projekt Y", client="Klient Y", kind="WEB"),
    Project(id="r", name="R&D", client="Studio", kind="INTERNAL"),
]


def entry(
    person: str = "a",
    project: str = "x",
    minutes: int = 60,
    billable: int | None = None,
    day: date = MONDAY,
    note: str | None = "work",
    invoiced: bool = False,
) -> TimeEntry:
    return TimeEntry(
        id=f"{person}-{project}-{minutes}-{day}",
        date=day,
        person_id=person,
        project_id=project,
        minutes=minutes,
        billable_minutes=minutes if billable is None else billable,
        note=note,
        invoiced=invoiced,
    )


# to_hours


@pytest.mark.parametrize(
    ("minutes", "hours"), [(0, 0.0), (15, 0.3), (45, 0.8), (90, 1.5), (100, 1.7), (-576, -9.6)]
)
def test_to_hours_rounds_half_up(minutes: int, hours: float) -> None:
    assert to_hours(minutes) == hours


# missing_logs


def test_missing_logs_lists_active_people_below_threshold() -> None:
    entries = [entry("a", minutes=200), entry("b", minutes=360)]
    result = missing_logs(entries, PEOPLE, MONDAY, min_hours=6)
    assert [(m.person, m.logged_hours, m.expected_hours) for m in result] == [("Anna", 3.3, 6)]


def test_missing_logs_no_entries_lists_everyone_active_with_zero() -> None:
    result = missing_logs([], PEOPLE, MONDAY, min_hours=6)
    assert [(m.person, m.logged_hours) for m in result] == [("Anna", 0.0), ("Boris", 0.0)]


def test_missing_logs_exactly_at_threshold_is_fine() -> None:
    entries = [entry("a", minutes=360), entry("b", minutes=359)]
    assert [m.person for m in missing_logs(entries, PEOPLE, MONDAY, min_hours=6)] == ["Boris"]


def test_missing_logs_ignores_other_days_and_sums_entries() -> None:
    entries = [
        entry("a", minutes=300, day=date(2026, 11, 6)),
        entry("a", minutes=200),
        entry("a", minutes=200, project="y"),
    ]
    result = missing_logs(entries, PEOPLE, MONDAY, min_hours=6)
    assert [m.person for m in result] == ["Boris"]


def test_missing_logs_zero_minute_entry_counts_as_nothing() -> None:
    result = missing_logs([entry("a", minutes=0)], PEOPLE, MONDAY, min_hours=6)
    assert [(m.person, m.logged_hours) for m in result] == [("Anna", 0.0), ("Boris", 0.0)]


# budget_status


def statuses_for(
    used_minutes: int, budget_minutes: int | None, warning: float = 80
) -> dict[str, object]:
    entries = [entry(project="x", minutes=used_minutes)] if used_minutes else []
    result = budget_status(entries, PROJECTS[:1], [budget("x", budget_minutes)], warning)
    return result[0].model_dump()


def budget(project_id: str, budget_minutes: int | None) -> Budget:
    return Budget(project_id=project_id, budget_minutes=budget_minutes)


@pytest.mark.parametrize(
    ("used", "limit", "status"),
    [
        (0, 600, "ok"),
        (479, 600, "ok"),
        (480, 600, "warning"),  # exactly 80 %
        (600, 600, "warning"),  # exactly 100 %
        (601, 600, "over"),
        (300, None, "no_budget"),
    ],
)
def test_budget_status_thresholds(used: int, limit: int | None, status: str) -> None:
    assert statuses_for(used, limit)["status"] == status


def test_budget_status_numbers() -> None:
    assert statuses_for(480, 600) == {
        "project": "Projekt X",
        "client": "Klient X",
        "budget_hours": 10.0,
        "used_hours": 8.0,
        "used_percent": 80.0,
        "remaining_hours": 2.0,
        "status": "warning",
    }


def test_budget_status_over_has_negative_remaining() -> None:
    result = statuses_for(720, 600)
    assert (result["used_percent"], result["remaining_hours"]) == (120.0, -2.0)


def test_budget_status_without_budget_has_no_percent() -> None:
    result = statuses_for(300, None)
    assert result["budget_hours"] is None
    assert result["used_percent"] is None
    assert result["remaining_hours"] is None
    assert result["used_hours"] == 5.0


def test_budget_status_zero_budget() -> None:
    assert statuses_for(0, 0)["status"] == "ok"
    assert statuses_for(60, 0)["status"] == "over"
    assert statuses_for(60, 0)["used_percent"] is None


def test_budget_status_respects_configured_warning_percent() -> None:
    assert statuses_for(540, 600, warning=95)["status"] == "ok"  # 90 %
    assert statuses_for(570, 600, warning=95)["status"] == "warning"  # 95 %


def test_budget_status_project_without_budget_record_is_no_budget() -> None:
    result = budget_status([], PROJECTS[:1], [], 80)
    assert result[0].status == "no_budget"


def test_budget_status_sorted_by_severity_then_percent() -> None:
    entries = [entry(project="x", minutes=300), entry(project="y", minutes=700)]
    budgets = [budget("x", 600), budget("y", 600), budget("r", None)]
    result = budget_status(entries, PROJECTS, budgets, 80)
    assert [s.status for s in result] == ["over", "ok", "no_budget"]


# hours_by_project


def test_hours_by_project_sorted_by_total() -> None:
    entries = [
        entry(project="x", minutes=60),
        entry(project="y", minutes=120, billable=90),
        entry(project="r", minutes=30, billable=0),
        entry("b", project="x", minutes=30),
    ]
    result = hours_by_project(entries, PROJECTS)
    assert [(r.project, r.client, r.total_hours, r.billable_hours) for r in result] == [
        ("Projekt Y", "Klient Y", 2.0, 1.5),
        ("Projekt X", "Klient X", 1.5, 1.5),
        ("R&D", "Studio", 0.5, 0.0),
    ]


def test_hours_by_project_no_entries() -> None:
    assert hours_by_project([], PROJECTS) == []


# hours_by_group


def test_hours_by_group() -> None:
    entries = [
        entry("a", project="x", minutes=60),
        entry("a", project="r", minutes=60, billable=0),
        entry("b", project="y", minutes=180, billable=90),
        entry("b", project="x", minutes=30),
    ]
    by_person = hours_by_group(entries, PEOPLE, PROJECTS, "person")
    assert [(g.name, g.total_hours, g.billable_hours, g.billable_percent) for g in by_person] == [
        ("Boris", 3.5, 2.0, 57.1),
        ("Anna", 2.0, 1.0, 50.0),
    ]
    by_client = hours_by_group(entries, PEOPLE, PROJECTS, "client")
    assert [(g.name, g.total_hours) for g in by_client] == [
        ("Klient Y", 3.0),
        ("Klient X", 1.5),
        ("Studio", 1.0),
    ]
    by_project = hours_by_group(entries, PEOPLE, PROJECTS, "project")
    assert [(g.name, g.total_hours) for g in by_project] == [
        ("Projekt Y", 3.0),
        ("Projekt X", 1.5),
        ("R&D", 1.0),
    ]


def test_hours_by_group_no_entries_and_zero_minutes() -> None:
    assert hours_by_group([], PEOPLE, PROJECTS, "person") == []
    [group] = hours_by_group([entry(minutes=0)], PEOPLE, PROJECTS, "person")
    assert (group.total_hours, group.billable_percent) == (0.0, 0.0)


def test_hours_by_group_unknown_ids_kept_under_id(caplog: pytest.LogCaptureFixture) -> None:
    entries = [entry("ghost", project="lost")]
    with caplog.at_level("WARNING", logger="studio_pulse.analyze"):
        assert [g.name for g in hours_by_group(entries, PEOPLE, PROJECTS, "person")] == ["ghost"]
        assert [g.name for g in hours_by_group(entries, PEOPLE, PROJECTS, "client")] == ["lost"]
    assert len(caplog.records) == 2


# billable_percent


def test_billable_percent() -> None:
    entries = [entry(minutes=90), entry(project="r", minutes=30, billable=0)]
    assert billable_percent(entries) == 75.0


def test_billable_percent_no_entries_or_zero_minutes() -> None:
    assert billable_percent([]) == 0.0
    assert billable_percent([entry(minutes=0)]) == 0.0


# entries_without_note


def test_entries_without_note_counts_empty_and_blank_notes() -> None:
    entries = [
        entry("a", note=None),
        entry("b", note=None),
        entry("b", note="   "),
        entry("b", note=""),
        entry("a", note="Rendering"),
    ]
    assert entries_without_note(entries, PEOPLE) == {"Boris": 3, "Anna": 1}


def test_entries_without_note_none_missing() -> None:
    assert entries_without_note([entry()], PEOPLE) == {}


# unbilled_hours


def test_unbilled_hours_per_client() -> None:
    entries = [
        entry(project="x", minutes=120),
        entry(project="x", minutes=600, invoiced=True),
        entry(project="y", minutes=90, billable=60),
        entry(project="r", minutes=240, billable=0),
    ]
    assert unbilled_hours(entries, PROJECTS) == {"Klient X": 2.0, "Klient Y": 1.0}
    assert unbilled_hours(entries, PROJECTS, client="Klient Y") == {"Klient Y": 1.0}
    assert unbilled_hours(entries, PROJECTS, client="Nobody") == {}


def test_unbilled_hours_no_entries() -> None:
    assert unbilled_hours([], PROJECTS) == {}


# Mock scenarios (expected numbers from the spec, not from the generator)


@pytest.fixture
def settings() -> Settings:
    return Settings(min_hours_per_day=6, budget_warning_percent=80)


@pytest.fixture
def mock_report(settings: Settings) -> ReportData:
    return build_report(MockSource(today=TUESDAY), MONDAY, settings)


def test_mock_report_totals(mock_report: ReportData) -> None:
    assert mock_report.day == MONDAY
    assert mock_report.total_hours == 46.5
    assert mock_report.billable_percent == 81.7  # shown as 82 %
    assert round(mock_report.billable_percent) == 82


def test_mock_report_budget_alerts(mock_report: ReportData) -> None:
    alerts = [
        (a.project, a.status, a.used_percent, a.remaining_hours) for a in mock_report.budget_alerts
    ]
    assert alerts == [
        ("Rezidencia Hradná, vizualizácie", "over", 112.0, -15.6),
        ("Lesná Brána, VR prehliadka", "warning", 91.0, 16.2),
    ]


def test_mock_report_missing_logs_and_notes(mock_report: ReportData) -> None:
    assert [(m.person, m.logged_hours) for m in mock_report.missing_logs] == [
        ("Jana Nováková", 0.0),
        ("Martin Horák", 3.5),
    ]
    assert mock_report.entries_without_note == {"Peter Kováč": 3}


def test_mock_report_hours_by_project(mock_report: ReportData) -> None:
    rows = [(r.project, r.total_hours, r.billable_hours) for r in mock_report.hours_by_project]
    assert rows == [
        ("Rezidencia Hradná, vizualizácie", 12.0, 12.0),
        ("Lesná Brána, VR prehliadka", 11.5, 11.5),
        ("Interné R&D", 8.5, 0.0),
        ("Riverside Lofts, web", 8.5, 8.5),
        ("HR kampaň, video", 6.0, 6.0),
    ]


def test_mock_budget_status_all_projects() -> None:
    source = MockSource(today=TUESDAY)
    history = source.time_entries(date(2000, 1, 1), MONDAY)
    result = budget_status(history, source.projects(), source.budgets(), 80)
    assert [(s.project, s.status, s.used_percent) for s in result] == [
        ("Rezidencia Hradná, vizualizácie", "over", 112.0),
        ("Lesná Brána, VR prehliadka", "warning", 91.0),
        ("Riverside Lofts, web", "ok", 54.0),
        ("HR kampaň, video", "ok", 30.0),
        ("Interné R&D", "no_budget", None),
    ]


def test_mock_report_on_monday_covers_friday(settings: Settings) -> None:
    friday = date(2026, 11, 6)
    report = build_report(MockSource(today=MONDAY), friday, settings)
    assert report.total_hours == 46.5
    assert [m.person for m in report.missing_logs] == ["Jana Nováková", "Martin Horák"]


def test_mock_unbilled_hours_excludes_internal_and_filters_client() -> None:
    source = MockSource(today=TUESDAY)
    entries = source.time_entries(date(2000, 1, 1), MONDAY)
    result = unbilled_hours(entries, source.projects())
    assert set(result) == {
        "Lesná Brána Development",
        "Hradná Invest",
        "Riverside Lofts",
        "Nordica HR",
    }
    assert all(hours > 0 for hours in result.values())
    assert unbilled_hours(entries, source.projects(), client="Nordica HR").keys() == {"Nordica HR"}


# Unknown ids


def test_unknown_ids_log_a_warning_and_keep_the_id_as_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    entries = [entry("ghost", project="lost", note=None)]
    with caplog.at_level("WARNING", logger="studio_pulse.analyze"):
        rows = hours_by_project(entries, PROJECTS)
        notes = entries_without_note(entries, PEOPLE)
        unbilled = unbilled_hours(entries, PROJECTS)
        missing_logs(entries, PEOPLE, MONDAY, min_hours=6)
        budget_status(entries, PROJECTS, [], 80)

    assert [row.project for row in rows] == ["lost"]
    assert notes == {"ghost": 1}
    assert unbilled == {"lost": 1.0}
    messages = [record.getMessage() for record in caplog.records]
    assert messages == [
        "Time entries reference unknown project ids: lost",
        "Time entries reference unknown person ids: ghost",
        "Time entries reference unknown project ids: lost",
        "Time entries reference unknown person ids: ghost",
        "Time entries reference unknown project ids: lost",
    ]


def test_known_ids_log_nothing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="studio_pulse.analyze"):
        build_report(MockSource(today=TUESDAY), MONDAY, Settings())
    assert caplog.records == []
