"""CLI `studio-pulse`: the daily report, delivered to the console, Microsoft Teams and Discord.

A webhook channel is enabled when its URL is set (`TEAMS_WEBHOOK_URL`, `DISCORD_WEBHOOK_URL`).
"""

import io
import logging
import sys
from datetime import date
from enum import StrEnum
from typing import Annotated, NoReturn

import typer

from studio_pulse import StudioPulseError
from studio_pulse.analyze import build_report
from studio_pulse.config import Settings, load_settings
from studio_pulse.dates import parse_date, previous_working_day, today_in
from studio_pulse.delivery import ConsoleDelivery, Delivery, DiscordDelivery, TeamsDelivery
from studio_pulse.sources import get_source

logger = logging.getLogger(__name__)

app = typer.Typer(help="Studio Pulse: time tracking and budget insights from Productive.")


class Channel(StrEnum):
    teams = "teams"
    discord = "discord"


ENV_VAR = {Channel.teams: "TEAMS_WEBHOOK_URL", Channel.discord: "DISCORD_WEBHOOK_URL"}


@app.callback()
def _cli() -> None:
    """Keep `report` as an explicit subcommand (Typer collapses a single-command app)."""


def report_day(value: str | None, today: date) -> date:
    """The reported day: `value` when given, otherwise the previous working day."""
    if value is None:
        return previous_working_day(today)
    day = parse_date(value, today)
    if day > today:
        raise StudioPulseError(f"Date {day.isoformat()} is in the future.")
    return day


def webhook_deliveries(settings: Settings, channel: Channel | None) -> list[Delivery]:
    """Teams and Discord deliveries whose webhook URL is set, optionally only `channel`."""
    configured: dict[Channel, Delivery] = {}
    if settings.teams_webhook_url:
        configured[Channel.teams] = TeamsDelivery(settings.teams_webhook_url)
    if settings.discord_webhook_url:
        configured[Channel.discord] = DiscordDelivery(settings.discord_webhook_url)

    if channel is not None:
        if channel not in configured:
            raise StudioPulseError(f"Channel {channel} is not configured: set {ENV_VAR[channel]}.")
        return [configured[channel]]
    if not configured:
        raise StudioPulseError(
            "No delivery channel is configured. Set TEAMS_WEBHOOK_URL or DISCORD_WEBHOOK_URL "
            "in .env, or use --dry-run to print the report."
        )
    return list(configured.values())


@app.command()
def report(
    date_: Annotated[
        str | None,
        typer.Option(
            "--date",
            help="Day to report: YYYY-MM-DD, 'today' or 'yesterday'. "
            "Default: the previous working day.",
            show_default=False,
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the report to the console, send nothing.")
    ] = False,
    show_payload: Annotated[
        bool, typer.Option("--show-payload", help="Also print the Teams and Discord JSON.")
    ] = False,
    channel: Annotated[
        Channel | None,
        typer.Option("--channel", help="Send to this channel only.", show_default=False),
    ] = None,
) -> None:
    """Build the daily report and post it to every configured channel."""
    try:
        settings = load_settings()
        today = today_in(settings.timezone)
        day = report_day(date_, today)
        targets: list[Delivery] = [] if dry_run else webhook_deliveries(settings, channel)
        if dry_run or show_payload:
            targets.insert(0, ConsoleDelivery(show_payload=show_payload))
        data = build_report(get_source(settings), day, settings, today)
    except StudioPulseError as exc:
        _fail(str(exc))

    # One failing channel does not stop the others.
    failures = []
    for delivery in targets:
        try:
            delivery.send(data)
        except StudioPulseError as exc:
            failures.append(f"{delivery.name}: {exc}")
    if failures:
        _fail("\n".join(failures))


def _fail(message: str) -> NoReturn:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=1)


def _utf8_output() -> None:
    """Slovak text must not crash on a Windows console or pipe with a legacy code page."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


def main() -> None:
    """Entry point `studio-pulse`."""
    _utf8_output()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app()


if __name__ == "__main__":
    main()
