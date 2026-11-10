from datetime import date

import pytest
from pydantic import ValidationError

from studio_pulse.models import Budget, BudgetStatus, Project, ReportData, TimeEntry


def test_time_entry_from_json_types() -> None:
    entry = TimeEntry.model_validate(
        {
            "id": "e1",
            "date": "2026-11-09",
            "person_id": "p1",
            "project_id": "pr1",
            "minutes": 90,
            "billable_minutes": 60,
        }
    )
    assert entry.date == date(2026, 11, 9)
    assert entry.note is None
    assert entry.invoiced is False


def test_negative_minutes_rejected() -> None:
    with pytest.raises(ValidationError):
        TimeEntry(
            id="e1",
            date=date(2026, 11, 9),
            person_id="p1",
            project_id="pr1",
            minutes=-5,
            billable_minutes=0,
        )


def test_budget_none_means_no_limit() -> None:
    assert Budget(project_id="pr1", budget_minutes=None).budget_minutes is None


def test_unknown_project_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        Project.model_validate({"id": "pr1", "name": "X", "client": "Y", "kind": "GAME"})


def test_report_data_round_trips_through_json() -> None:
    report = ReportData(
        day=date(2026, 11, 9),
        generated_on=date(2026, 11, 10),
        total_hours=46.5,
        billable_percent=82.0,
        low_billable_percent=70,
        billable_low=False,
        hours_by_project=[],
        budget_alerts=[
            BudgetStatus(
                project="Rezidencia Hradná, vizualizácie",
                client="Hradná Invest",
                budget_hours=80,
                used_hours=89.6,
                used_percent=112.0,
                remaining_hours=-9.6,
                status="over",
            )
        ],
        missing_logs=[],
        entries_without_note={"Peter Kováč": 3},
    )
    assert ReportData.model_validate_json(report.model_dump_json()) == report
