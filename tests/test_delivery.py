import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import SecretStr

from studio_pulse import StudioPulseError
from studio_pulse.analyze import build_report
from studio_pulse.config import Settings
from studio_pulse.delivery import (
    ConsoleDelivery,
    Delivery,
    DiscordDelivery,
    TeamsDelivery,
    base,
    discord,
    teams,
    text,
)
from studio_pulse.models import BudgetStatus, MissingLog, ReportData
from studio_pulse.sources import MockSource

MONDAY = date(2026, 11, 9)
TUESDAY = date(2026, 11, 10)
SNAPSHOT = Path(__file__).parent / "snapshots" / "teams_card_mock.json"
TEAMS_URL = "https://example.invalid/workflows/secret-teams-token"
DISCORD_URL = "https://example.invalid/api/webhooks/1/secret-discord-token"

SPEC_EXAMPLE = """\
Studio Pulse · utorok 10. 11.

Včera: 46,5 h, z toho 82 % fakturovateľných.

Rozpočty
• Rezidencia Hradná, vizualizácie: 112 % (prekročené o 15,6 h)
• Lesná Brána, VR prehliadka: 91 % (zostáva 16,2 h)

Chýbajúce logy
• Jana Nováková: 0 h
• Martin Horák: 3,5 h

Bez poznámky: Peter Kováč (3 záznamy)"""


@pytest.fixture
def mock_report() -> ReportData:
    settings = Settings(min_hours_per_day=6, budget_warning_percent=80, low_billable_percent=70)
    return build_report(MockSource(today=TUESDAY), MONDAY, settings, today=TUESDAY)


def report(**changes: object) -> ReportData:
    """A report with no alerts unless `changes` add some."""
    data: dict[str, object] = {
        "day": MONDAY,
        "generated_on": TUESDAY,
        "total_hours": 60.0,
        "billable_percent": 85.0,
        "low_billable_percent": 70.0,
        "billable_low": False,
        "hours_by_project": [],
        "budget_alerts": [],
        "missing_logs": [],
        "entries_without_note": {},
    }
    return ReportData.model_validate(data | changes)


def budget(status: str, used_percent: float | None, remaining: float) -> BudgetStatus:
    return BudgetStatus.model_validate(
        {
            "project": "Projekt",
            "client": "Klient",
            "budget_hours": 10.0,
            "used_hours": 10.0 - remaining,
            "used_percent": used_percent,
            "remaining_hours": remaining,
            "status": status,
        }
    )


# Slovak formatting


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.0, "0"), (3.5, "3,5"), (46.5, "46,5"), (8.0, "8"), (112.0, "112"), (0.1, "0,1")],
)
def test_number_uses_decimal_comma(value: float, expected: str) -> None:
    assert text.number(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"), [(81.7, "82 %"), (81.5, "82 %"), (80.4, "80 %"), (0.0, "0 %")]
)
def test_percent_is_whole_and_rounded_half_up(value: float, expected: str) -> None:
    assert text.percent(value) == expected


@pytest.mark.parametrize(
    ("count", "expected"),
    [(1, "1 záznam"), (2, "2 záznamy"), (4, "4 záznamy"), (5, "5 záznamov"), (11, "11 záznamov")],
)
def test_records_plural(count: int, expected: str) -> None:
    assert text.records(count) == expected


def test_day_label_yesterday_or_named_day() -> None:
    assert text.day_label(report(day=MONDAY, generated_on=TUESDAY)) == "Včera"
    friday = date(2026, 11, 6)
    assert text.day_label(report(day=friday, generated_on=MONDAY)) == "Piatok 6. 11."
    assert text.day_label(report(day=MONDAY, generated_on=MONDAY)) == "Pondelok 9. 11."


def test_title_names_the_day_the_report_is_produced() -> None:
    assert text.title(report(generated_on=date(2026, 11, 15))) == "Studio Pulse · nedeľa 15. 11."


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (budget("over", 112.0, -15.6), "Projekt: 112 % (prekročené o 15,6 h)"),
        (budget("warning", 80.0, 2.0), "Projekt: 80 % (zostáva 2 h)"),
        (budget("warning", 100.0, 0.0), "Projekt: 100 % (zostáva 0 h)"),
        (budget("over", None, -2.0), "Projekt: prekročené o 2 h"),  # zero budget
    ],
)
def test_budget_line(status: BudgetStatus, expected: str) -> None:
    assert text.budget_line(status) == expected


