from decimal import Decimal

import pytest

from alerts.models import PriceHistory
from alerts.services import chart


@pytest.mark.django_db
def test_render_price_history_png_returns_none_with_too_little_history(alert_with_target_price):
    PriceHistory.objects.create(alert=alert_with_target_price, price=Decimal("1000.00"), currency="BRL")

    assert chart.render_price_history_png(alert_with_target_price) is None


@pytest.mark.django_db
def test_render_price_history_png_returns_png_bytes_with_enough_history(alert_with_target_price):
    PriceHistory.objects.create(alert=alert_with_target_price, price=Decimal("1000.00"), currency="BRL")
    PriceHistory.objects.create(alert=alert_with_target_price, price=Decimal("900.00"), currency="BRL")

    png_bytes = chart.render_price_history_png(alert_with_target_price)

    assert png_bytes is not None
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
