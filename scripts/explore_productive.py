"""Answer the questions the Productive API docs leave open, against a real account. Read only.

Usage: uv run python scripts/explore_productive.py [YYYY-MM-DD]   (default day: 2026-11-10)

Needs PRODUCTIVE_API_TOKEN and PRODUCTIVE_ORG_ID in the environment or .env. Prints ids, names,
flags and numbers only: no emails, no token.

Open questions:
1. Drafts: what do the default list, `filter[with_draft]` and `filter[only_draft]` return, and
   which attributes (`status`, `submitted`, `approved`, ...) mark a draft?
2. Units: are `time`, `billable_time`, `budgeted_time` and `estimated_time` in minutes?
3. Is relationship linkage returned without `include`?
4. Which person flags separate team members from client contacts?
5. Do `filter[date][gt_eq]` / `filter[date][lt_eq]` include both ends?
6. Pagination meta.
"""

import json
import sys
from typing import Any

from studio_pulse.config import load_settings
from studio_pulse.sources.productive import (
    DEALS,
    PEOPLE,
    PROJECTS,
    SERVICES,
    Document,
    ProductiveClient,
    Request,
)


def resources(documents: list[Document]) -> list[dict[str, Any]]:
    return [resource for document in documents for resource in document.get("data", [])]


def attrs(resource: dict[str, Any], *names: str) -> str:
    values = resource.get("attributes", {})
    return " ".join(f"{name}={values.get(name)!r}" for name in names)


def rel(resource: dict[str, Any], name: str) -> str:
    relationship = resource.get("relationships", {}).get(name)
    if relationship is None:
        return f"{name}=<no relationship>"
    if "data" not in relationship:
        return f"{name}=<no linkage>"
    linkage = relationship["data"]
    return f"{name}={linkage['id'] if linkage else None}"


def section(title: str) -> None:
    print(f"\n=== {title}")


def main() -> None:
    day = sys.argv[1] if len(sys.argv) > 1 else "2026-11-10"
    settings = load_settings()
    if settings.productive_api_token is None or not settings.productive_org_id:
        sys.exit("Set PRODUCTIVE_API_TOKEN and PRODUCTIVE_ORG_ID first.")
    client = ProductiveClient(settings.productive_api_token, settings.productive_org_id)

    section("People (question 4)")
    people = resources(client.fetch_all(PEOPLE))
    names = {}
    for p in people:
        a = p.get("attributes", {})
        names[p["id"]] = f"{a.get('first_name')} {a.get('last_name')}"
        print(
            p["id"],
            names[p["id"]],
            attrs(p, "is_user", "virtual", "placeholder", "agent", "contact", "person_type"),
            attrs(p, "sample_data", "deactivated_at", "archived_at"),
            rel(p, "company"),
        )

    section("Projects")
    for p in resources(client.fetch_all(PROJECTS)):
        print(
            p["id"],
            attrs(p, "name", "project_type_id", "sample_data", "archived_at"),
            rel(p, "company"),
        )

    section("Deals and budgets (question 2)")
    for d in resources(client.fetch_all(DEALS)):
        print(
            d["id"],
            attrs(d, "name", "budget", "type", "sample_data", "closed_at"),
            attrs(d, "budgeted_time", "worked_time", "billable_time", "budget_total"),
            rel(d, "project"),
            rel(d, "company"),
        )

    section("Services (question 2)")
    for s in resources(client.fetch_all(SERVICES)):
        print(
            s["id"],
            attrs(s, "name", "billable", "billing_type_id", "unit_id"),
            attrs(s, "budgeted_time", "estimated_time", "worked_time", "billable_time"),
            rel(s, "deal"),
            rel(s, "person"),
        )

    section("Linkage without include (question 3)")
    plain = client.fetch_all(Request("services", {}))
    for s in resources(plain)[:2]:
        print(s["id"], rel(s, "deal"), rel(s, "person"))
    plain = client.fetch_all(Request("time_entries", {"filter[date]": day}))
    for e in resources(plain)[:2]:
        print(e["id"], rel(e, "person"), rel(e, "service"))

    section(f"Time entries on {day} (questions 1, 2, 5)")
    variants = {
        "default": {},
        "with_draft=true": {"filter[with_draft]": "true"},
        "only_draft=true": {"filter[only_draft]": "true"},
    }
    for label, extra in variants.items():
        params = {"filter[date][gt_eq]": day, "filter[date][lt_eq]": day, **extra}
        entries = resources(client.fetch_all(Request("time_entries", params)))
        print(f"\n-- {label}: {len(entries)} entries")
        for e in entries:
            person = e.get("relationships", {}).get("person", {}).get("data") or {}
            print(
                e["id"],
                names.get(person.get("id"), person.get("id")),
                attrs(e, "date", "time", "billable_time", "invoiced", "note"),
                attrs(e, "status", "submitted", "approved", "rejected", "draft"),
                rel(e, "service"),
            )
    first = resources(client.fetch_all(Request("time_entries", {"filter[date]": day})))
    if first:
        print("\nAll attribute names of one entry:", sorted(first[0].get("attributes", {})))

    section("Pagination meta (question 6)")
    page = client.fetch_all(Request("people", {}))[0]
    print(json.dumps(page.get("meta"), indent=1), json.dumps(page.get("links"), indent=1))


if __name__ == "__main__":
    main()
