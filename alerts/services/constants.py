"""Shared static lookup tables used by the alert engine and the bot."""

# Scanned for "multi-destino / me surpreenda" alerts (blank Alert.destination).
# Kept short on purpose: each extra destination is one more Amadeus call per check.
POPULAR_DESTINATIONS = [
    "GRU",  # São Paulo
    "GIG",  # Rio de Janeiro
    "LIS",  # Lisboa
    "MAD",  # Madri
    "CDG",  # Paris
    "LHR",  # Londres
    "MCO",  # Orlando
    "MIA",  # Miami
    "EZE",  # Buenos Aires
    "SSA",  # Salvador
]

# Cap on (date, destination) candidate pairs evaluated per check, so a
# flexible-weekday + multi-destino alert can't fan out into dozens of
# Amadeus calls in one run.
MAX_CANDIDATES_PER_CHECK = 20

# Cap on how many matching weekday dates are generated for FLEXIBLE_WEEKDAY
# alerts within the user's month range.
MAX_FLEXIBLE_DATES = 8
