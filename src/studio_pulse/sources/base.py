"""The interface every data source implements. Sources are read only."""

from datetime import date
from typing import Protocol

from studio_pulse.models import Budget, Person, Project, TimeEntry


class DataSource(Protocol):
    def people(self) -> list[Person]: ...

    def projects(self) -> list[Project]: ...

    def budgets(self) -> list[Budget]: ...

    def time_entries(self, date_from: date, date_to: date) -> list[TimeEntry]:
        """Entries with `date_from <= date <= date_to` (inclusive)."""
        ...
