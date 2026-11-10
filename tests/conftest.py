import pytest

ENV_VARS = (
    "DATA_SOURCE",
    "PRODUCTIVE_API_TOKEN",
    "PRODUCTIVE_ORG_ID",
    "TEAMS_WEBHOOK_URL",
    "DISCORD_WEBHOOK_URL",
)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests independent of the developer's shell environment."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
