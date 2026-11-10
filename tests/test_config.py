from pathlib import Path

import pytest

from studio_pulse import StudioPulseError
from studio_pulse.config import load_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_repository_config_loads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    settings = load_settings()

    assert settings.data_source == "mock"
    assert settings.min_hours_per_day == 6
    assert settings.budget_warning_percent == 80
    assert settings.low_billable_percent == 70
    assert settings.timezone == "Europe/Bratislava"


def test_defaults_without_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    settings = load_settings()

    assert settings.data_source == "mock"
    assert settings.productive_api_token is None
    assert settings.teams_webhook_url is None


def test_yaml_overrides_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("min_hours_per_day: 7.5\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert load_settings().min_hours_per_day == 7.5


def test_env_file_and_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "DATA_SOURCE=productive  # comment\n"
        "PRODUCTIVE_API_TOKEN=secret-token\n"
        "TEAMS_WEBHOOK_URL=\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PRODUCTIVE_ORG_ID", "123")

    settings = load_settings()

    assert settings.data_source == "productive"
    assert settings.productive_org_id == "123"
    assert settings.productive_api_token is not None
    assert settings.productive_api_token.get_secret_value() == "secret-token"
    assert "secret-token" not in repr(settings)
    assert settings.teams_webhook_url is None


def test_invalid_value_raises_readable_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_SOURCE", "excel")

    with pytest.raises(StudioPulseError, match="data_source"):
        load_settings()


def test_unknown_timezone_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("timezone: Europe/Atlantis\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(StudioPulseError, match="timezone"):
        load_settings()
