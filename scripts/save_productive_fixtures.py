"""Save raw Productive API responses as test fixtures. Read only.

Usage: uv run python scripts/save_productive_fixtures.py [YYYY-MM-DD]   (default: 2026-11-10)

Needs PRODUCTIVE_API_TOKEN and PRODUCTIVE_ORG_ID in the environment or .env. Sends exactly the
requests `ProductiveSource` sends (time entries from `HISTORY_START` up to the given day) and
writes each list to tests/fixtures/productive/<name>.json as a JSON array of response pages.

Every email address is replaced: a person's own email becomes
<first>.<last>@studio-pulse.example (lowercase ASCII); any other address becomes
redacted@studio-pulse.example. Review the files before committing them.
"""

import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

from studio_pulse.analyze import HISTORY_START
from studio_pulse.config import load_settings
from studio_pulse.sources.productive import (
    DEALS,
    PEOPLE,
    PROJECTS,
    SERVICES,
    Document,
    ProductiveClient,
    time_entries_request,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "productive"
EMAIL_DOMAIN = "studio-pulse.example"
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def ascii_slug(text: str) -> str:
    """Lowercase ASCII without diacritics or spaces, e.g. "Nováková" → "novakova"."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return "".join(char for char in folded.lower() if char.isalnum())


def person_emails(documents: list[Document]) -> dict[str, str]:
    """Original email → <first>.<last>@studio-pulse.example for every person resource."""
    replacements = {}
    for document in documents:
        for resource in [*document.get("data", []), *document.get("included", [])]:
            attributes = resource.get("attributes", {})
            if resource.get("type") != "people" or not attributes.get("email"):
                continue
            first = ascii_slug(attributes.get("first_name") or "")
            last = ascii_slug(attributes.get("last_name") or "")
            local = ".".join(part for part in (first, last) if part) or f"person{resource['id']}"
            replacements[attributes["email"]] = f"{local}@{EMAIL_DOMAIN}"
    return replacements


def scrub(value: Any, replacements: dict[str, str]) -> Any:
    """`value` with every email address replaced."""
    if isinstance(value, dict):
        return {key: scrub(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item, replacements) for item in value]
    if isinstance(value, str):
        return EMAIL_PATTERN.sub(
            lambda match: replacements.get(match.group(), f"redacted@{EMAIL_DOMAIN}"), value
        )
    return value


def main() -> None:
    day = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2026, 11, 10)
    settings = load_settings()
    if settings.productive_api_token is None or not settings.productive_org_id:
        sys.exit("Set PRODUCTIVE_API_TOKEN and PRODUCTIVE_ORG_ID first.")
    client = ProductiveClient(settings.productive_api_token, settings.productive_org_id)

    responses = {
        "people": client.fetch_all(PEOPLE),
        "projects": client.fetch_all(PROJECTS),
        "deals": client.fetch_all(DEALS),
        "services": client.fetch_all(SERVICES),
        "time_entries": client.fetch_all(time_entries_request(HISTORY_START, day)),
    }
    replacements: dict[str, str] = {}
    for documents in responses.values():
        replacements.update(person_emails(documents))

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    for name, documents in responses.items():
        path = FIXTURES_DIR / f"{name}.json"
        text = json.dumps(scrub(documents, replacements), indent=2, ensure_ascii=False)
        path.write_text(text + "\n", encoding="utf-8")
        count = sum(len(document.get("data", [])) for document in documents)
        print(
            f"{path.relative_to(FIXTURES_DIR.parents[2])}: {len(documents)} page(s), {count} items"
        )


if __name__ == "__main__":
    main()
