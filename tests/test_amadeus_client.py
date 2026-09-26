import httpx
import pytest
import respx
from django.core.cache import cache

from alerts.services.amadeus_client import AmadeusClient, AmadeusError, TOKEN_CACHE_KEY

BASE_URL = "https://test.api.amadeus.com"


@pytest.fixture(autouse=True)
def clear_token_cache():
    cache.delete(TOKEN_CACHE_KEY)
    yield
    cache.delete(TOKEN_CACHE_KEY)


@pytest.fixture
def client():
    return AmadeusClient(client_id="id", client_secret="secret", base_url=BASE_URL)


def _mock_token(respx_mock, token="fake-token", expires_in=1800):
    return respx_mock.post(f"{BASE_URL}/v1/security/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": token, "expires_in": expires_in})
    )


@respx.mock
def test_get_access_token_fetches_and_caches(client):
    token_route = _mock_token(respx.mock)

    token = client.get_access_token()

    assert token == "fake-token"
    assert token_route.called
    assert cache.get(TOKEN_CACHE_KEY) == "fake-token"


@respx.mock
def test_get_access_token_uses_cache_on_second_call(client):
    token_route = _mock_token(respx.mock)

    client.get_access_token()
    client.get_access_token()

    assert token_route.call_count == 1


@respx.mock
def test_get_access_token_raises_on_auth_failure(client):
    respx.mock.post(f"{BASE_URL}/v1/security/oauth2/token").mock(
        return_value=httpx.Response(401, json={"error": "invalid_client"})
    )

    with pytest.raises(AmadeusError):
        client.get_access_token()


@respx.mock
def test_search_flight_offers_parses_price_and_currency(client):
    _mock_token(respx.mock)
    respx.mock.get(f"{BASE_URL}/v2/shopping/flight-offers").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"price": {"grandTotal": "1234.56", "currency": "BRL"}, "id": "1"},
                    {"price": {"grandTotal": "999.00", "currency": "BRL"}, "id": "2"},
                ]
            },
        )
    )

    offers = client.search_flight_offers("BEL", "LIS", "2026-03-01")

    assert len(offers) == 2
    assert offers[0].price == 1234.56
    assert offers[1].price == 999.00
    assert offers[0].currency == "BRL"


@respx.mock
def test_find_cheapest_offer_returns_lowest_price(client):
    _mock_token(respx.mock)
    respx.mock.get(f"{BASE_URL}/v2/shopping/flight-offers").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"price": {"grandTotal": "1234.56", "currency": "BRL"}, "id": "1"},
                    {"price": {"grandTotal": "999.00", "currency": "BRL"}, "id": "2"},
                ]
            },
        )
    )

    offer = client.find_cheapest_offer("BEL", "LIS", "2026-03-01")

    assert offer.price == 999.00


@respx.mock
def test_find_cheapest_offer_returns_none_when_no_offers(client):
    _mock_token(respx.mock)
    respx.mock.get(f"{BASE_URL}/v2/shopping/flight-offers").mock(
        return_value=httpx.Response(200, json={"data": []})
    )

    offer = client.find_cheapest_offer("BEL", "LIS", "2026-03-01")

    assert offer is None


@respx.mock
def test_search_flight_offers_raises_on_api_error(client):
    _mock_token(respx.mock)
    respx.mock.get(f"{BASE_URL}/v2/shopping/flight-offers").mock(
        return_value=httpx.Response(500, text="internal error")
    )

    with pytest.raises(AmadeusError):
        client.search_flight_offers("BEL", "LIS", "2026-03-01")


@respx.mock
def test_request_retries_on_429_then_succeeds(client, monkeypatch):
    monkeypatch.setattr("alerts.services.amadeus_client.time.sleep", lambda _: None)
    _mock_token(respx.mock)

    route = respx.mock.get(f"{BASE_URL}/v2/shopping/flight-offers")
    route.side_effect = [
        httpx.Response(429, text="rate limited"),
        httpx.Response(200, json={"data": [{"price": {"grandTotal": "500.00", "currency": "BRL"}, "id": "1"}]}),
    ]

    offers = client.search_flight_offers("BEL", "LIS", "2026-03-01")

    assert len(offers) == 1
    assert offers[0].price == 500.00
    assert route.call_count == 2


@respx.mock
def test_request_refetches_token_on_401(client, monkeypatch):
    monkeypatch.setattr("alerts.services.amadeus_client.time.sleep", lambda _: None)
    token_route = _mock_token(respx.mock)

    route = respx.mock.get(f"{BASE_URL}/v2/shopping/flight-offers")
    route.side_effect = [
        httpx.Response(401, text="expired token"),
        httpx.Response(200, json={"data": [{"price": {"grandTotal": "500.00", "currency": "BRL"}, "id": "1"}]}),
    ]

    offers = client.search_flight_offers("BEL", "LIS", "2026-03-01")

    assert len(offers) == 1
    assert token_route.call_count == 2
