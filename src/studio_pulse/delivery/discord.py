"""Discord delivery through a channel webhook: `content` plus one embed."""

from typing import Any

from pydantic import SecretStr

from studio_pulse.delivery import text
from studio_pulse.delivery.base import post_json
from studio_pulse.models import ReportData

# Embed colours by the worst status in the report.
RED = 0xE74C3C  # a project over budget
AMBER = 0xF1C40F  # any other alert
GREEN = 0x2ECC71  # nothing to report
FIELD_VALUE_LIMIT = 1024  # Discord's limit for an embed field value


def _field(name: str, lines: list[str]) -> dict[str, Any]:
    value = "\n".join(text.BULLET + line for line in lines)
    if len(value) > FIELD_VALUE_LIMIT:
        value = value[: FIELD_VALUE_LIMIT - 1] + "…"
    return {"name": name, "value": value, "inline": False}


def embed_color(report: ReportData) -> int:
    if any(status.status == "over" for status in report.budget_alerts):
        return RED
    return AMBER if text.has_alerts(report) else GREEN


def build_payload(report: ReportData) -> dict[str, Any]:
    fields = []
    if report.budget_alerts:
        fields.append(_field(text.BUDGETS, text.budget_lines(report)))
    if report.missing_logs:
        fields.append(_field(text.MISSING_LOGS, text.missing_log_lines(report)))
    if note := text.without_note(report):
        fields.append(_field(text.WITHOUT_NOTE, [note]))
    description = text.summary(report)
    if not text.has_alerts(report):
        description += f"\n{text.ALL_OK}"
    return {
        "content": text.title(report),
        "embeds": [
            {
                "title": "Denný prehľad",
                "description": description,
                "color": embed_color(report),
                "fields": fields,
            }
        ],
    }


class DiscordDelivery:
    name = "discord"

    def __init__(self, webhook_url: SecretStr) -> None:
        self.webhook_url = webhook_url

    def send(self, report: ReportData) -> None:
        post_json(self.webhook_url, build_payload(report), "Discord")
