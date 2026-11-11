"""Productive (productive.io) data source: a read-only JSON:API client and all response mapping.

Endpoints, attributes and filters follow the official API reference at
https://developer.productive.io (OpenAPI spec and the pagination, filtering and rate-limit guides).

How Productive stores what Studio Pulse needs:

    company ─< project ─< deal (budget: true) ─< service ─< time entry >─ person

A time entry links only to a person and a service, so its project is found through
service → deal → project. A budget is a deal with `budget: true`; its time limit is the sum of
its services' `budgeted_time`.

Verified against a real account (the docs are unclear or say otherwise):
- `time`, `billable_time` and `budgeted_time` are all in minutes, although the reference calls
  the last two "hours" (a 20 h budget has `budgeted_time` 1200).
- Relationship linkage (`relationships.<name>.data`) is returned only for relationships listed
  in `include`, so every relationship read below is included.
- Notes are HTML (`<ul><li><p>Code review</p></li></ul>`) or null.

Drafts: time entries count whether or not they are submitted or approved, and drafts are
requested explicitly with `filter[with_draft]`. A draft still means the person logged the time;
"missing logs" should flag people who logged nothing, not people who have not submitted yet.
(Entries saved with "Save as draft" in the UI come back as `draft: false, submitted: false`.)
"""

import html
import logging
import re
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import date
from functools import cached_property
from typing import Any, NamedTuple

import httpx
from pydantic import SecretStr

from studio_pulse import StudioPulseError
from studio_pulse.models import Budget, Person, Project, ProjectKind, TimeEntry

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

BASE_URL = "https://api.productive.io/api/v2/"
PAGE_SIZE = 200  # documented `max_page_size`
TIMEOUT_SECONDS = 30.0
MAX_ATTEMPTS = 5  # first try plus four retries on HTTP 429
MAX_RETRY_WAIT_SECONDS = 30.0

HOURS_UNIT = 1  # service `unit_id`: 1 hours, 2 days, 3 pieces
INTERNAL_PROJECT_TYPE = 1  # project `project_type_id`: 1 internal overhead, 2 client work
# TODO: Productive has no project kind (3D, VR, WEB, CGI). Every client project gets this neutral
# value instead of a guess; see "Known limitations" in the README.
DEFAULT_PROJECT_KIND: ProjectKind = "OTHER"

Document = dict[str, Any]  # one JSON:API response body
Resource = dict[str, Any]  # one item of `data` or `included`


class Request(NamedTuple):
    """A list endpoint (relative to `BASE_URL`) and its query parameters, without paging."""

    path: str
    params: dict[str, str]


PEOPLE = Request("people", {})
PROJECTS = Request("projects", {"include": "company"})
DEALS = Request("deals", {"include": "project"})
SERVICES = Request("services", {"include": "deal"})


def time_entries_request(date_from: date, date_to: date) -> Request:
    """Time entries with `date_from <= date <= date_to`, drafts included."""
    return Request(
        "time_entries",
        {
            "filter[date][gt_eq]": date_from.isoformat(),
            "filter[date][lt_eq]": date_to.isoformat(),
            "filter[with_draft]": "true",
            "include": "person,service",
        },
    )


# HTTP


