"""Settings: secrets from environment / `.env`, thresholds from `config.yaml`."""

from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from studio_pulse import StudioPulseError


class Settings(BaseSettings):
    """All runtime configuration. Priority: init kwargs > env > .env > config.yaml > defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        yaml_file="config.yaml",
        yaml_file_encoding="utf-8",
        extra="ignore",
    )

    # Secrets and environment (.env)
    data_source: Literal["mock", "productive"] = "mock"
    productive_api_token: SecretStr | None = None
    productive_org_id: str | None = None
    teams_webhook_url: SecretStr | None = None
    discord_webhook_url: SecretStr | None = None

    # Thresholds (config.yaml)
    min_hours_per_day: float = Field(default=6, ge=0, le=24)
    budget_warning_percent: float = Field(default=80, gt=0, le=100)
    low_billable_percent: float = Field(default=70, ge=0, le=100)
    timezone: str = "Europe/Bratislava"

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown time zone {value!r}") from None
        return value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return init_settings, env_settings, dotenv_settings, YamlConfigSettingsSource(settings_cls)


def load_settings() -> Settings:
    """Load settings from the current working directory, raising a readable error on bad values."""
    try:
        return Settings()
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise StudioPulseError(f"Invalid configuration: {problems}") from None
