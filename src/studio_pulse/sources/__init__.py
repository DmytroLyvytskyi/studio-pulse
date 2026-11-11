"""Data sources and the factory selecting one from settings."""

from studio_pulse import StudioPulseError
from studio_pulse.config import Settings
from studio_pulse.dates import today_in
from studio_pulse.sources.base import DataSource
from studio_pulse.sources.mock import MockSource
from studio_pulse.sources.productive import ProductiveClient, ProductiveSource

__all__ = ["DataSource", "MockSource", "ProductiveSource", "get_source"]


def get_source(settings: Settings) -> DataSource:
    """Return the implementation selected by `DATA_SOURCE`."""
    if settings.data_source == "mock":
        return MockSource(today=today_in(settings.timezone))
    if settings.productive_api_token is None or not settings.productive_org_id:
        raise StudioPulseError(
            "DATA_SOURCE=productive needs PRODUCTIVE_API_TOKEN and PRODUCTIVE_ORG_ID "
            "(environment or .env)."
        )
    return ProductiveSource(
        ProductiveClient(settings.productive_api_token, settings.productive_org_id)
    )
