class FlightProviderError(Exception):
    """Raised when a flight-price provider (Amadeus, Sky Scrapper, ...) returns
    an unrecoverable error. alert_engine catches this base class so it doesn't
    need to know which concrete provider is configured."""