def test_plain_text_matches_the_spec_example(mock_report: ReportData) -> None:
    assert text.plain_text(mock_report) == SPEC_EXAMPLE


def test_plain_text_without_alerts() -> None:
    assert text.plain_text(report()) == (
        "Studio Pulse · utorok 10. 11.\n\n"
        "Včera: 60 h, z toho 85 % fakturovateľných.\n\n"
        "Všetko v poriadku, žiadne upozornenia."
    )


def test_low_billable_is_highlighted_and_counts_as_alert() -> None:
    low = report(billable_percent=55.0, billable_low=True)
    assert text.summary(low) == "Včera: 60 h, z toho 55 % fakturovateľných. Pod cieľom 70 %."
    assert text.has_alerts(low)
    assert text.ALL_OK not in text.plain_text(low)


def test_several_people_without_note() -> None:
    notes = report(entries_without_note={"Peter Kováč": 3, "Jana Nováková": 1})
    assert text.without_note(notes) == "Peter Kováč (3 záznamy), Jana Nováková (1 záznam)"


# Teams


def test_adaptive_card_matches_snapshot(mock_report: ReportData) -> None:
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert teams.build_adaptive_card(mock_report) == expected


def test_teams_payload_wraps_the_card(mock_report: ReportData) -> None:
    payload = teams.build_payload(mock_report)
    [attachment] = payload["attachments"]
    assert payload["type"] == "message"
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert attachment["content"] == teams.build_adaptive_card(mock_report)


def test_adaptive_card_without_alerts() -> None:
    card = teams.build_adaptive_card(report())
    assert card["version"] == "1.4"
    assert [block.get("text") for block in card["body"]] == [
        "Studio Pulse · utorok 10. 11.",
        "Včera: 60 h, z toho 85 % fakturovateľných.",
        text.ALL_OK,
    ]
    assert card["body"][2]["color"] == "Good"


def test_adaptive_card_marks_low_billable() -> None:
    card = teams.build_adaptive_card(report(billable_percent=55.0, billable_low=True))
    assert card["body"][1]["color"] == "Warning"
    assert text.ALL_OK not in json.dumps(card, ensure_ascii=False)


# Discord


def test_discord_payload(mock_report: ReportData) -> None:
    payload = discord.build_payload(mock_report)
    [embed] = payload["embeds"]
    assert payload["content"] == "Studio Pulse · utorok 10. 11."
    assert embed["description"] == "Včera: 46,5 h, z toho 82 % fakturovateľných."
    assert embed["color"] == discord.RED
    assert [(f["name"], f["value"]) for f in embed["fields"]] == [
        (
            "Rozpočty",
            "• Rezidencia Hradná, vizualizácie: 112 % (prekročené o 15,6 h)\n"
            "• Lesná Brána, VR prehliadka: 91 % (zostáva 16,2 h)",
        ),
        ("Chýbajúce logy", "• Jana Nováková: 0 h\n• Martin Horák: 3,5 h"),
        ("Bez poznámky", "• Peter Kováč (3 záznamy)"),
    ]


@pytest.mark.parametrize(
    ("changes", "color"),
    [
        ({}, discord.GREEN),
        ({"budget_alerts": [budget("warning", 90.0, 1.0)]}, discord.AMBER),
        (
            {"missing_logs": [MissingLog(person="X", logged_hours=0, expected_hours=6)]},
            discord.AMBER,
        ),
        ({"billable_percent": 50.0, "billable_low": True}, discord.AMBER),
        (
            {"budget_alerts": [budget("over", 120.0, -2.0), budget("warning", 90.0, 1.0)]},
            discord.RED,
        ),
    ],
)
def test_discord_color_by_worst_status(changes: dict[str, object], color: int) -> None:
    assert discord.embed_color(report(**changes)) == color


def test_discord_without_alerts_says_all_ok() -> None:
    [embed] = discord.build_payload(report())["embeds"]
    assert embed["fields"] == []
    assert embed["description"].endswith(text.ALL_OK)


def test_discord_field_is_truncated_to_the_limit() -> None:
    people = [
        MissingLog(person=f"Osoba {i:03d}", logged_hours=0, expected_hours=6) for i in range(200)
    ]
    [embed] = discord.build_payload(report(missing_logs=people))["embeds"]
    value = embed["fields"][0]["value"]
    assert len(value) == discord.FIELD_VALUE_LIMIT
    assert value.endswith("…")


