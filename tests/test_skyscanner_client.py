import httpx
import pytest
import respx
from django.core.cache import cache

from alerts.services.skyscanner_client import RAPIDAPI_HOST, SkyScrapperClient, SkyScrapperError

BASE_URL = f"https://{RAPIDAPI_HOST}"


@pytest.fixture(autouse=True)
def clear_airport_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def client():
    return SkyScrapperClient(api_key="test-key")


def _airport_payload(sky_id="LOND", entity_id="27544008"):
    return {
        "status": True,
        "data": [
            {
                "skyId": sky_id,
                "entityId": entity_id,
                "navigation": {"relevantFlightParams": {"skyId": sky_id, "entityId": entity_id}},
            }
        ],
    }


def _mock_airport(respx_mock, query, sky_id, entity_id):
    return respx_mock.get(f"{BASE_URL}/api/v1/flights/searchAirport", params={"query": query}).mock(
        return_value=httpx.Response(200, json=_airport_payload(sky_id, entity_id))
    )


@respx.mock
def test_resolve_airport_returns_sky_id_and_entity_id(client):
    _mock_airport(respx.mock, "LIS", "LIS", "27544850")

    sky_id, entity_id = client.resolve_airport("LIS")

    assert sky_id == "LIS"
    assert entity_id == "27544850"


@respx.mock
def test_resolve_airport_is_cached(client):
    route = _mock_airport(respx.mock, "LIS", "LIS", "27544850")

    client.resolve_airport("LIS")
    client.resolve_airport("LIS")

    assert route.call_count == 1


@respx.mock
def test_resolve_airport_picks_exact_sky_id_match_not_first_result(client):
    """Regression test: searchAirport?query=BEL is a free-text name search,
    not an exact IATA lookup — it returns Belgaum, Belgrade, Belfast (x3),
    Belém and Belgium in that order for a real "BEL" query. We must find
    the entry whose own skyId is exactly "BEL", not just take entries[0]."""
    payload = {
        "status": True,
        "data": [
            {"navigation": {"localizedName": "Belgaum", "relevantFlightParams": {"skyId": "IXG", "entityId": "1"}}},
            {"navigation": {"localizedName": "Belgrade Nikola Tesla", "relevantFlightParams": {"skyId": "BEG", "entityId": "2"}}},
            {"navigation": {"localizedName": "Belfast", "relevantFlightParams": {"skyId": "BELF", "entityId": "3"}}},
            {"navigation": {"localizedName": "Belfast International", "relevantFlightParams": {"skyId": "BFS", "entityId": "4"}}},
            {"navigation": {"localizedName": "Belfast City", "relevantFlightParams": {"skyId": "BHD", "entityId": "5"}}},
            {"navigation": {"localizedName": "Belem", "relevantFlightParams": {"skyId": "BEL", "entityId": "95673777"}}},
            {"navigation": {"localizedName": "Belgium", "relevantFlightParams": {"skyId": "BE", "entityId": "7"}}},
            {"navigation": {"localizedName": "Belgorod", "relevantFlightParams": {"skyId": "EGO", "entityId": "8"}}},
        ],
    }
    respx.mock.get(f"{BASE_URL}/api/v1/flights/searchAirport").mock(
        return_value=httpx.Response(200, json=payload)
    )

    sky_id, entity_id = client.resolve_airport("BEL")

    assert sky_id == "BEL"
    assert entity_id == "95673777"


@respx.mock
def test_resolve_airport_falls_back_to_first_result_without_exact_match(client, caplog):
    payload = {
        "status": True,
        "data": [
            {"navigation": {"localizedName": "Belfast", "relevantFlightParams": {"skyId": "BELF", "entityId": "3"}}},
        ],
    }
    respx.mock.get(f"{BASE_URL}/api/v1/flights/searchAirport").mock(
        return_value=httpx.Response(200, json=payload)
    )

    sky_id, entity_id = client.resolve_airport("BEL")

    assert sky_id == "BELF"
    assert entity_id == "3"


@respx.mock
def test_resolve_airport_raises_when_not_found(client):
    respx.mock.get(f"{BASE_URL}/api/v1/flights/searchAirport").mock(
        return_value=httpx.Response(200, json={"status": True, "data": []})
    )

    with pytest.raises(SkyScrapperError):
        client.resolve_airport("ZZZ")


