"""Productive mapping against real responses saved by scripts/save_productive_fixtures.py.

The trial account: 3 people, 4 projects (plus an archived one), per-person
hourly services on the client budgets, and one non-billable service without a time limit on the
internal budget. Expected numbers are those the PM sees in Productive for 2026-11-10.
"""

import copy
import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from studio_pulse import analyze
from studio_pulse.config import Settings
from studio_pulse.models import Budget, Project
from studio_pulse.sources.productive import (
    BASE_URL,
    ProductiveClient,
    ProductiveSource,
    map_budgets,
    note_text,
)

FIXTURES = Path(__file__).parent / "fixtures" / "productive"
DAY = date(2026, 11, 10)
TODAY = date(2026, 11, 11)


def load(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def time_entries_between(request: httpx.Request, pages: list[dict[str, Any]]) -> httpx.Response:
    """Serve the saved entries the way the API applies the date filters."""
    params = request.url.params
    assert params["filter[with_draft]"] == "true"
    assert set(params["include"].split(",")) == {"person", "service"}
    low, high = params["filter[date][gt_eq]"], params["filter[date][lt_eq]"]
    document = copy.deepcopy(pages[0])
    document["data"] = [
        entry
        for page in pages
        for entry in page["data"]
        if low <= entry["attributes"]["date"] <= high
    ]
    document["meta"] = {"current_page": 1, "total_pages": 1}
    return httpx.Response(200, json=document)


def mock_productive(
    router: respx.MockRouter, overrides: dict[str, list[dict[str, Any]]] | None = None
) -> None:
    fixtures = {name: load(name) for name in ("people", "projects", "deals", "services")}
    fixtures.update(overrides or {})
    for name, pages in fixtures.items():
        router.get(f"{BASE_URL}{name}").mock(
            side_effect=lambda request, pages=pages: httpx.Response(
                200, json=pages[int(request.url.params["page[number]"]) - 1]
            )
        )
    entries = load("time_entries")
    router.get(f"{BASE_URL}time_entries").mock(
        side_effect=lambda request: time_entries_between(request, entries)
    )


@pytest.fixture
def source() -> ProductiveSource:
    return ProductiveSource(ProductiveClient(SecretStr("token"), "1", sleep=lambda _: None))


@pytest.fixture
def mocked() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        mock_productive(router)
        yield router


@pytest.mark.usefixtures("mocked")
def test_people(source: ProductiveSource) -> None:
    people = {person.name: person for person in source.people()}
    assert set(people) == {"Jana Nováková", "Martin Horák", "Peter Kováč"}
    assert people["Jana Nováková"].email == "jana.novakova@studio-pulse.example"
    assert all(person.active for person in people.values())


@pytest.mark.usefixtures("mocked")
def test_projects(source: ProductiveSource) -> None:
    projects = {(p.name, p.active): p for p in source.projects()}
    assert set(projects) == {
        ("Interné R&D", True),
        ("TEST", False),  # archived project in the trial account
        ("Lesná Brána, VR prehliadka", True),
        ("Rezidencia Hradná, vizualizácie", True),
        ("Riverside Lofts, web", True),
    }
    hradna = projects[("Rezidencia Hradná, vizualizácie", True)]
    assert (hradna.client, hradna.kind) == ("Hradná Invest", "OTHER")
    internal = projects[("Interné R&D", True)]
    assert (internal.client, internal.kind) == ("Studio Pulse Demo", "INTERNAL")


@pytest.mark.usefixtures("mocked")
def test_budget_status(source: ProductiveSource) -> None:
    projects = [p for p in source.projects() if p.active]
    entries = source.time_entries(analyze.HISTORY_START, DAY)
    statuses = {
        s.project: s for s in analyze.budget_status(entries, projects, source.budgets(), 80)
    }

    lesna = statuses["Lesná Brána, VR prehliadka"]
    assert (lesna.used_hours, lesna.budget_hours, lesna.used_percent, lesna.status) == (
        17.0,
        20.0,
        85.0,
        "warning",
    )
    hradna = statuses["Rezidencia Hradná, vizualizácie"]
    assert (hradna.used_hours, hradna.budget_hours, hradna.remaining_hours, hradna.status) == (
        13.5,
        12.0,
        -1.5,
        "over",
    )
    riverside = statuses["Riverside Lofts, web"]
    assert (riverside.used_hours, riverside.budget_hours, riverside.status) == (12.0, 40.0, "ok")
    internal = statuses["Interné R&D"]
    assert (internal.used_hours, internal.budget_hours, internal.status) == (
        12.5,
        None,
        "no_budget",
    )


@pytest.mark.usefixtures("mocked")
def test_daily_report(source: ProductiveSource) -> None:
    report = analyze.build_report(source, DAY, Settings(), today=TODAY)

    assert report.total_hours == 10.5
    assert report.billable_percent == 57.1
    assert report.billable_low
    assert [(m.person, m.logged_hours) for m in report.missing_logs] == [
        ("Jana Nováková", 0.0),
        ("Martin Horák", 3.5),
    ]
    assert report.entries_without_note == {"Peter Kováč": 2}
    assert {(s.project, s.status) for s in report.budget_alerts} == {
        ("Lesná Brána, VR prehliadka", "warning"),
        ("Rezidencia Hradná, vizualizácie", "over"),
    }


@pytest.mark.usefixtures("mocked")
def test_unbilled_hours(source: ProductiveSource) -> None:
    entries = source.time_entries(analyze.HISTORY_START, DAY)
    assert analyze.unbilled_hours(entries, source.projects(), "Hradná Invest") == {
        "Hradná Invest": 13.5
    }


@pytest.mark.usefixtures("mocked")
def test_notes_are_plain_text(source: ProductiveSource) -> None:
    notes = {entry.note for entry in source.time_entries(DAY, DAY)}
    assert "Code review" in notes
    assert not any("<" in note for note in notes if note)


def test_sample_data_is_ignored(source: ProductiveSource) -> None:
    people, projects = load("people"), load("projects")
    people[0]["data"][0]["attributes"]["sample_data"] = True
    sample_project = next(
        p for p in projects[0]["data"] if p["attributes"]["name"].startswith("Lesná")
    )
    sample_project["attributes"]["sample_data"] = True
    with respx.mock(assert_all_called=False) as router:
        mock_productive(router, {"people": people, "projects": projects})
        assert people[0]["data"][0]["id"] not in {p.id for p in source.people()}
        assert sample_project["id"] not in {p.id for p in source.projects()}
        entries = source.time_entries(analyze.HISTORY_START, DAY)
        assert people[0]["data"][0]["id"] not in {e.person_id for e in entries}
        assert sample_project["id"] not in {e.project_id for e in entries}


def deal(deal_id: str, project_id: str) -> dict[str, Any]:
    return {
        "id": deal_id,
        "type": "deals",
        "attributes": {"budget": True, "sample_data": False},
        "relationships": {"project": {"data": {"type": "projects", "id": project_id}}},
    }


def service(deal_id: str, unit_id: int, budgeted_time: int) -> dict[str, Any]:
    return {
        "id": f"s-{deal_id}-{unit_id}",
        "type": "services",
        "attributes": {"unit_id": unit_id, "budgeted_time": budgeted_time},
        "relationships": {"deal": {"data": {"type": "deals", "id": deal_id}}},
    }


def test_budget_counts_only_hour_services() -> None:
    projects = [
        Project(id="p1", name="Hodiny", client="K", kind="OTHER"),
        Project(id="p2", name="Dni", client="K", kind="OTHER"),
        Project(id="p3", name="Bez rozpočtu", client="K", kind="OTHER"),
    ]
    deals = [{"data": [deal("d1", "p1"), deal("d2", "p2")]}]
    services = [{"data": [service("d1", 1, 600), service("d1", 2, 5), service("d2", 2, 3)]}]
    assert map_budgets(projects, deals, services) == [
        Budget(project_id="p1", budget_minutes=600),
        Budget(project_id="p2", budget_minutes=None),
        Budget(project_id="p3", budget_minutes=None),
    ]


@pytest.mark.parametrize(
    ("html", "text"),
    [
        ("<ul><li><p>Code review</p></li></ul>", "Code review"),
        ("<p>Oprava &amp; test</p><p>formulára</p>", "Oprava & test formulára"),
        ("<p></p>", None),
        ("", None),
        (None, None),
    ],
)
def test_note_text(html: str | None, text: str | None) -> None:
    assert note_text(html) == text
