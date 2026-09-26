"""Thin wrapper around the Amadeus Flight Offers Search API.

Handles OAuth2 client-credentials auth (token cached via Django's cache
framework) and basic rate-limit retry/backoff. No other module should talk
to Amadeus directly.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

TOKEN_CACHE_KEY = "amadeus:access_token"
TOKEN_SAFETY_MARGIN_SECONDS = 60
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 1.0


class AmadeusError(Exception):
    """Raised when the Amadeus API returns an unrecoverable error."""


@dataclass(frozen=True)
class FlightOffer:
    price: float
    currency: str
    raw: dict[str, Any]


class AmadeusClient:
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.client_id = client_id or settings.AMADEUS_CLIENT_ID
        self.client_secret = client_secret or settings.AMADEUS_CLIENT_SECRET
        self.base_url = (base_url or settings.AMADEUS_BASE_URL).rstrip("/")
        self._http = http_client or httpx.Client(base_url=self.base_url, timeout=15.0)

    # -- Auth -----------------------------------------------------------

    def _fetch_new_token(self) -> str:
        response = self._http.post(
            "/v1/security/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code != 200:
            raise AmadeusError(f"Falha ao autenticar na Amadeus: {response.status_code} {response.text}")

        payload = response.json()
        token = payload["access_token"]
        expires_in = int(payload.get("expires_in", 1800))
        cache_ttl = max(expires_in - TOKEN_SAFETY_MARGIN_SECONDS, 60)
        cache.set(TOKEN_CACHE_KEY, token, timeout=cache_ttl)
        return token

    def get_access_token(self) -> str:
        token = cache.get(TOKEN_CACHE_KEY)
        if token:
            return token
        return self._fetch_new_token()

    # -- Flight offers ----------------------------------------------------

    def _request_with_retry(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        backoff = INITIAL_BACKOFF_SECONDS
        last_response: httpx.Response | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            token = self.get_access_token()
            headers = kwargs.pop("headers", {})
            headers["Authorization"] = f"Bearer {token}"

            response = self._http.request(method, path, headers=headers, **kwargs)

            if response.status_code == 401:
                cache.delete(TOKEN_CACHE_KEY)
                last_response = response
                continue

            if response.status_code == 429:
                logger.warning("Amadeus rate limit atingido, tentativa %s/%s", attempt, MAX_RETRIES)
                last_response = response
                time.sleep(backoff)
                backoff *= 2
                continue

            return response

        raise AmadeusError(
            f"Amadeus API falhou após {MAX_RETRIES} tentativas: "
            f"{last_response.status_code if last_response else 'sem resposta'}"
        )

    def search_flight_offers(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: str | None = None,
        adults: int = 1,
        currency: str = "BRL",
        max_results: int = 10,
    ) -> list[FlightOffer]:
        """Search flight offers for a single departure date.

        `departure_date` / `return_date` must be ISO strings (YYYY-MM-DD).
        """
        params: dict[str, Any] = {
            "originLocationCode": origin,
            "destinationLocationCode": destination,
            "departureDate": departure_date,
            "adults": adults,
            "currencyCode": currency,
            "max": max_results,
        }
        if return_date:
            params["returnDate"] = return_date

        response = self._request_with_retry(
            "GET", "/v2/shopping/flight-offers", params=params
        )

        if response.status_code != 200:
            raise AmadeusError(
                f"Erro ao buscar ofertas ({origin}->{destination} em {departure_date}): "
                f"{response.status_code} {response.text}"
            )

        data = response.json()
        offers = []
        for item in data.get("data", []):
            price_info = item["price"]
            offers.append(
                FlightOffer(
                    price=float(price_info["grandTotal"]),
                    currency=price_info["currency"],
                    raw=item,
                )
            )
        return offers

    def find_cheapest_offer(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: str | None = None,
        currency: str = "BRL",
    ) -> FlightOffer | None:
        offers = self.search_flight_offers(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            currency=currency,
        )
        if not offers:
            return None
        return min(offers, key=lambda offer: offer.price)
