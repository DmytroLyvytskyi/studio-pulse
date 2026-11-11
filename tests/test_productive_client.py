"""HTTP behaviour of the Productive client: headers, pagination, retry and errors."""

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from studio_pulse import StudioPulseError
from studio_pulse.config import Settings
from studio_pulse.sources import ProductiveSource, get_source
from studio_pulse.sources.productive import BASE_URL, PEOPLE, ProductiveClient

PEOPLE_URL = f"{BASE_URL}people"
TOKEN = "secret-token-123"


def person(person_id: str, first: str, last: str) -> dict[str, Any]:
    return {
        "id": person_id,
        "type": "people",
        "attributes": {"first_name": first, "last_name": last, "is_user": True},
    }


def page(items: list[dict[str, Any]], number: int, total: int) -> dict[str, Any]:
    return {
        "data": items,
        "meta": {"current_page": number, "total_pages": total, "total_count": 3},
    }


@pytest.fixture
def waits() -> list[float]:
    return []


@pytest.fixture
def client(waits: list[float]) -> ProductiveClient:
    return ProductiveClient(SecretStr(TOKEN), "4242", sleep=waits.append)


@respx.mock
def test_sends_documented_headers(client: ProductiveClient) -> None:
    route = respx.get(PEOPLE_URL).respond(json=page([], 1, 1))
    client.fetch_all(PEOPLE)
    headers = route.calls.last.request.headers
    assert headers["X-Auth-Token"] == TOKEN
    assert headers["X-Organization-Id"] == "4242"
    assert headers["Content-Type"] == "application/vnd.api+json"


@respx.mock
def test_fetches_every_page(client: ProductiveClient) -> None:
    route = respx.get(PEOPLE_URL)
    route.side_effect = [
        httpx.Response(200, json=page([person("1", "Jana", "N"), person("2", "Peter", "K")], 1, 2)),
        httpx.Response(200, json=page([person("3", "Martin", "H")], 2, 2)),
    ]
    documents = client.fetch_all(PEOPLE)

    assert [item["id"] for document in documents for item in document["data"]] == ["1", "2", "3"]
    params = [call.request.url.params for call in route.calls]
    assert [p["page[number]"] for p in params] == ["1", "2"]
    assert {p["page[size]"] for p in params} == {"200"}


@respx.mock
def test_stops_when_meta_has_no_total_pages(client: ProductiveClient) -> None:
    route = respx.get(PEOPLE_URL).respond(json={"data": [person("1", "Jana", "N")]})
    client.fetch_all(PEOPLE)
    assert route.call_count == 1


@respx.mock
def test_retries_after_429_using_rate_limit_reset(
    client: ProductiveClient, waits: list[float]
) -> None:
    route = respx.get(PEOPLE_URL)
    route.side_effect = [
        httpx.Response(429, headers={"X-RateLimit-Reset": "3"}),
        httpx.Response(200, json=page([person("1", "Jana", "N")], 1, 1)),
    ]
    documents = client.fetch_all(PEOPLE)

    assert documents[0]["data"][0]["id"] == "1"
    assert route.call_count == 2
    assert waits == [3.0]


@respx.mock
def test_backs_off_exponentially_without_reset_header(
    client: ProductiveClient, waits: list[float]
) -> None:
    route = respx.get(PEOPLE_URL)
    route.side_effect = [
        httpx.Response(429),
        httpx.Response(429),
        httpx.Response(200, json=page([], 1, 1)),
    ]
    client.fetch_all(PEOPLE)
    assert waits == [1.0, 2.0]


@respx.mock
def test_gives_up_after_repeated_429(client: ProductiveClient, waits: list[float]) -> None:
    respx.get(PEOPLE_URL).respond(429, text="Too many requests")
    with pytest.raises(StudioPulseError, match="HTTP 429"):
        client.fetch_all(PEOPLE)
    assert len(waits) == 4


@respx.mock
@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_name_the_settings_not_the_token(client: ProductiveClient, status: int) -> None:
    respx.get(PEOPLE_URL).respond(status)
    with pytest.raises(StudioPulseError, match="PRODUCTIVE_API_TOKEN") as error:
        client.fetch_all(PEOPLE)
    assert TOKEN not in str(error.value)


@respx.mock
def test_server_error_is_readable(client: ProductiveClient) -> None:
    respx.get(PEOPLE_URL).respond(500, text="Internal error")
    with pytest.raises(StudioPulseError, match=r"HTTP 500 for /people: Internal error"):
        client.fetch_all(PEOPLE)


@respx.mock
def test_network_error_is_readable(client: ProductiveClient) -> None:
    respx.get(PEOPLE_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
    with pytest.raises(StudioPulseError, match="Could not reach the Productive API"):
        client.fetch_all(PEOPLE)


def test_get_source_requires_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # no developer .env
    with pytest.raises(StudioPulseError, match="PRODUCTIVE_API_TOKEN and PRODUCTIVE_ORG_ID"):
        get_source(Settings(data_source="productive"))


def test_get_source_returns_productive_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        data_source="productive", productive_api_token=SecretStr(TOKEN), productive_org_id="4242"
    )
    assert isinstance(get_source(settings), ProductiveSource)