class ProductiveClient:
    """GET-only JSON:API client with pagination and retry on HTTP 429."""

    def __init__(
        self,
        token: SecretStr,
        org_id: str,
        *,
        base_url: str = BASE_URL,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._org_id = org_id
        self._base_url = base_url
        self._sleep = sleep

    def fetch_all(self, request: Request) -> list[Document]:
        """Every page of `request` as raw JSON:API documents, in order."""
        documents: list[Document] = []
        with httpx.Client(
            base_url=self._base_url, timeout=TIMEOUT_SECONDS, headers=self._headers()
        ) as http:
            page = 1
            while True:
                params = {**request.params, "page[number]": str(page), "page[size]": str(PAGE_SIZE)}
                document = self._get(http, request.path, params)
                documents.append(document)
                total_pages = document.get("meta", {}).get("total_pages")
                if not isinstance(total_pages, int) or page >= total_pages:
                    return documents
                page += 1

    def _headers(self) -> dict[str, str]:
        return {
            "X-Auth-Token": self._token.get_secret_value(),
            "X-Organization-Id": self._org_id,
            "Content-Type": "application/vnd.api+json",
        }

    def _get(self, http: httpx.Client, path: str, params: dict[str, str]) -> Document:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = http.get(path, params=params)
            except httpx.HTTPError as exc:
                raise StudioPulseError(
                    f"Could not reach the Productive API ({type(exc).__name__})."
                ) from None
            if response.status_code == 429 and attempt < MAX_ATTEMPTS:
                wait = _retry_wait(response, attempt)
                logger.warning("Productive rate limit reached, retrying in %.1f s", wait)
                self._sleep(wait)
                continue
            if response.status_code in (401, 403):
                raise StudioPulseError(
                    f"Productive rejected the request (HTTP {response.status_code}). "
                    "Check PRODUCTIVE_API_TOKEN and PRODUCTIVE_ORG_ID."
                )
            if not response.is_success:
                raise StudioPulseError(
                    f"Productive API returned HTTP {response.status_code} for /{path}: "
                    f"{response.text[:200].strip()}"
                )
            try:
                return response.json()
            except ValueError:
                raise StudioPulseError(
                    f"Productive API returned invalid JSON for /{path}."
                ) from None
        raise AssertionError("unreachable")  # the last attempt always returns or raises


def _retry_wait(response: httpx.Response, attempt: int) -> float:
    """Seconds to wait: the documented `X-RateLimit-Reset` header, else exponential backoff."""
    try:
        wait = float(response.headers["X-RateLimit-Reset"])
    except (KeyError, ValueError):
        wait = 2.0 ** (attempt - 1)
    return min(max(wait, 0.0), MAX_RETRY_WAIT_SECONDS)


# JSON:API helpers


def _data(documents: Iterable[Document]) -> list[Resource]:
    return [resource for document in documents for resource in document.get("data", [])]


def _included(documents: Iterable[Document]) -> dict[tuple[str, str], Resource]:
    return {
        (resource["type"], resource["id"]): resource
        for document in documents
        for resource in document.get("included", [])
    }


def _attr(resource: Resource, name: str, default: Any = None) -> Any:
    value = resource.get("attributes", {}).get(name)
    return default if value is None else value


def _rel_id(resource: Resource, name: str) -> str | None:
    """Id of a to-one relationship, or None when it is empty or not returned."""
    linkage = resource.get("relationships", {}).get(name, {}).get("data")
    return linkage["id"] if isinstance(linkage, dict) else None


def _is_sample(resource: Resource) -> bool:
    return bool(_attr(resource, "sample_data", False))


def note_text(note: str | None) -> str | None:
    """Plain text of an HTML note; None when it holds no text (e.g. `<p></p>`)."""
    if note is None:
        return None
    text = html.unescape(re.sub(r"<[^>]*>", " ", note))
    return " ".join(text.split()) or None


# Mapping


def map_people(documents: list[Document]) -> list[Person]:
    """Team members who can track time: users, without placeholders, AI agents or sample data."""
    people = []
    for resource in _data(documents):
        if _is_sample(resource) or not _attr(resource, "is_user", False):
            continue
        if _attr(resource, "placeholder", False) or _attr(resource, "agent", False):
            continue
        name = f"{_attr(resource, 'first_name', '')} {_attr(resource, 'last_name', '')}".strip()
        active = (
            _attr(resource, "deactivated_at") is None and _attr(resource, "archived_at") is None
        )
        people.append(
            Person(
                id=resource["id"],
                name=name or resource["id"],
                email=_attr(resource, "email"),
                active=active,
            )
        )
    return people


def map_projects(documents: list[Document]) -> list[Project]:
    """Projects with their client company name; sample data skipped."""
    companies = _included(documents)
    projects = []
    for resource in _data(documents):
        if _is_sample(resource):
            continue
        company_id = _rel_id(resource, "company")
        company = companies.get(("companies", company_id)) if company_id else None
        is_internal = _attr(resource, "project_type_id") == INTERNAL_PROJECT_TYPE
        projects.append(
            Project(
                id=resource["id"],
                name=_attr(resource, "name", resource["id"]),
                client=_attr(company, "name", "") if company else "",
                kind="INTERNAL" if is_internal else DEFAULT_PROJECT_KIND,
                active=_attr(resource, "archived_at") is None,
            )
        )
    return projects


def map_budgets(
    projects: list[Project], deal_documents: list[Document], service_documents: list[Document]
) -> list[Budget]:
    """Time limit per project: the sum of `budgeted_time` (minutes) of its budgets' services.

    A project without a budget, or whose budget services set no time (such as an internal
    budget with one shared service and no hour limit), has no limit (`None`). Only services
    tracked in hours count.
    """
    project_of_budget = {
        deal["id"]: project_id
        for deal in _data(deal_documents)
        if _attr(deal, "budget", False)
        and not _is_sample(deal)
        and (project_id := _rel_id(deal, "project"))
    }
    limits: dict[str, int] = defaultdict(int)
    for service in _data(service_documents):
        project_id = project_of_budget.get(_rel_id(service, "deal") or "")
        # TODO: services tracked in days or pieces are skipped: what their `budgeted_time` means
        # is not documented. See "Known limitations" in the README.
        if project_id and _attr(service, "unit_id") == HOURS_UNIT:
            limits[project_id] += max(_attr(service, "budgeted_time", 0), 0)
    return [
        Budget(project_id=project.id, budget_minutes=limits.get(project.id) or None)
        for project in projects
    ]


def project_of_service(
    deal_documents: list[Document], service_documents: list[Document]
) -> dict[str, str]:
    """Service id → project id, through the service's deal."""
    project_of_deal = {
        deal["id"]: project_id
        for deal in _data(deal_documents)
        if (project_id := _rel_id(deal, "project"))
    }
    return {
        service["id"]: project_of_deal[deal_id]
        for service in _data(service_documents)
        if (deal_id := _rel_id(service, "deal")) in project_of_deal
    }


def map_time_entries(
    documents: list[Document],
    service_projects: dict[str, str],
    person_ids: set[str],
    project_ids: set[str],
) -> list[TimeEntry]:
    """Time entries of known people on known projects.

    Entries outside those sets are skipped: sample data, time off (no service) and contacts.
    """
    entries = []
    skipped = 0
    for resource in _data(documents):
        person_id = _rel_id(resource, "person")
        project_id = service_projects.get(_rel_id(resource, "service") or "")
        if person_id not in person_ids or project_id not in project_ids:
            skipped += 1
            continue
        entries.append(
            TimeEntry(
                id=resource["id"],
                date=date.fromisoformat(_attr(resource, "date")),
                person_id=person_id,
                project_id=project_id,
                minutes=_attr(resource, "time", 0),
                billable_minutes=_attr(resource, "billable_time", 0),
                note=note_text(_attr(resource, "note")),
                invoiced=bool(_attr(resource, "invoiced", False)),
            )
        )
    if skipped:
        logger.debug("Skipped %d Productive time entries outside known people/projects", skipped)
    return entries


# Data source


class ProductiveSource:
    """`DataSource` reading Productive. Lists are fetched once per instance."""

    def __init__(self, client: ProductiveClient) -> None:
        self._client = client

    def people(self) -> list[Person]:
        return list(self._people)

    def projects(self) -> list[Project]:
        return list(self._projects)

    def budgets(self) -> list[Budget]:
        return map_budgets(self._projects, self._deals, self._services)

    def time_entries(self, date_from: date, date_to: date) -> list[TimeEntry]:
        documents = self._client.fetch_all(time_entries_request(date_from, date_to))
        return map_time_entries(
            documents,
            project_of_service(self._deals, self._services),
            {person.id for person in self._people},
            {project.id for project in self._projects},
        )

    @cached_property
    def _people(self) -> list[Person]:
        return map_people(self._client.fetch_all(PEOPLE))

    @cached_property
    def _projects(self) -> list[Project]:
        return map_projects(self._client.fetch_all(PROJECTS))

    @cached_property
    def _deals(self) -> list[Document]:
        return self._client.fetch_all(DEALS)

    @cached_property
    def _services(self) -> list[Document]:
        return self._client.fetch_all(SERVICES)
