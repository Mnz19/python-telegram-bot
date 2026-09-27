"""Wrapper around the "Sky Scrapper" Skyscanner-data API on RapidAPI.

Default flight-price provider for this project (see FLIGHT_PROVIDER in
settings). Chosen over Amadeus for local development because signup is just
a RapidAPI account (Google/GitHub login) with an instant free-tier API key —
no manual business/company review.

Caveat: this is meta-search data, not GDS/NDC fare data, so per-fare checked
-bag info isn't available here. `require_checked_bag` is accepted for
interface compatibility with AmadeusClient (same FlightOffer/duck-typed
`find_cheapest_offer` shape) but has no effect — every offer reports
has_checked_bag=False and a warning is logged instead of silently dropping
every result. Airline preference filtering happens client-side on the
parsed carrier codes, since the API has no such request parameter.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from django.conf import settings
from django.core.cache import cache

from .amadeus_client import FlightOffer
from .exceptions import FlightProviderError

logger = logging.getLogger(__name__)

RAPIDAPI_HOST = "sky-scrapper.p.rapidapi.com"
# Airport -> (skyId, entityId) resolution is effectively static; cache it
# for a long time so it doesn't eat into the free-tier request quota.
AIRPORT_CACHE_TTL_SECONDS = 60 * 60 * 24 * 30


class SkyScrapperError(FlightProviderError):
    """Raised when the Sky Scrapper API returns an unrecoverable error."""


class SkyScrapperClient:
    def __init__(self, api_key: str | None = None, http_client: httpx.Client | None = None) -> None:
        self.api_key = api_key or settings.RAPIDAPI_KEY
        self._http = http_client or httpx.Client(base_url=f"https://{RAPIDAPI_HOST}", timeout=20.0)

    def _headers(self) -> dict[str, str]:
        return {"X-RapidAPI-Key": self.api_key, "X-RapidAPI-Host": RAPIDAPI_HOST}

    # -- Airport resolution -------------------------------------------------

    def resolve_airport(self, iata_code: str) -> tuple[str, str]:
        """Returns (skyId, entityId) for an IATA code, required by searchFlights.

        `query` on this endpoint is a free-text search over airport/city
        names, not an exact IATA lookup — searching "BEL" returns Belgaum,
        Belgrade, Belfast (x3), Belém and Belgium, in that order. We must
        pick the entry whose own skyId matches the requested code exactly,
        not just the first result.
        """
        iata_code = iata_code.upper()
        cache_key = f"skyscanner:airport:{iata_code}"
        cached = cache.get(cache_key)
        if cached:
            return tuple(cached)

        response = self._http.get(
            "/api/v1/flights/searchAirport",
            params={"query": iata_code},
            headers=self._headers(),
        )
        if response.status_code != 200:
            raise SkyScrapperError(
                f"Falha ao resolver aeroporto {iata_code}: {response.status_code} {response.text}"
            )

        entries = response.json().get("data", [])
        if not entries:
            raise SkyScrapperError(f"Aeroporto não encontrado: {iata_code}")

        match = None
        for entry in entries:
            flight_params = entry.get("navigation", {}).get("relevantFlightParams", {})
            sky_id = flight_params.get("skyId") or entry.get("skyId")
            if sky_id and sky_id.upper() == iata_code:
                match = flight_params if flight_params.get("entityId") else entry
                break

        if match is None:
            logger.warning(
                "Nenhum resultado de searchAirport bateu exatamente com %s; "
                "usando o primeiro resultado (%s) como aproximação.",
                iata_code, entries[0].get("navigation", {}).get("localizedName"),
            )
            match = entries[0].get("navigation", {}).get("relevantFlightParams", {})

        sky_id = match.get("skyId")
        entity_id = match.get("entityId")
        if not sky_id or not entity_id:
            raise SkyScrapperError(f"Resposta inesperada ao resolver aeroporto {iata_code}")

        cache.set(cache_key, (sky_id, entity_id), timeout=AIRPORT_CACHE_TTL_SECONDS)
        return sky_id, entity_id

    # -- Flight offers ----------------------------------------------------

    def search_flight_offers(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: str | None = None,
        adults: int = 1,
        currency: str = "BRL",
        max_results: int = 10,
        included_airline_codes: list[str] | None = None,
    ) -> list[FlightOffer]:
        origin_sky_id, origin_entity_id = self.resolve_airport(origin)
        destination_sky_id, destination_entity_id = self.resolve_airport(destination)

        params: dict[str, Any] = {
            "originSkyId": origin_sky_id,
            "destinationSkyId": destination_sky_id,
            "originEntityId": origin_entity_id,
            "destinationEntityId": destination_entity_id,
            "date": departure_date,
            "cabinClass": "economy",
            "adults": adults,
            "sortBy": "price_low",
            "currency": currency,
            "market": "en-US",
            "countryCode": "BR",
        }
        if return_date:
            params["returnDate"] = return_date

        response = self._http.get(
            "/api/v1/flights/searchFlights", params=params, headers=self._headers()
        )
        if response.status_code != 200:
            raise SkyScrapperError(
                f"Erro ao buscar ofertas ({origin}->{destination} em {departure_date}): "
                f"{response.status_code} {response.text}"
            )

        payload = response.json()
        itineraries = payload.get("data", {}).get("itineraries", [])

        offers = []
        for item in itineraries[:max_results]:
            price_info = item.get("price") or {}
            if "raw" not in price_info:
                continue
            offers.append(
                FlightOffer(
                    price=float(price_info["raw"]),
                    currency=currency,
                    raw=item,
                    carrier_codes=_extract_carrier_codes(item),
                    has_checked_bag=False,
                )
            )

        if included_airline_codes:
            wanted = {code.upper() for code in included_airline_codes}
            offers = [offer for offer in offers if wanted.intersection(offer.carrier_codes)]

        return offers

    def find_cheapest_offer(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: str | None = None,
        currency: str = "BRL",
        included_airline_codes: list[str] | None = None,
        require_checked_bag: bool = False,
    ) -> FlightOffer | None:
        if require_checked_bag:
            logger.warning(
                "require_checked_bag pedido, mas o provedor Sky Scrapper não expõe "
                "informação de bagagem por oferta — ignorando o filtro."
            )

        offers = self.search_flight_offers(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            currency=currency,
            included_airline_codes=included_airline_codes,
        )
        if not offers:
            return None
        return min(offers, key=lambda offer: offer.price)


def _extract_carrier_codes(item: dict[str, Any]) -> tuple[str, ...]:
    codes = set()
    for leg in item.get("legs", []):
        for carrier in leg.get("carriers", {}).get("marketing", []):
            code = carrier.get("alternateId")
            if code:
                codes.add(code)
    return tuple(sorted(codes))
