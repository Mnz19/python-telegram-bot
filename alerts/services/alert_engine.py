"""Price comparison and notification-trigger logic for a single Alert.

Kept independent from Celery/Telegram so it's trivially unit-testable:
`evaluate_alert` takes an Alert + a fetched price and returns a decision;
`check_alert` wires that decision to the Amadeus client and PriceHistory;
`notify` is the only piece that talks to Telegram, and is passed in as a
callable so tests can stub it out.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable

from django.conf import settings
from telegram import Bot

from .amadeus_client import AmadeusClient, AmadeusError, FlightOffer
from ..models import Alert, PriceHistory

logger = logging.getLogger(__name__)

NotifyFn = Callable[[Alert, Decimal, str], None]


@dataclass(frozen=True)
class AlertDecision:
    should_notify: bool
    price: Decimal
    currency: str
    reason: str
    lowest_historical_price: Decimal | None


def resolve_departure_date(alert: Alert, today: date | None = None) -> date:
    """Pick a concrete departure date to query Amadeus for, based on period_type."""
    today = today or date.today()

    if alert.period_type == Alert.PeriodType.FIXED_DATE:
        return alert.date_from

    if alert.period_type == Alert.PeriodType.DATE_RANGE:
        return alert.date_from

    if alert.period_type == Alert.PeriodType.NEXT_MONTHS:
        return today + timedelta(days=30 * (alert.months_ahead or 1))

    raise ValueError(f"period_type desconhecido: {alert.period_type}")


def evaluate_alert(alert: Alert, price: Decimal, currency: str) -> AlertDecision:
    """Decide whether `price` should trigger a notification for `alert`.

    Triggers when either:
      - target_price is set and price <= target_price, or
      - drop_percentage is set and price dropped by that percentage (or
        more) relative to the lowest price ever recorded for this alert.
    """
    lowest = _lowest_historical_price(alert)

    if alert.target_price is not None and price <= alert.target_price:
        return AlertDecision(
            should_notify=True,
            price=price,
            currency=currency,
            reason=f"Preço {price} atingiu ou ficou abaixo do alvo {alert.target_price}",
            lowest_historical_price=lowest,
        )

    if alert.drop_percentage is not None and lowest is not None and lowest > 0:
        drop = (lowest - price) / lowest * Decimal("100")
        if drop >= alert.drop_percentage:
            return AlertDecision(
                should_notify=True,
                price=price,
                currency=currency,
                reason=f"Queda de {drop:.1f}% em relação ao menor preço histórico ({lowest})",
                lowest_historical_price=lowest,
            )

    return AlertDecision(
        should_notify=False,
        price=price,
        currency=currency,
        reason="Nenhum critério de disparo atendido",
        lowest_historical_price=lowest,
    )


def _lowest_historical_price(alert: Alert) -> Decimal | None:
    lowest_record = alert.price_history.order_by("price").first()
    return lowest_record.price if lowest_record else None


def check_alert(
    alert: Alert,
    client: AmadeusClient | None = None,
    notify_fn: NotifyFn | None = None,
) -> AlertDecision | None:
    """Fetch the current cheapest offer for `alert`, record it, and notify if warranted.

    Returns None if the price fetch itself failed (already logged); the
    caller (a Celery task) is expected to treat that as a soft failure and
    move on to the next alert.
    """
    client = client or AmadeusClient()
    departure_date = resolve_departure_date(alert)
    return_date = alert.date_to.isoformat() if alert.date_to else None

    try:
        offer: FlightOffer | None = client.find_cheapest_offer(
            origin=alert.origin,
            destination=alert.destination,
            departure_date=departure_date.isoformat(),
            return_date=return_date,
        )
    except AmadeusError:
        logger.exception("Falha ao consultar preços para alert #%s", alert.id)
        return None

    if offer is None:
        logger.info("Nenhuma oferta encontrada para alert #%s", alert.id)
        return None

    price = Decimal(str(offer.price))
    decision = evaluate_alert(alert, price, offer.currency)

    PriceHistory.objects.create(
        alert=alert,
        price=price,
        currency=offer.currency,
        raw_response=offer.raw,
    )

    if decision.should_notify and notify_fn is not None:
        notify_fn(alert, price, offer.currency)

    return decision


def format_notification(alert: Alert, price: Decimal, currency: str) -> str:
    return (
        f"\U0001f6a8 Preço encontrado para {alert.origin} → {alert.destination}!\n\n"
        f"Preço atual: {price} {currency}\n"
        f"Alvo: {alert.target_price or '-'} | Queda configurada: "
        f"{alert.drop_percentage or '-'}%\n"
        f"Alerta #{alert.id}"
    )


def notify(alert: Alert, price: Decimal, currency: str) -> None:
    """Send the notification message to the alert's owner via Telegram.

    This is the default `notify_fn` used by the Celery task. It's a thin,
    synchronous wrapper around the async `telegram.Bot.send_message`, kept
    separate from `check_alert` so tests can inject a stub instead.
    """
    message = format_notification(alert, price, currency)
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)

    async def _send() -> None:
        async with bot:
            await bot.send_message(chat_id=alert.user.chat_id, text=message)

    asyncio.run(_send())
