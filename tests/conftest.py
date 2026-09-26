from datetime import date
from decimal import Decimal

import pytest

from alerts.models import Alert, TelegramUser


@pytest.fixture
def telegram_user(db):
    return TelegramUser.objects.create(chat_id=123456789, username="tester")


@pytest.fixture
def alert_with_target_price(telegram_user):
    return Alert.objects.create(
        user=telegram_user,
        origin="BEL",
        destination="LIS",
        period_type=Alert.PeriodType.FIXED_DATE,
        date_from=date(2026, 3, 1),
        target_price=Decimal("1500.00"),
    )


@pytest.fixture
def alert_with_drop_percentage(telegram_user):
    return Alert.objects.create(
        user=telegram_user,
        origin="BEL",
        destination="LIS",
        period_type=Alert.PeriodType.FIXED_DATE,
        date_from=date(2026, 3, 1),
        drop_percentage=Decimal("20.00"),
    )
