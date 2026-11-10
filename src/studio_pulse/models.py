"""Data models. Durations are stored in minutes (int) and converted to hours only for output."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

ProjectKind = Literal["3D", "VR", "WEB", "CGI", "INTERNAL"]
BudgetState = Literal["ok", "warning", "over", "no_budget"]
GroupBy = Literal["project", "person", "client"]


# Source data


class Person(BaseModel):
    id: str
    name: str
    email: str | None = None
    active: bool = True


class Project(BaseModel):
    id: str
    name: str
    client: str
    kind: ProjectKind
    active: bool = True


class Budget(BaseModel):
    project_id: str
    budget_minutes: int | None = Field(ge=0)  # None = no limit


class TimeEntry(BaseModel):
    id: str
    date: date
    person_id: str
    project_id: str
    minutes: int = Field(ge=0)
    billable_minutes: int = Field(ge=0)
    note: str | None = None
    invoiced: bool = False


# Analysis results (serializable to JSON for MCP and the report)


class MissingLog(BaseModel):
    person: str
    logged_hours: float
    expected_hours: float


class BudgetStatus(BaseModel):
    project: str
    client: str
    budget_hours: float | None
    used_hours: float
    used_percent: float | None
    remaining_hours: float | None
    status: BudgetState


class ProjectHours(BaseModel):
    project: str
    client: str
    total_hours: float
    billable_hours: float


class GroupHours(BaseModel):
    name: str  # project, person or client name
    total_hours: float
    billable_hours: float
    billable_percent: float


class ReportData(BaseModel):
    day: date  # the reported day
    generated_on: date  # the day the report is produced ("Včera" when `day` is the day before)
    total_hours: float
    billable_percent: float
    low_billable_percent: float  # configured threshold
    billable_low: bool  # billable_percent below the threshold (and something was logged)
    hours_by_project: list[ProjectHours]
    budget_alerts: list[BudgetStatus]  # warning and over only
    missing_logs: list[MissingLog]
    entries_without_note: dict[str, int]  # person name -> count
