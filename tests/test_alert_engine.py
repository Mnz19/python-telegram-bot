from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import ANY, MagicMock

import pytest

from alerts.models import Alert, PriceHistory
from alerts.services import alert_engine
from alerts.services.amadeus_client import AmadeusError, FlightOffer
from alerts.services.constants import POPULAR_DESTINATIONS


# -- resolve_candidate_dates -------------------------------------------------


def test_resolve_candidate_dates_fixed_date(alert_with_target_price):
    result = alert_engine.resolve_candidate_dates(alert_with_target_price)
    assert result == [alert_with_target_price.date_from]


def test_resolve_candidate_dates_next_months(telegram_user):
    alert = Alert.objects.create(
        user=telegram_user,
        origin="BEL",
        destination="LIS",
        period_type=Alert.PeriodType.NEXT_MONTHS,
        months_ahead=2,
        trip_type=Alert.TripType.ONE_WAY,
        target_price=Decimal("1500.00"),
    )
    today = date(2026, 1, 1)
    result = alert_engine.resolve_candidate_dates(alert, today=today)
    assert result == [today + timedelta(days=60)]


def test_resolve_candidate_dates_date_range(telegram_user):
    alert = Alert.objects.create(
        user=telegram_user,
        origin="BEL",
        destination="LIS",
        period_type=Alert.PeriodType.DATE_RANGE,
        date_from=date(2026, 1, 10),
        date_to=date(2026, 1, 13),
        trip_type=Alert.TripType.ONE_WAY,
        target_price=Decimal("1500.00"),
    )
    result = alert_engine.resolve_candidate_dates(alert)
    assert result == [date(2026, 1, 10), date(2026, 1, 11), date(2026, 1, 12), date(2026, 1, 13)]


def test_resolve_candidate_dates_flexible_weekday_matches_only_that_weekday(telegram_user):
    alert = Alert.objects.create(
        user=telegram_user,
        origin="BEL",
        destination="LIS",
        period_type=Alert.PeriodType.FLEXIBLE_WEEKDAY,
        date_from=date(2026, 10, 1),
        date_to=date(2026, 10, 31),
        flexible_weekday=4,  # Friday
        trip_type=Alert.TripType.ONE_WAY,
        target_price=Decimal("1500.00"),
    )
    result = alert_engine.resolve_candidate_dates(alert)
    assert result == [date(2026, 10, 2), date(2026, 10, 9), date(2026, 10, 16), date(2026, 10, 23), date(2026, 10, 30)]
    assert all(d.weekday() == 4 for d in result)


def test_resolve_candidate_dates_flexible_weekday_is_capped(telegram_user):
    alert = Alert.objects.create(
        user=telegram_user,
        origin="BEL",
        destination="LIS",
        period_type=Alert.PeriodType.FLEXIBLE_WEEKDAY,
        date_from=date(2026, 1, 1),
        date_to=date(2026, 12, 31),
        flexible_weekday=4,
        trip_type=Alert.TripType.ONE_WAY,
        target_price=Decimal("1500.00"),
    )
    result = alert_engine.resolve_candidate_dates(alert)
    assert len(result) == alert_engine.MAX_FLEXIBLE_DATES


# -- resolve_candidate_destinations / resolve_return_date --------------------


def test_resolve_candidate_destinations_single(alert_with_target_price):
    assert alert_engine.resolve_candidate_destinations(alert_with_target_price) == ["LIS"]


def test_resolve_candidate_destinations_multi(multi_destination_alert):
    assert alert_engine.resolve_candidate_destinations(multi_destination_alert) == POPULAR_DESTINATIONS


def test_resolve_return_date_round_trip(telegram_user):
    alert = Alert(trip_type=Alert.TripType.ROUND_TRIP, trip_duration_days=7)
    assert alert_engine.resolve_return_date(alert, date(2026, 3, 1)) == date(2026, 3, 8)


def test_resolve_return_date_one_way_is_none():
    alert = Alert(trip_type=Alert.TripType.ONE_WAY, trip_duration_days=7)
    assert alert_engine.resolve_return_date(alert, date(2026, 3, 1)) is None


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
    decision = alert_engine.evaluate_alert(alert_with_drop_percentage, Decimal("800.00"), "BRL")
    assert decision.should_notify is False


def test_evaluate_alert_triggers_on_sufficient_drop(alert_with_drop_percentage):
    PriceHistory.objects.create(alert=alert_with_drop_percentage, price=Decimal("1000.00"), currency="BRL")
    decision = alert_engine.evaluate_alert(alert_with_drop_percentage, Decimal("750.00"), "BRL")
    assert decision.should_notify is True
    assert decision.lowest_historical_price == Decimal("1000.00")


def test_evaluate_alert_does_not_trigger_on_insufficient_drop(alert_with_drop_percentage):
    PriceHistory.objects.create(alert=alert_with_drop_percentage, price=Decimal("1000.00"), currency="BRL")
    # 10% drop only, and drop_percentage alerts don't auto-trigger on new-low.
    decision = alert_engine.evaluate_alert(alert_with_drop_percentage, Decimal("900.00"), "BRL")
    assert decision.should_notify is False


