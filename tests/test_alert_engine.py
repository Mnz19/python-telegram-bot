from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from alerts.models import Alert, PriceHistory
from alerts.services import alert_engine
from alerts.services.amadeus_client import FlightOffer


# -- resolve_departure_date -------------------------------------------------


def test_resolve_departure_date_fixed_date(alert_with_target_price):
    result = alert_engine.resolve_departure_date(alert_with_target_price)
    assert result == alert_with_target_price.date_from


def test_resolve_departure_date_next_months(telegram_user):
    alert = Alert.objects.create(
        user=telegram_user,
        origin="BEL",
        destination="LIS",
        period_type=Alert.PeriodType.NEXT_MONTHS,
        months_ahead=2,
        target_price="1500.00",
    )
    today = date(2026, 1, 1)
    result = alert_engine.resolve_departure_date(alert, today=today)
    assert result == today + timedelta(days=60)


# -- evaluate_alert: target price -------------------------------------------


def test_evaluate_alert_triggers_on_target_price_hit(alert_with_target_price):
    decision = alert_engine.evaluate_alert(alert_with_target_price, Decimal("1499.99"), "BRL")
    assert decision.should_notify is True


def test_evaluate_alert_triggers_on_target_price_exact_match(alert_with_target_price):
    decision = alert_engine.evaluate_alert(alert_with_target_price, Decimal("1500.00"), "BRL")
    assert decision.should_notify is True


def test_evaluate_alert_does_not_trigger_above_target_price(alert_with_target_price):
    decision = alert_engine.evaluate_alert(alert_with_target_price, Decimal("1600.00"), "BRL")
    assert decision.should_notify is False


# -- evaluate_alert: drop percentage -----------------------------------------


def test_evaluate_alert_no_drop_trigger_without_history(alert_with_drop_percentage):
    """With no historical price yet, a percentage drop can't be computed."""
    decision = alert_engine.evaluate_alert(alert_with_drop_percentage, Decimal("800.00"), "BRL")
    assert decision.should_notify is False


def test_evaluate_alert_triggers_on_sufficient_drop(alert_with_drop_percentage):
    PriceHistory.objects.create(alert=alert_with_drop_percentage, price=Decimal("1000.00"), currency="BRL")
    # 25% drop from the lowest historical price of 1000 -> 750, threshold is 20%
    decision = alert_engine.evaluate_alert(alert_with_drop_percentage, Decimal("750.00"), "BRL")
    assert decision.should_notify is True
    assert decision.lowest_historical_price == Decimal("1000.00")


def test_evaluate_alert_does_not_trigger_on_insufficient_drop(alert_with_drop_percentage):
    PriceHistory.objects.create(alert=alert_with_drop_percentage, price=Decimal("1000.00"), currency="BRL")
    # Only a 10% drop, threshold is 20%
    decision = alert_engine.evaluate_alert(alert_with_drop_percentage, Decimal("900.00"), "BRL")
    assert decision.should_notify is False


def test_evaluate_alert_uses_lowest_of_multiple_historical_prices(alert_with_drop_percentage):
    PriceHistory.objects.create(alert=alert_with_drop_percentage, price=Decimal("1000.00"), currency="BRL")
    PriceHistory.objects.create(alert=alert_with_drop_percentage, price=Decimal("700.00"), currency="BRL")
    # 20% drop from the true lowest (700) would require <= 560, not just <= 800 (from 1000)
    decision = alert_engine.evaluate_alert(alert_with_drop_percentage, Decimal("650.00"), "BRL")
    assert decision.should_notify is False


# -- check_alert: wiring between Amadeus client, PriceHistory, and notify ----


def test_check_alert_records_price_history_and_notifies(alert_with_target_price):
    client = MagicMock()
    client.find_cheapest_offer.return_value = FlightOffer(price=1200.0, currency="BRL", raw={"id": "1"})
    notify_fn = MagicMock()

    decision = alert_engine.check_alert(alert_with_target_price, client=client, notify_fn=notify_fn)

    assert decision.should_notify is True
    assert PriceHistory.objects.filter(alert=alert_with_target_price).count() == 1
    history = PriceHistory.objects.get(alert=alert_with_target_price)
    assert history.price == Decimal("1200.0")
    assert history.raw_response == {"id": "1"}
    notify_fn.assert_called_once_with(alert_with_target_price, Decimal("1200.0"), "BRL")


def test_check_alert_does_not_notify_when_threshold_not_met(alert_with_target_price):
    client = MagicMock()
    client.find_cheapest_offer.return_value = FlightOffer(price=9999.0, currency="BRL", raw={})
    notify_fn = MagicMock()

    decision = alert_engine.check_alert(alert_with_target_price, client=client, notify_fn=notify_fn)

    assert decision.should_notify is False
    notify_fn.assert_not_called()
    assert PriceHistory.objects.filter(alert=alert_with_target_price).count() == 1


def test_check_alert_returns_none_and_skips_history_when_no_offers_found(alert_with_target_price):
    client = MagicMock()
    client.find_cheapest_offer.return_value = None
    notify_fn = MagicMock()

    decision = alert_engine.check_alert(alert_with_target_price, client=client, notify_fn=notify_fn)

    assert decision is None
    notify_fn.assert_not_called()
    assert PriceHistory.objects.filter(alert=alert_with_target_price).count() == 0


def test_check_alert_returns_none_on_amadeus_error(alert_with_target_price):
    from alerts.services.amadeus_client import AmadeusError

    client = MagicMock()
    client.find_cheapest_offer.side_effect = AmadeusError("boom")
    notify_fn = MagicMock()

    decision = alert_engine.check_alert(alert_with_target_price, client=client, notify_fn=notify_fn)

    assert decision is None
    notify_fn.assert_not_called()
    assert PriceHistory.objects.filter(alert=alert_with_target_price).count() == 0
