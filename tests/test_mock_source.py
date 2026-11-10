import importlib.util
import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from studio_pulse import StudioPulseError
from studio_pulse.config import Settings
from studio_pulse.dates import previous_working_day
from studio_pulse.models import TimeEntry
from studio_pulse.sources import MockSource, get_source

ROOT = Path(__file__).resolve().parents[1]
TUESDAY = date(2026, 11, 10)
ALL_TIME = (date(2000, 1, 1), date(2100, 1, 1))


def load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "generate_mock", ROOT / "scripts/generate_mock.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source() -> MockSource:
    return MockSource(today=TUESDAY)


def day_entries(source: MockSource, day: date) -> list[TimeEntry]:
    return source.time_entries(day, day)


def minutes_by_person(source: MockSource, day: date) -> dict[str, int]:
    names = {person.id: person.name for person in source.people()}
    totals = {person.name: 0 for person in source.people()}
    for entry in day_entries(source, day):
        totals[names[entry.person_id]] += entry.minutes
    return totals


def test_committed_mock_data_matches_generator() -> None:
    generated = load_generator().generate()
    for name, records in generated.items():
        committed = json.loads((ROOT / "mock" / f"{name}.json").read_text(encoding="utf-8"))
        assert committed == records, f"mock/{name}.json is stale, run scripts/generate_mock.py"


def test_generator_is_deterministic() -> None:
    generator = load_generator()
    assert generator.generate() == generator.generate()


def test_agency_size(source: MockSource) -> None:
    assert len(source.people()) == 8
    assert len(source.projects()) == 5
    assert {project.kind for project in source.projects()} == {"3D", "VR", "WEB", "CGI", "INTERNAL"}


def test_budget_scenarios(source: MockSource) -> None:
    used = Counter[str]()
    for entry in source.time_entries(*ALL_TIME):
        used[entry.project_id] += entry.minutes
    budgets = {budget.project_id: budget.budget_minutes for budget in source.budgets()}
    names = {project.id: project.name for project in source.projects()}

    percent = {
        names[pid]: round(100 * used[pid] / limit) for pid, limit in budgets.items() if limit
    }
    assert percent == {
        "Lesná Brána, VR prehliadka": 91,
        "Rezidencia Hradná, vizualizácie": 112,
        "Riverside Lofts, web": 54,
        "HR kampaň, video": 30,
    }
    assert budgets["pr-rnd"] is None


def test_previous_working_day_problems(source: MockSource) -> None:
    day = previous_working_day(TUESDAY)
    totals = minutes_by_person(source, day)

    assert totals["Jana Nováková"] == 0
    assert totals["Martin Horák"] == 210
    assert totals["Zuzana Kráľová"] == 360  # exactly at the 6 h threshold
    assert sum(totals.values()) == 2790  # 46.5 h

    without_note = [e for e in day_entries(source, day) if e.note is None]
    assert len(without_note) == 3
    assert {e.person_id for e in without_note} == {"p3"}  # Peter Kováč


def test_data_covers_last_two_weeks_without_weekends(source: MockSource) -> None:
    entries = source.time_entries(*ALL_TIME)
    days = {entry.date for entry in entries}

    assert len(days) == 10
    assert max(days) == previous_working_day(TUESDAY)
    assert TUESDAY - min(days) <= timedelta(days=14)
    assert all(day.weekday() < 5 for day in days)


@pytest.mark.parametrize("today", [date(2026, 11, 9), date(2026, 11, 14), date(2026, 11, 15)])
def test_monday_and_weekend_map_previous_day_to_friday(today: date) -> None:
    source = MockSource(today=today)
    friday = date(2026, 11, 6) if today.day == 9 else date(2026, 11, 13)
    assert sum(minutes_by_person(source, friday).values()) == 2790


def test_billable_and_invoiced_flags(source: MockSource) -> None:
    entries = source.time_entries(*ALL_TIME)
    internal = [e for e in entries if e.project_id == "pr-rnd"]
    client = [e for e in entries if e.project_id != "pr-rnd"]

    assert all(e.billable_minutes == 0 and not e.invoiced for e in internal)
    assert all(e.billable_minutes == e.minutes for e in client)
    recent = previous_working_day(TUESDAY)
    assert not any(e.invoiced for e in day_entries(source, recent))
    assert any(e.invoiced for e in client)


def test_time_entries_range_is_inclusive(source: MockSource) -> None:
    day = previous_working_day(TUESDAY)
    assert source.time_entries(day, day)
    assert source.time_entries(TUESDAY, TUESDAY) == []


def test_missing_mock_dir_gives_readable_error(tmp_path: Path) -> None:
    with pytest.raises(StudioPulseError, match=r"generate_mock\.py"):
        MockSource(today=TUESDAY, data_dir=tmp_path)


def test_invalid_mock_file_gives_readable_error(tmp_path: Path) -> None:
    (tmp_path / "people.json").write_text('[{"id": "p1"}]', encoding="utf-8")
    with pytest.raises(StudioPulseError, match="invalid content"):
        MockSource(today=TUESDAY, data_dir=tmp_path)


def test_get_source_selects_mock() -> None:
    assert isinstance(get_source(Settings(data_source="mock")), MockSource)


def test_get_source_productive_not_implemented_yet() -> None:
    with pytest.raises(StudioPulseError, match="not implemented"):
        get_source(Settings(data_source="productive"))