def test_evaluate_alert_uses_lowest_of_multiple_historical_prices(alert_with_drop_percentage):
    PriceHistory.objects.create(alert=alert_with_drop_percentage, price=Decimal("1000.00"), currency="BRL")
    PriceHistory.objects.create(alert=alert_with_drop_percentage, price=Decimal("700.00"), currency="BRL")
    decision = alert_engine.evaluate_alert(alert_with_drop_percentage, Decimal("650.00"), "BRL")
    assert decision.should_notify is False


# -- evaluate_alert: automatic new-low (only when no explicit threshold) ----


def test_evaluate_alert_new_low_triggers_without_threshold(alert_without_threshold):
    PriceHistory.objects.create(alert=alert_without_threshold, price=Decimal("1000.00"), currency="BRL")
    decision = alert_engine.evaluate_alert(alert_without_threshold, Decimal("999.00"), "BRL")
    assert decision.should_notify is True
    assert decision.is_new_low is True


def test_evaluate_alert_first_ever_price_is_not_a_new_low(alert_without_threshold):
    decision = alert_engine.evaluate_alert(alert_without_threshold, Decimal("999.00"), "BRL")
    assert decision.should_notify is False
    assert decision.is_new_low is False


def test_evaluate_alert_new_low_does_not_fire_when_threshold_set(alert_with_target_price):
    PriceHistory.objects.create(alert=alert_with_target_price, price=Decimal("2000.00"), currency="BRL")
    # Below the previous low but nowhere near the 1500 target -> no trigger.
    decision = alert_engine.evaluate_alert(alert_with_target_price, Decimal("1900.00"), "BRL")
    assert decision.should_notify is False
    assert decision.is_new_low is False


# -- evaluate_alert: mistake fare --------------------------------------------


def test_evaluate_alert_flags_mistake_fare_regardless_of_threshold(alert_with_target_price):
    for price in ("1000.00", "1000.00", "1000.00"):
        PriceHistory.objects.create(alert=alert_with_target_price, price=Decimal(price), currency="BRL")
    # 400 is <= 50% of the 1000 average, and far above the 1500 target anyway.
    decision = alert_engine.evaluate_alert(alert_with_target_price, Decimal("400.00"), "BRL")
    assert decision.should_notify is True
    assert decision.is_mistake_fare is True


def test_evaluate_alert_does_not_flag_mistake_fare_with_too_little_history(alert_without_threshold):
    PriceHistory.objects.create(alert=alert_without_threshold, price=Decimal("1000.00"), currency="BRL")
    decision = alert_engine.evaluate_alert(alert_without_threshold, Decimal("100.00"), "BRL")
    assert decision.is_mistake_fare is False


# -- should_check_now ---------------------------------------------------------


def test_should_check_now_true_without_prior_history(alert_with_target_price):
    assert alert_engine.should_check_now(alert_with_target_price) is True


def test_should_check_now_false_for_far_alert_checked_recently(telegram_user):
    from django.utils import timezone

    far_date = timezone.localdate() + timedelta(days=200)
    alert = Alert.objects.create(
        user=telegram_user,
        origin="BEL",
        destination="LIS",
        period_type=Alert.PeriodType.FIXED_DATE,
        date_from=far_date,
        trip_type=Alert.TripType.ONE_WAY,
        target_price=Decimal("1500.00"),
    )
    PriceHistory.objects.create(alert=alert, price=Decimal("1000.00"), currency="BRL")

    assert alert_engine.should_check_now(alert, now=timezone.now()) is False


def test_should_check_now_true_for_near_alert_checked_over_an_hour_ago(telegram_user):
    from django.utils import timezone

    near_date = timezone.localdate() + timedelta(days=5)
    alert = Alert.objects.create(
        user=telegram_user,
        origin="BEL",
        destination="LIS",
        period_type=Alert.PeriodType.FIXED_DATE,
        date_from=near_date,
        trip_type=Alert.TripType.ONE_WAY,
        target_price=Decimal("1500.00"),
    )
    PriceHistory.objects.create(alert=alert, price=Decimal("1000.00"), currency="BRL")

    two_hours_later = timezone.now() + timedelta(hours=2)
    assert alert_engine.should_check_now(alert, now=two_hours_later) is True


def test_should_check_now_false_for_near_alert_checked_moments_ago(telegram_user):
    from django.utils import timezone

    near_date = timezone.localdate() + timedelta(days=5)
    alert = Alert.objects.create(
        user=telegram_user,
        origin="BEL",
        destination="LIS",
        period_type=Alert.PeriodType.FIXED_DATE,
        date_from=near_date,
        trip_type=Alert.TripType.ONE_WAY,
        target_price=Decimal("1500.00"),
    )
    PriceHistory.objects.create(alert=alert, price=Decimal("1000.00"), currency="BRL")

    assert alert_engine.should_check_now(alert, now=timezone.now()) is False


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
    assert history.matched_destination == "LIS"
    assert history.matched_departure_date == alert_with_target_price.date_from
    notify_fn.assert_called_once_with(alert_with_target_price, Decimal("1200.0"), "BRL", ANY)


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


