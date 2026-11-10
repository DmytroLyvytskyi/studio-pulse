import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner, Result

from studio_pulse import report, sources

MONDAY = date(2026, 11, 9)
TUESDAY = date(2026, 11, 10)
TEAMS_URL = "https://example.invalid/workflows/secret-teams-token"
DISCORD_URL = "https://example.invalid/api/webhooks/1/secret-discord-token"

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No developer .env or config.yaml, and a fixed "today" (Tuesday) for the mock data."""
    monkeypatch.chdir(tmp_path)
    set_today(monkeypatch, TUESDAY)


def set_today(monkeypatch: pytest.MonkeyPatch, today: date) -> None:
    monkeypatch.setattr(report, "today_in", lambda _tz: today)
    monkeypatch.setattr(sources, "today_in", lambda _tz: today)


def run(*args: str) -> Result:
    return runner.invoke(report.app, ["report", *args])


@pytest.fixture
def webhooks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", TEAMS_URL)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", DISCORD_URL)


# Reported day


def test_dry_run_reports_previous_working_day() -> None:
    result = run("--dry-run")

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("Studio Pulse · utorok 10. 11.\n\nVčera: 46,5 h")
    assert "Rezidencia Hradná, vizualizácie: 112 %" in result.stdout


def test_on_monday_the_default_is_friday(monkeypatch: pytest.MonkeyPatch) -> None:
    set_today(monkeypatch, MONDAY)

    result = run("--dry-run")

    assert result.exit_code == 0, result.output
    assert "Piatok 6. 11.:" in result.stdout


@pytest.mark.parametrize(
    ("value", "label"), [("2026-11-06", "Piatok 6. 11.:"), ("yesterday", "Včera:")]
)
def test_date_option(value: str, label: str) -> None:
    result = run("--dry-run", "--date", value)

    assert result.exit_code == 0, result.output
    assert label in result.stdout


@pytest.mark.parametrize(
    ("value", "message"),
    [("2026-11-11", "is in the future"), ("next week", "Invalid date 'next week'")],
)
def test_invalid_date(value: str, message: str) -> None:
    result = run("--dry-run", "--date", value)

    assert result.exit_code == 1
    assert message in result.stderr
    assert "Traceback" not in result.output


# Channels


def test_no_channel_configured_is_an_error() -> None:
    result = run()

    assert result.exit_code == 1
    assert "No delivery channel is configured" in result.stderr


@respx.mock
def test_sends_to_every_configured_channel(webhooks: None) -> None:
    teams = respx.post(TEAMS_URL).mock(return_value=httpx.Response(202))
    discord = respx.post(DISCORD_URL).mock(return_value=httpx.Response(204))

    result = run()

    assert result.exit_code == 0, result.output
    assert teams.call_count == 1
    assert discord.call_count == 1
    assert result.stdout == ""
    card = json.loads(teams.calls.last.request.content)["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard"
    assert json.loads(discord.calls.last.request.content)["content"].startswith("Studio Pulse")


@respx.mock
def test_only_configured_channels_are_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", DISCORD_URL)
    discord = respx.post(DISCORD_URL).mock(return_value=httpx.Response(204))

    result = run()

    assert result.exit_code == 0, result.output
    assert discord.call_count == 1  # respx would fail any other (Teams) request


@respx.mock
def test_channel_option_selects_one_channel(webhooks: None) -> None:
    teams = respx.post(TEAMS_URL).mock(return_value=httpx.Response(202))
    discord = respx.post(DISCORD_URL).mock(return_value=httpx.Response(204))

    result = run("--channel", "discord")

    assert result.exit_code == 0, result.output
    assert teams.call_count == 0
    assert discord.call_count == 1


def test_channel_option_requires_its_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", DISCORD_URL)

    result = run("--channel", "teams")

    assert result.exit_code == 1
    assert "Channel teams is not configured: set TEAMS_WEBHOOK_URL." in result.stderr


def test_unknown_channel_is_rejected() -> None:
    result = run("--channel", "slack")

    assert result.exit_code == 2


@respx.mock
def test_dry_run_sends_nothing(webhooks: None) -> None:
    route = respx.route().mock(return_value=httpx.Response(204))

    result = run("--dry-run")

    assert result.exit_code == 0, result.output
    assert route.call_count == 0
    assert "Studio Pulse" in result.stdout


def test_show_payload_prints_json() -> None:
    result = run("--dry-run", "--show-payload")

    assert result.exit_code == 0, result.output
    assert "--- Teams payload ---" in result.stdout
    assert '"type": "AdaptiveCard"' in result.stdout
    assert "--- Discord payload ---" in result.stdout
    assert '"embeds"' in result.stdout


@respx.mock
def test_show_payload_without_dry_run_prints_and_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", DISCORD_URL)
    discord = respx.post(DISCORD_URL).mock(return_value=httpx.Response(204))

    result = run("--show-payload")

    assert result.exit_code == 0, result.output
    assert discord.call_count == 1
    assert "--- Discord payload ---" in result.stdout


@respx.mock
def test_failing_channel_does_not_stop_the_others(webhooks: None) -> None:
    respx.post(TEAMS_URL).mock(return_value=httpx.Response(400, text="Bad card"))
    discord = respx.post(DISCORD_URL).mock(return_value=httpx.Response(204))

    result = run()

    assert result.exit_code == 1
    assert discord.call_count == 1
    assert "teams: The Teams webhook returned HTTP 400: Bad card" in result.stderr
    assert "secret" not in result.output


# Configuration


def test_invalid_configuration_is_a_readable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_SOURCE", "excel")

    result = run("--dry-run")

    assert result.exit_code == 1
    assert "Invalid configuration: data_source" in result.stderr
    assert "Traceback" not in result.output


def test_webhook_urls_come_from_env_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(f"DISCORD_WEBHOOK_URL={DISCORD_URL}\n", encoding="utf-8")

    result = run("--channel", "teams")

    assert result.exit_code == 1
    assert "Channel teams is not configured" in result.stderr
