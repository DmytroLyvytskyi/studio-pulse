"""Data sources and the factory selecting one from settings."""

from studio_pulse import StudioPulseError
from studio_pulse.config import Settings
from studio_pulse.dates import today_in
from studio_pulse.sources.base import DataSource
from studio_pulse.sources.mock import MockSource

__all__ = ["DataSource", "MockSource", "get_source"]


def get_source(settings: Settings) -> DataSource:
    """Return the implementation selected by `DATA_SOURCE`."""
    if settings.data_source == "mock":
        return MockSource(today=today_in(settings.timezone))
    # TODO: Productive source is milestone 8 (after verifying the API docs).
    raise StudioPulseError("DATA_SOURCE=productive is not implemented yet. Use DATA_SOURCE=mock.")
