"""The interface every delivery channel implements, and the shared webhook POST."""

import logging
import time
from typing import Any, Protocol

import httpx
from pydantic import SecretStr

from studio_pulse import StudioPulseError
from studio_pulse.models import ReportData

logger = logging.getLogger(__name__)
# httpx logs every request URL at INFO level, and webhook URLs contain their secret token.
logging.getLogger("httpx").setLevel(logging.WARNING)

TIMEOUT_SECONDS = 15.0
MAX_ATTEMPTS = 4  # first try plus three retries on HTTP 429
MAX_RETRY_WAIT_SECONDS = 30.0


class Delivery(Protocol):
    name: str

    def send(self, report: ReportData) -> None: ...


def post_json(url: SecretStr, payload: dict[str, Any], channel: str) -> None:
    """POST `payload` as JSON; retry on 429. Errors never contain the (secret) webhook URL."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
                response = client.post(url.get_secret_value(), json=payload)
        except httpx.HTTPError as exc:
            raise StudioPulseError(
                f"Could not reach the {channel} webhook ({type(exc).__name__})."
            ) from None
        if response.status_code == 429 and attempt < MAX_ATTEMPTS:
            wait = _retry_after(response, attempt)
            logger.warning("%s webhook rate limited, retrying in %.1f s", channel, wait)
            time.sleep(wait)
            continue
        if response.is_success:
            logger.info("Report sent to %s", channel)
            return
        raise StudioPulseError(
            f"The {channel} webhook returned HTTP {response.status_code}: "
            f"{response.text[:200].strip()}"
        )


def _retry_after(response: httpx.Response, attempt: int) -> float:
    """Seconds to wait: the `Retry-After` header when valid, else exponential backoff."""
    try:
        wait = float(response.headers["Retry-After"])
    except (KeyError, ValueError):
        wait = 2.0 ** (attempt - 1)
    return min(max(wait, 0.0), MAX_RETRY_WAIT_SECONDS)