def test_check_alert_returns_none_when_all_candidates_error(alert_with_target_price):
    client = MagicMock()
    client.find_cheapest_offer.side_effect = AmadeusError("boom")
    notify_fn = MagicMock()

    decision = alert_engine.check_alert(alert_with_target_price, client=client, notify_fn=notify_fn)

    assert decision is None
    notify_fn.assert_not_called()
    assert PriceHistory.objects.filter(alert=alert_with_target_price).count() == 0


def test_check_alert_scans_every_destination_for_multi_destination_alert(multi_destination_alert):
    client = MagicMock()

    def fake_find_cheapest(origin, destination, **kwargs):
        # LIS is deliberately the cheapest of the bunch.
        price = 500.0 if destination == "LIS" else 2000.0
        return FlightOffer(price=price, currency="BRL", raw={"destination": destination})

    client.find_cheapest_offer.side_effect = fake_find_cheapest

    decision = alert_engine.check_alert(multi_destination_alert, client=client, notify_fn=None)

    assert client.find_cheapest_offer.call_count == len(POPULAR_DESTINATIONS)
    assert decision.matched_destination == "LIS"
    assert decision.price == Decimal("500.0")
    history = PriceHistory.objects.get(alert=multi_destination_alert)
    assert history.matched_destination == "LIS"


def test_check_alert_skips_failing_candidate_and_uses_a_working_one(telegram_user):
    alert = Alert.objects.create(
        user=telegram_user,
        origin="BEL",
        destination="LIS",
        period_type=Alert.PeriodType.FLEXIBLE_WEEKDAY,
        date_from=date(2026, 10, 1),
        date_to=date(2026, 10, 9),
        flexible_weekday=4,
        trip_type=Alert.TripType.ONE_WAY,
        target_price=Decimal("1500.00"),
    )
    client = MagicMock()
    client.find_cheapest_offer.side_effect = [
        AmadeusError("rate limited"),
        FlightOffer(price=900.0, currency="BRL", raw={}),
    ]

    decision = alert_engine.check_alert(alert, client=client, notify_fn=None)

    assert decision is not None
    assert decision.price == Decimal("900.0")


def test_check_alert_passes_airline_and_bag_preferences_to_client(telegram_user):
    alert = Alert.objects.create(
        user=telegram_user,
        origin="BEL",
        destination="LIS",
        period_type=Alert.PeriodType.FIXED_DATE,
        date_from=date(2026, 3, 1),
        trip_type=Alert.TripType.ONE_WAY,
        target_price=Decimal("1500.00"),
        preferred_airlines=["LA", "G3"],
        require_checked_bag=True,
    )
    client = MagicMock()
    client.find_cheapest_offer.return_value = FlightOffer(price=900.0, currency="BRL", raw={})

    alert_engine.check_alert(alert, client=client, notify_fn=None)

    client.find_cheapest_offer.assert_called_once_with(
        origin="BEL",
        destination="LIS",
        departure_date="2026-03-01",
        return_date=None,
        included_airline_codes=["LA", "G3"],
        require_checked_bag=True,
    )


def test_check_alert_computes_round_trip_return_date(telegram_user):
    alert = Alert.objects.create(
        user=telegram_user,
        origin="BEL",
        destination="LIS",
        period_type=Alert.PeriodType.FIXED_DATE,
        date_from=date(2026, 3, 1),
        trip_type=Alert.TripType.ROUND_TRIP,
        trip_duration_days=10,
        target_price=Decimal("1500.00"),
    )
    client = MagicMock()
    client.find_cheapest_offer.return_value = FlightOffer(price=900.0, currency="BRL", raw={})

    alert_engine.check_alert(alert, client=client, notify_fn=None)

    assert client.find_cheapest_offer.call_args.kwargs["return_date"] == "2026-03-11"


# -- weekly summary -----------------------------------------------------------


def test_build_weekly_summary_message_none_without_recent_history(alert_with_target_price):
    from django.utils import timezone

    message = alert_engine.build_weekly_summary_message(alert_with_target_price.user, timezone.now() - timedelta(days=7))
    assert message is None


def test_build_weekly_summary_message_includes_cheapest_recent_price(alert_with_target_price):
    from django.utils import timezone

    PriceHistory.objects.create(alert=alert_with_target_price, price=Decimal("1200.00"), currency="BRL")
    PriceHistory.objects.create(alert=alert_with_target_price, price=Decimal("999.00"), currency="BRL")

    message = alert_engine.build_weekly_summary_message(
        alert_with_target_price.user, timezone.now() - timedelta(days=7)
    )

    assert message is not None
    assert "999.00" in message
    assert "1200.00" not in message


def test_build_weekly_summary_message_ignores_inactive_alerts(alert_with_target_price):
    from django.utils import timezone

    PriceHistory.objects.create(alert=alert_with_target_price, price=Decimal("999.00"), currency="BRL")
    alert_with_target_price.is_active = False
    alert_with_target_price.save()

    message = alert_engine.build_weekly_summary_message(
        alert_with_target_price.user, timezone.now() - timedelta(days=7)
    )
    assert message is None
