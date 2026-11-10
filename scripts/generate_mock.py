"""Generate deterministic mock data for Studio Pulse into `mock/`.

Run: uv run python scripts/generate_mock.py

Time entries store `working_days_ago` (1 = previous working day) instead of a date.
The mock source converts it to a real date relative to today, so the data never goes stale
and the previous working day always holds the built-in problem scenarios.

All people, companies and projects are fictional.
"""

import json
import random
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

SEED = 20261110
WORKING_DAYS = 10  # about the last 14 calendar days
INVOICED_FROM_DAYS_AGO = 6  # billable entries at least this old are already invoiced
DAILY_CAPACITY_MINUTES = (420, 450, 480, 510)
DAY_OFF_PROBABILITY = 0.03
MISSING_NOTE_PROBABILITY = 0.05
CHUNK_MINUTES = (60, 90, 120, 150, 180)
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "mock"

PEOPLE = [
    ("p1", "Jana Nováková"),
    ("p2", "Martin Horák"),
    ("p3", "Peter Kováč"),
    ("p4", "Lucia Szabová"),
    ("p5", "Tomáš Baláž"),
    ("p6", "Katarína Vargová"),
    ("p7", "Michal Polák"),
    ("p8", "Zuzana Kráľová"),
]

INTERNAL = "pr-rnd"
PROJECTS = [
    ("pr-lesna", "Lesná Brána, VR prehliadka", "Lesná Brána Development", "VR", 120),
    ("pr-hradna", "Rezidencia Hradná, vizualizácie", "Hradná Invest", "3D", 80),
    ("pr-riverside", "Riverside Lofts, web", "Riverside Lofts", "WEB", 200),
    ("pr-hr", "HR kampaň, video", "Nordica HR", "CGI", 60),
    (INTERNAL, "Interné R&D", "Studio", "INTERNAL", None),
]

# Total minutes logged per client project over all mock days (budget scenarios).
TARGET_MINUTES = {
    "pr-lesna": 6552,  # 109.2 h of 120 h = 91 %, warning (10.8 h left)
    "pr-hradna": 5376,  # 89.6 h of 80 h = 112 %, over (by 9.6 h)
    "pr-riverside": 6480,  # 108 h of 200 h = 54 %, ok
    "pr-hr": 1080,  # 18 h of 60 h = 30 %, ok
}

TEAMS = {
    "pr-lesna": ["p1", "p3", "p4", "p7"],
    "pr-hradna": ["p3", "p5", "p7", "p8"],
    "pr-riverside": ["p1", "p2", "p6"],
    "pr-hr": ["p5", "p8"],
}

NOTES = {
    "pr-lesna": [
        "Modelovanie exteriéru",
        "Textúry a materiály",
        "Nastavenie VR scén",
        "Testovanie prehliadky v headsete",
    ],
    "pr-hradna": [
        "Modelovanie interiéru",
        "Rendering obývačky",
        "Postprodukcia vizualizácií",
        "Úpravy podľa pripomienok klienta",
    ],
    "pr-riverside": [
        "Frontend, galéria bytov",
        "Responzívny layout",
        "Napojenie na CMS",
        "Opravy po review",
    ],
    "pr-hr": ["Storyboard", "Animácia úvodnej scény", "Kompozícia"],
    INTERNAL: [
        "Testovanie nového renderera",
        "Interná porada",
        "Školenie Unreal Engine",
        "Príprava knižnice assetov",
    ],
}

# Previous working day: hand-written problem scenarios.
# Total 46.5 h, of which 38 h billable (82 %).
# (person_id, project_id, minutes, note); note None = entry without a note.
PREVIOUS_DAY = [
    # p1 Jana Nováková: nothing logged
    ("p2", "pr-riverside", 210, "Responzívny layout"),  # Martin Horák: only 3.5 h
    ("p3", "pr-hradna", 180, None),  # Peter Kováč: three entries without a note
    ("p3", "pr-hradna", 150, None),
    ("p3", "pr-lesna", 150, None),
    ("p4", "pr-lesna", 300, "Nastavenie VR scén"),
    ("p4", INTERNAL, 150, "Interná porada"),
    ("p5", "pr-hradna", 240, "Rendering obývačky"),
    ("p5", "pr-hr", 240, "Animácia úvodnej scény"),
    ("p6", "pr-riverside", 300, "Napojenie na CMS"),
    ("p6", INTERNAL, 120, "Interná porada"),
    ("p7", "pr-lesna", 240, "Textúry a materiály"),
    ("p7", "pr-hradna", 150, "Postprodukcia vizualizácií"),
    ("p8", "pr-hr", 120, "Kompozícia"),
    ("p8", INTERNAL, 240, "Testovanie nového renderera"),  # Zuzana: exactly 6 h, the threshold
]

