"""Microsoft Teams delivery through a Workflows webhook with an Adaptive Card (version 1.4).

The webhook comes from the Teams workflow "Post to a channel when a webhook request is received".
Office 365 Connectors incoming webhooks are retired and are not supported.
"""

from typing import Any

from pydantic import SecretStr

from studio_pulse.delivery import text
from studio_pulse.delivery.base import post_json
from studio_pulse.models import BudgetStatus, ReportData

PRODUCTIVE_URL = "https://app.productive.io"
BUDGET_COLORS = {"over": "Attention", "warning": "Warning"}


def _text_block(content: str, **options: Any) -> dict[str, Any]:
    return {"type": "TextBlock", "text": content, "wrap": True, **options}


def _section(heading: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    header = _text_block(heading, weight="Bolder", spacing="Medium", separator=True)
    return [header, *items]


def _budget_block(status: BudgetStatus) -> dict[str, Any]:
    return _text_block(
        text.BULLET + text.budget_line(status), color=BUDGET_COLORS.get(status.status, "Default")
    )


def build_adaptive_card(report: ReportData) -> dict[str, Any]:
    """The Adaptive Card for `report`; paste it into the Adaptive Cards Designer to preview."""
    summary_options = {"color": "Warning"} if report.billable_low else {}
    body: list[dict[str, Any]] = [
        _text_block(text.title(report), size="Large", weight="Bolder"),
        _text_block(text.summary(report), **summary_options),
    ]
    if not text.has_alerts(report):
        body.append(_text_block(text.ALL_OK, color="Good"))
    if report.budget_alerts:
        body += _section(text.BUDGETS, [_budget_block(s) for s in report.budget_alerts])
    if report.missing_logs:
        facts = [
            {"title": log.person, "value": text.hours(log.logged_hours)}
            for log in report.missing_logs
        ]
        body += _section(text.MISSING_LOGS, [{"type": "FactSet", "facts": facts}])
    if note := text.without_note(report):
        body += _section(text.WITHOUT_NOTE, [_text_block(note)])
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
        "actions": [
            {"type": "Action.OpenUrl", "title": "Otvoriť Productive", "url": PRODUCTIVE_URL}
        ],
    }


def build_payload(report: ReportData) -> dict[str, Any]:
    """The message a Workflows webhook expects: the card as one attachment."""
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": build_adaptive_card(report),
            }
        ],
    }


class TeamsDelivery:
    name = "teams"

    def __init__(self, webhook_url: SecretStr) -> None:
        self.webhook_url = webhook_url

    def send(self, report: ReportData) -> None:
        post_json(self.webhook_url, build_payload(report), "Teams")
