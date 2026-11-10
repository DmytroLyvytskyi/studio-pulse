"""Console delivery: prints the report, optionally with the Teams and Discord JSON payloads."""

import json

from studio_pulse.delivery import discord, teams, text
from studio_pulse.models import ReportData


class ConsoleDelivery:
    name = "console"

    def __init__(self, show_payload: bool = False) -> None:
        self.show_payload = show_payload

    def send(self, report: ReportData) -> None:
        print(text.plain_text(report))
        if self.show_payload:
            for label, payload in (
                ("Teams payload", teams.build_payload(report)),
                ("Discord payload", discord.build_payload(report)),
            ):
                print(f"\n--- {label} ---")
                print(json.dumps(payload, ensure_ascii=False, indent=2))
