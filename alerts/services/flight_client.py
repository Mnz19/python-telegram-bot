"""Picks which flight-price provider to talk to, based on settings.FLIGHT_PROVIDER.

Both AmadeusClient and SkyScrapperClient expose the same
`find_cheapest_offer(...)` / `search_flight_offers(...)` shape and raise
subclasses of FlightProviderError, so alert_engine and the bot commands
never need to know which one is active.
"""

from __future__ import annotations

from django.conf import settings

from .amadeus_client import AmadeusClient
from .skyscanner_client import SkyScrapperClient

FlightClient = AmadeusClient | SkyScrapperClient


def get_flight_client() -> FlightClient:
    provider = getattr(settings, "FLIGHT_PROVIDER", "skyscanner")
    if provider == "amadeus":
        return AmadeusClient()
    return SkyScrapperClient()
