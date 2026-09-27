"""Price comparison and notification-trigger logic for a single Alert.

Kept independent from Celery/Telegram so it's trivially unit-testable:
`evaluate_alert` takes an Alert + a fetched price and returns a decision;
`check_alert` wires that decision to the Amadeus client and PriceHistory
(fanning out over every candidate date/destination for multi-destino and
flexible-weekday alerts); `notify` is the only piece that talks to Telegram,
passed in as a callable so tests can stub it out.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Callable

from django.conf import settings
from django.db.models import Avg, Count
from django.utils import timezone
from telegram import Bot

from . import chart
from .amadeus_client import FlightOffer
from .exceptions import FlightProviderError
from .flight_client import FlightClient, get_flight_client
from .constants import MAX_CANDIDATES_PER_CHECK, MAX_FLEXIBLE_DATES, POPULAR_DESTINATIONS
from ..models import Alert, PriceHistory

logger = logging.getLogger(__name__)

# Below this fraction of the historical average, a price is flagged as a
# probable fare-mistake and always triggers a notification.
MISTAKE_FARE_RATIO = Decimal("0.5")
MISTAKE_FARE_MIN_HISTORY = 3

# Rate limiting: how often an alert needs re-checking, based on how close
# its nearest candidate departure date is.
NEAR_TERM_DAYS = 30
MID_TERM_DAYS = 90
NEAR_TERM_INTERVAL = timedelta(hours=1)
MID_TERM_INTERVAL = timedelta(hours=4)
FAR_TERM_INTERVAL = timedelta(hours=24)


@dataclass(frozen=True)
class AlertDecision:
    should_notify: bool
    price: Decimal
    currency: str
    reasons: tuple[str, ...]
    lowest_historical_price: Decimal | None
    is_new_low: bool = False
    is_mistake_fare: bool = False
    matched_destination: str = ""
    matched_departure_date: date | None = None

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "Nenhum critério de disparo atendido"


NotifyFn = Callable[[Alert, Decimal, str, AlertDecision], None]


# -- Candidate generation -----------------------------------------------------


def resolve_candidate_dates(alert: Alert, today: date | None = None) -> list[date]:
    """List of departure dates worth querying Amadeus for, based on period_type."""
    today = today or timezone.localdate()

    if alert.period_type == Alert.PeriodType.FIXED_DATE:
        return [alert.date_from]

    if alert.period_type == Alert.PeriodType.NEXT_MONTHS:
        return [today + timedelta(days=30 * (alert.months_ahead or 1))]

    if alert.period_type == Alert.PeriodType.DATE_RANGE:
        return _dates_in_range(alert.date_from, alert.date_to)[:MAX_FLEXIBLE_DATES]

    if alert.period_type == Alert.PeriodType.FLEXIBLE_WEEKDAY:
        all_dates = _dates_in_range(alert.date_from, alert.date_to)
        matching = [d for d in all_dates if d.weekday() == alert.flexible_weekday]
        return matching[:MAX_FLEXIBLE_DATES]

    raise ValueError(f"period_type desconhecido: {alert.period_type}")


def _dates_in_range(start: date, end: date) -> list[date]:
    if not start or not end or end < start:
        return []
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def resolve_candidate_destinations(alert: Alert) -> list[str]:
    """Multi-destino ('me surpreenda') alerts scan every popular destination."""
    if alert.destination:
        return [alert.destination]
    return list(POPULAR_DESTINATIONS)


def resolve_return_date(alert: Alert, departure_date: date) -> date | None:
    if alert.trip_type == Alert.TripType.ROUND_TRIP and alert.trip_duration_days:
        return departure_date + timedelta(days=alert.trip_duration_days)
    return None


# -- Evaluation ----------------------------------------------------------


def _historical_stats(alert: Alert) -> tuple[Decimal | None, Decimal | None, int]:
    """Returns (lowest_price, average_price, count) from prior PriceHistory."""
    stats = alert.price_history.aggregate(avg_price=Avg("price"), count=Count("id"))
    count = stats["count"] or 0
    avg_price = stats["avg_price"]
    lowest = _lowest_historical_price(alert)
    return lowest, avg_price, count


def _lowest_historical_price(alert: Alert) -> Decimal | None:
    lowest_record = alert.price_history.order_by("price").first()
    return lowest_record.price if lowest_record else None


def evaluate_alert(
    alert: Alert,
    price: Decimal,
    currency: str,
    matched_destination: str = "",
    matched_departure_date: date | None = None,
) -> AlertDecision:
    """Decide whether `price` should trigger a notification for `alert`.

    Triggers on any of:
      - target_price is set and price <= target_price
      - drop_percentage is set and price dropped by that percentage (or
        more) relative to the lowest price ever recorded for this alert
      - the price is a new all-time low for this alert (requires at least
        one prior check, so the very first check never "trips" this)
      - the price looks like a fare-mistake: far enough below the
        historical average that it's worth flagging regardless of the
        alert's configured thresholds
    """
    lowest, average, history_count = _historical_stats(alert)
    reasons: list[str] = []

    if alert.target_price is not None and price <= alert.target_price:
        reasons.append(f"Preço {price} atingiu ou ficou abaixo do alvo {alert.target_price}")

    if alert.drop_percentage is not None and lowest is not None and lowest > 0:
        drop = (lowest - price) / lowest * Decimal("100")
        if drop >= alert.drop_percentage:
            reasons.append(f"Queda de {drop:.1f}% em relação ao menor preço histórico ({lowest})")

    # Only an automatic trigger when the user hasn't set an explicit
    # threshold of their own — otherwise every dip would double-notify.
    has_explicit_threshold = alert.target_price is not None or alert.drop_percentage is not None
    is_new_low = (
        not has_explicit_threshold and history_count > 0 and lowest is not None and price < lowest
    )
    if is_new_low:
        reasons.append(f"Novo menor preço histórico (anterior: {lowest})")

    is_mistake_fare = (
        history_count >= MISTAKE_FARE_MIN_HISTORY
        and average is not None
        and average > 0
        and price <= average * MISTAKE_FARE_RATIO
    )
    if is_mistake_fare:
        reasons.append(f"Preço muito abaixo da média histórica ({average:.2f}) — possível erro de tarifa")

    return AlertDecision(
        should_notify=bool(reasons),
        price=price,
        currency=currency,
        reasons=tuple(reasons),
        lowest_historical_price=lowest,
        is_new_low=is_new_low,
        is_mistake_fare=is_mistake_fare,
        matched_destination=matched_destination,
        matched_departure_date=matched_departure_date,
    )


# -- Rate limiting ---------------------------------------------------------


def should_check_now(alert: Alert, now: datetime | None = None) -> bool:
    """Whether it's worth spending an Amadeus call on `alert` right now.

    Alerts with a nearby departure date are checked every run; alerts far
    in the future are checked less often, to conserve rate-limited API calls.
    """
    now = now or timezone.now()

    try:
        candidate_dates = resolve_candidate_dates(alert, today=now.date())
    except ValueError:
        return True
    if not candidate_dates:
        return True

    nearest = min(candidate_dates)
    days_out = (nearest - now.date()).days

    last_checked_at = alert.price_history.order_by("-checked_at").values_list(
        "checked_at", flat=True
    ).first()
    if last_checked_at is None:
        return True

    if days_out <= NEAR_TERM_DAYS:
        required_interval = NEAR_TERM_INTERVAL
    elif days_out <= MID_TERM_DAYS:
        required_interval = MID_TERM_INTERVAL
    else:
        required_interval = FAR_TERM_INTERVAL

    return (now - last_checked_at) >= required_interval


# -- Checking an alert ------------------------------------------------------


def check_alert(
    alert: Alert,
    client: FlightClient | None = None,
    notify_fn: NotifyFn | None = None,
) -> AlertDecision | None:
    """Fetch the cheapest offer across every candidate (date, destination)
    pair for `alert`, record it, and notify if warranted.

    Returns None if no offer could be found/fetched at all (already logged);
    the caller (a Celery task) treats that as a soft failure.
    """
    client = client or get_flight_client()

    dates = resolve_candidate_dates(alert)
    destinations = resolve_candidate_destinations(alert)
    pairs = list(itertools.product(dates, destinations))[:MAX_CANDIDATES_PER_CHECK]

    best_offer: FlightOffer | None = None
    best_pair: tuple[date, str] | None = None

    for departure_date, destination in pairs:
        return_date = resolve_return_date(alert, departure_date)
        try:
            offer = client.find_cheapest_offer(
                origin=alert.origin,
                destination=destination,
                departure_date=departure_date.isoformat(),
                return_date=return_date.isoformat() if return_date else None,
                included_airline_codes=alert.preferred_airlines or None,
                require_checked_bag=alert.require_checked_bag,
            )
        except FlightProviderError:
            logger.warning(
                "Falha ao consultar %s->%s em %s para alert #%s",
                alert.origin, destination, departure_date, alert.id,
            )
            continue

        if offer is not None and (best_offer is None or offer.price < best_offer.price):
            best_offer = offer
            best_pair = (departure_date, destination)

    if best_offer is None or best_pair is None:
        logger.info("Nenhuma oferta encontrada para alert #%s", alert.id)
        return None

    price = Decimal(str(best_offer.price))
    decision = evaluate_alert(
        alert,
        price,
        best_offer.currency,
        matched_destination=best_pair[1],
        matched_departure_date=best_pair[0],
    )

    PriceHistory.objects.create(
        alert=alert,
        price=price,
        currency=best_offer.currency,
        raw_response=best_offer.raw,
        matched_departure_date=best_pair[0],
        matched_destination=best_pair[1],
        is_mistake_fare=decision.is_mistake_fare,
    )

    if decision.should_notify and notify_fn is not None:
        notify_fn(alert, price, best_offer.currency, decision)

    return decision


# -- Notifications ----------------------------------------------------------


def format_notification(alert: Alert, price: Decimal, currency: str, decision: AlertDecision) -> str:
    header = "🚨 Possível erro de tarifa!" if decision.is_mistake_fare else "🚨 Queda de preço encontrada!"
    destination = decision.matched_destination or alert.destination or "vários destinos"
    label = f"{alert.name}\n" if alert.name else ""

    lines = [
        header,
        "",
        f"{label}{alert.origin} → {destination}",
        f"Preço atual: {price} {currency}",
    ]
    if decision.matched_departure_date:
        lines.append(f"Data: {decision.matched_departure_date.isoformat()}")
    lines.append(decision.reason)
    lines.append(f"Alerta #{alert.id}")
    return "\n".join(lines)


def notify(alert: Alert, price: Decimal, currency: str, decision: AlertDecision | None = None) -> None:
    """Send the notification message (and a price-history chart, if there's
    enough data) to the alert's owner via Telegram.

    This is the default `notify_fn` used by the Celery task. It's a thin,
    synchronous wrapper around the async `telegram.Bot` calls, kept separate
    from `check_alert` so tests can inject a stub instead.
    """
    decision = decision or evaluate_alert(alert, price, currency)
    message = format_notification(alert, price, currency, decision)
    chart_png = chart.render_price_history_png(alert)
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)

    async def _send() -> None:
        async with bot:
            await bot.send_message(chat_id=alert.user.chat_id, text=message)
            if chart_png:
                await bot.send_photo(chat_id=alert.user.chat_id, photo=chart_png)

    asyncio.run(_send())


def build_weekly_summary_message(user, since: datetime) -> str | None:
    """Builds the weekly digest text for `user`, or None if they have no
    active alerts with any price history in the window."""
    lines = []
    for alert in user.alerts.filter(is_active=True).order_by("id"):
        cheapest = alert.price_history.filter(checked_at__gte=since).order_by("price").first()
        if cheapest is None:
            continue
        destination = alert.destination or "vários destinos"
        label = f"{alert.name} - " if alert.name else ""
        lines.append(
            f"#{alert.id} {label}{alert.origin} → {destination}: "
            f"menor preço da semana {cheapest.price} {cheapest.currency}"
        )

    if not lines:
        return None

    return "📅 Resumo semanal de preços:\n\n" + "\n".join(lines)


def send_weekly_summary_to_user(user, since: datetime) -> None:
    message = build_weekly_summary_message(user, since)
    if message is None:
        return

    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)

    async def _send() -> None:
        async with bot:
            await bot.send_message(chat_id=user.chat_id, text=message)

    asyncio.run(_send())