# Console


def test_console_prints_text(mock_report: ReportData, capsys: pytest.CaptureFixture[str]) -> None:
    ConsoleDelivery().send(mock_report)
    assert capsys.readouterr().out == SPEC_EXAMPLE + "\n"


def test_console_shows_payloads(
    mock_report: ReportData, capsys: pytest.CaptureFixture[str]
) -> None:
    ConsoleDelivery(show_payload=True).send(mock_report)
    out = capsys.readouterr().out
    assert out.startswith(SPEC_EXAMPLE)
    assert "--- Teams payload ---" in out
    assert "--- Discord payload ---" in out
    assert '"Otvoriť Productive"' in out  # JSON keeps Slovak characters readable


def test_adapters_implement_the_protocol() -> None:
    adapters: list[Delivery] = [
        ConsoleDelivery(),
        TeamsDelivery(SecretStr(TEAMS_URL)),
        DiscordDelivery(SecretStr(DISCORD_URL)),
    ]
    assert [adapter.name for adapter in adapters] == ["console", "teams", "discord"]


# Sending (respx; no real network calls)


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waits: list[float] = []
    monkeypatch.setattr(base.time, "sleep", waits.append)
    return waits


@respx.mock
def test_teams_send_posts_the_payload(mock_report: ReportData) -> None:
    route = respx.post(TEAMS_URL).mock(return_value=httpx.Response(202))
    TeamsDelivery(SecretStr(TEAMS_URL)).send(mock_report)
    assert route.call_count == 1
    assert json.loads(route.calls[0].request.content) == teams.build_payload(mock_report)


@respx.mock
def test_discord_send_posts_the_payload(mock_report: ReportData) -> None:
    route = respx.post(DISCORD_URL).mock(return_value=httpx.Response(204))
    DiscordDelivery(SecretStr(DISCORD_URL)).send(mock_report)
    assert json.loads(route.calls[0].request.content) == discord.build_payload(mock_report)


@respx.mock
def test_retry_after_429_then_success(mock_report: ReportData, sleeps: list[float]) -> None:
    route = respx.post(DISCORD_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1.5"}),
            httpx.Response(429),  # no header: exponential backoff
            httpx.Response(204),
        ]
    )
    DiscordDelivery(SecretStr(DISCORD_URL)).send(mock_report)
    assert route.call_count == 3
    assert sleeps == [1.5, 2.0]


@respx.mock
def test_retry_wait_is_capped(mock_report: ReportData, sleeps: list[float]) -> None:
    respx.post(TEAMS_URL).mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "3600"}), httpx.Response(202)]
    )
    TeamsDelivery(SecretStr(TEAMS_URL)).send(mock_report)
    assert sleeps == [base.MAX_RETRY_WAIT_SECONDS]


@respx.mock
def test_gives_up_after_repeated_429(mock_report: ReportData, sleeps: list[float]) -> None:
    route = respx.post(TEAMS_URL).mock(return_value=httpx.Response(429, text="slow down"))
    with pytest.raises(StudioPulseError, match="Teams webhook returned HTTP 429"):
        TeamsDelivery(SecretStr(TEAMS_URL)).send(mock_report)
    assert route.call_count == base.MAX_ATTEMPTS
    assert len(sleeps) == base.MAX_ATTEMPTS - 1


@respx.mock
def test_http_error_message_hides_the_url(
    mock_report: ReportData, caplog: pytest.LogCaptureFixture
) -> None:
    respx.post(DISCORD_URL).mock(return_value=httpx.Response(400, json={"message": "Bad embed"}))
    with caplog.at_level("DEBUG"), pytest.raises(StudioPulseError) as error:
        DiscordDelivery(SecretStr(DISCORD_URL)).send(mock_report)
    assert "HTTP 400" in str(error.value)
    assert "Bad embed" in str(error.value)
    assert "secret" not in str(error.value)
    assert "secret" not in caplog.text


@respx.mock
def test_network_error_message_hides_the_url(mock_report: ReportData) -> None:
    respx.post(TEAMS_URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(
        StudioPulseError, match=r"Could not reach the Teams webhook \(ConnectError\)"
    ) as error:
        TeamsDelivery(SecretStr(TEAMS_URL)).send(mock_report)
    assert error.value.__cause__ is None
    assert "secret" not in str(error.value)