Row = tuple[int, str, str, int, str | None]  # working_days_ago, person, project, minutes, note


def _email(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return ascii_name.lower().replace(" ", ".") + "@studio-pulse.example"


def _older_day_rows(rng: random.Random, days_ago: int, quotas: dict[str, int]) -> list[Row]:
    """Spread each project's quota for one day over its team, fill the rest with internal work."""
    capacity = {
        person_id: 0 if rng.random() < DAY_OFF_PROBABILITY else rng.choice(DAILY_CAPACITY_MINUTES)
        for person_id, _ in PEOPLE
    }
    allocated: dict[tuple[str, str], int] = defaultdict(int)
    for project_id, quota in quotas.items():
        while quota > 0:
            available = [p for p in TEAMS[project_id] if capacity[p] > 0]
            if not available:
                raise RuntimeError(f"Team of {project_id} has no capacity left on day {days_ago}")
            person_id = rng.choice(available)
            chunk = min(rng.choice(CHUNK_MINUTES), quota, capacity[person_id])
            allocated[person_id, project_id] += chunk
            capacity[person_id] -= chunk
            quota -= chunk

    rows: list[Row] = []
    for person_id, _ in PEOPLE:
        for project_id in TEAMS:
            minutes = allocated.get((person_id, project_id), 0)
            if minutes:
                rows.append((days_ago, person_id, project_id, minutes, _note(rng, project_id)))
        if capacity[person_id] >= 30:
            rows.append((days_ago, person_id, INTERNAL, capacity[person_id], _note(rng, INTERNAL)))
    return rows


def _note(rng: random.Random, project_id: str) -> str | None:
    if rng.random() < MISSING_NOTE_PROBABILITY:
        return None
    return rng.choice(NOTES[project_id])


def _time_entry_rows(rng: random.Random) -> list[Row]:
    rows: list[Row] = [(1, *row) for row in PREVIOUS_DAY]
    remaining = dict(TARGET_MINUTES)
    for _, project_id, minutes, _ in PREVIOUS_DAY:
        if project_id in remaining:
            remaining[project_id] -= minutes

    older_days = list(range(WORKING_DAYS, 1, -1))  # oldest first
    for index, days_ago in enumerate(older_days):
        days_left = len(older_days) - index
        quotas = {project_id: left // days_left for project_id, left in remaining.items()}
        if days_left == 1:
            quotas = dict(remaining)
        for project_id, quota in quotas.items():
            remaining[project_id] -= quota
        rows.extend(_older_day_rows(rng, days_ago, quotas))
    return sorted(rows, key=lambda row: (-row[0], row[1], row[2]))


def generate(seed: int = SEED) -> dict[str, list[dict[str, Any]]]:
    """Build all mock files as JSON-ready data. Same seed -> identical output."""
    rng = random.Random(seed)
    kinds = {project_id: kind for project_id, _, _, kind, _ in PROJECTS}

    time_entries = []
    for number, (days_ago, person_id, project_id, minutes, note) in enumerate(
        _time_entry_rows(rng), start=1
    ):
        billable = 0 if kinds[project_id] == "INTERNAL" else minutes
        time_entries.append(
            {
                "id": f"te-{number:04d}",
                "working_days_ago": days_ago,
                "person_id": person_id,
                "project_id": project_id,
                "minutes": minutes,
                "billable_minutes": billable,
                "note": note,
                "invoiced": billable > 0 and days_ago >= INVOICED_FROM_DAYS_AGO,
            }
        )

    return {
        "people": [
            {"id": person_id, "name": name, "email": _email(name), "active": True}
            for person_id, name in PEOPLE
        ],
        "projects": [
            {"id": project_id, "name": name, "client": client, "kind": kind, "active": True}
            for project_id, name, client, kind, _ in PROJECTS
        ],
        "budgets": [
            {"project_id": project_id, "budget_minutes": None if hours is None else hours * 60}
            for project_id, _, _, _, hours in PROJECTS
        ],
        "time_entries": time_entries,
    }


def write(output_dir: Path = OUTPUT_DIR) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, records in generate().items():
        path = output_dir / f"{name}.json"
        text = json.dumps(records, ensure_ascii=False, indent=2) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"Wrote {len(records):>4} records to {path}")


if __name__ == "__main__":
    write()