@respx.mock
def test_resolve_airport_raises_on_http_error(client):
    respx.mock.get(f"{BASE_URL}/api/v1/flights/searchAirport").mock(
        return_value=httpx.Response(500, text="boom")
    )

    with pytest.raises(SkyScrapperError):
        client.resolve_airport("LIS")


def _flight_search_payload(price=900.0, carrier="LA"):
    return {
        "status": True,
        "data": {
            "itineraries": [
                {
                    "price": {"raw": price, "formatted": f"R${price}"},
                    "legs": [
                        {
                            "durationInMinutes": 500,
                            "stopCount": 0,
                            "carriers": {"marketing": [{"name": "LATAM", "alternateId": carrier}]},
                        }
                    ],
                }
            ]
        },
    }


@respx.mock
def test_search_flight_offers_parses_price_and_carrier(client):
    _mock_airport(respx.mock, "BEL", "BEL", "1")
    _mock_airport(respx.mock, "LIS", "LIS", "2")
    respx.mock.get(f"{BASE_URL}/api/v1/flights/searchFlights").mock(
        return_value=httpx.Response(200, json=_flight_search_payload(price=900.0, carrier="LA"))
    )

    offers = client.search_flight_offers("BEL", "LIS", "2026-03-01", currency="BRL")

    assert len(offers) == 1
    assert offers[0].price == 900.0
    assert offers[0].currency == "BRL"
    assert offers[0].carrier_codes == ("LA",)
    assert offers[0].has_checked_bag is False


@respx.mock
def test_search_flight_offers_filters_by_included_airline_codes(client):
    _mock_airport(respx.mock, "BEL", "BEL", "1")
    _mock_airport(respx.mock, "LIS", "LIS", "2")
    respx.mock.get(f"{BASE_URL}/api/v1/flights/searchFlights").mock(
        return_value=httpx.Response(200, json=_flight_search_payload(carrier="G3"))
    )

    offers = client.search_flight_offers(
        "BEL", "LIS", "2026-03-01", included_airline_codes=["LA"]
    )

    assert offers == []


@respx.mock
def test_search_flight_offers_raises_on_api_error(client):
    _mock_airport(respx.mock, "BEL", "BEL", "1")
    _mock_airport(respx.mock, "LIS", "LIS", "2")
    respx.mock.get(f"{BASE_URL}/api/v1/flights/searchFlights").mock(
        return_value=httpx.Response(500, text="boom")
    )

    with pytest.raises(SkyScrapperError):
        client.search_flight_offers("BEL", "LIS", "2026-03-01")


@respx.mock
def test_find_cheapest_offer_returns_lowest_price(client):
    _mock_airport(respx.mock, "BEL", "BEL", "1")
    _mock_airport(respx.mock, "LIS", "LIS", "2")
    payload = {
        "status": True,
        "data": {
            "itineraries": [
                {"price": {"raw": 900.0}, "legs": []},
                {"price": {"raw": 500.0}, "legs": []},
            ]
        },
    }
    respx.mock.get(f"{BASE_URL}/api/v1/flights/searchFlights").mock(
        return_value=httpx.Response(200, json=payload)
    )

    offer = client.find_cheapest_offer("BEL", "LIS", "2026-03-01")

    assert offer.price == 500.0


@respx.mock
def test_find_cheapest_offer_ignores_require_checked_bag_instead_of_filtering_everything(client, caplog):
    _mock_airport(respx.mock, "BEL", "BEL", "1")
    _mock_airport(respx.mock, "LIS", "LIS", "2")
    respx.mock.get(f"{BASE_URL}/api/v1/flights/searchFlights").mock(
        return_value=httpx.Response(200, json=_flight_search_payload())
    )

    offer = client.find_cheapest_offer("BEL", "LIS", "2026-03-01", require_checked_bag=True)

    assert offer is not None


@respx.mock
def test_find_cheapest_offer_returns_none_when_no_itineraries(client):
    _mock_airport(respx.mock, "BEL", "BEL", "1")
    _mock_airport(respx.mock, "LIS", "LIS", "2")
    respx.mock.get(f"{BASE_URL}/api/v1/flights/searchFlights").mock(
        return_value=httpx.Response(200, json={"status": True, "data": {"itineraries": []}})
    )

    offer = client.find_cheapest_offer("BEL", "LIS", "2026-03-01")

    assert offer is None
