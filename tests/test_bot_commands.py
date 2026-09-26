from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import ConversationHandler

from alerts.bot import conversation
from alerts.bot.commands.alerts import current_price, list_alerts, stop_alert
from alerts.models import Alert, PriceHistory, TelegramUser


def make_update(text=None, args=None, chat_id=123456789, username="tester"):
    update = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.effective_chat.id = chat_id
    update.effective_user.username = username
    return update


def make_context(user_data=None, args=None):
    context = MagicMock()
    context.user_data = user_data if user_data is not None else {}
    context.args = args or []
    return context


# -- /novaalerta conversation flow ------------------------------------------


@pytest.mark.django_db(transaction=True)
async def test_full_conversation_creates_alert_with_target_price():
    context = make_context()

    update = make_update()
    state = await conversation.start_new_alert(update, context)
    assert state == conversation.ORIGIN

    update = make_update(text="GRU")
    state = await conversation.receive_origin(update, context)
    assert state == conversation.DESTINATION
    assert context.user_data["origin"] == "GRU"

    update = make_update(text="LIS")
    state = await conversation.receive_destination(update, context)
    assert state == conversation.PERIOD_TYPE
    assert context.user_data["destination"] == "LIS"

    update = make_update(text="1")
    state = await conversation.receive_period_type(update, context)
    assert state == conversation.PERIOD_DETAILS
    assert context.user_data["period_type"] == Alert.PeriodType.FIXED_DATE

    update = make_update(text="2026-03-01")
    state = await conversation.receive_period_details(update, context)
    assert state == conversation.THRESHOLD

    update = make_update(text="1500")
    state = await conversation.receive_threshold(update, context)
    assert state == ConversationHandler.END

    alert = await Alert.objects.aget(origin="GRU", destination="LIS")
    assert alert.target_price == Decimal("1500")
    assert alert.drop_percentage is None
    assert alert.date_from.isoformat() == "2026-03-01"

    user = await TelegramUser.objects.aget(chat_id=123456789)
    assert user.username == "tester"


@pytest.mark.django_db(transaction=True)
async def test_conversation_with_drop_percentage_threshold():
    context = make_context(
        user_data={"origin": "BEL", "destination": "LIS", "period_type": Alert.PeriodType.FIXED_DATE, "date_from": "2026-03-01"}
    )
    update = make_update(text="20%")

    state = await conversation.receive_threshold(update, context)

    assert state == ConversationHandler.END
    alert = await Alert.objects.aget(origin="BEL", destination="LIS")
    assert alert.drop_percentage == Decimal("20")
    assert alert.target_price is None


@pytest.mark.django_db(transaction=True)
async def test_conversation_date_range_parsing():
    context = make_context(user_data={"period_type": Alert.PeriodType.DATE_RANGE})
    update = make_update(text="2026-01-10 a 2026-01-20")

    state = await conversation.receive_period_details(update, context)

    assert state == conversation.THRESHOLD
    assert context.user_data["date_from"].isoformat() == "2026-01-10"
    assert context.user_data["date_to"].isoformat() == "2026-01-20"


@pytest.mark.django_db(transaction=True)
async def test_conversation_next_months_parsing():
    context = make_context(user_data={"period_type": Alert.PeriodType.NEXT_MONTHS})
    update = make_update(text="3")

    state = await conversation.receive_period_details(update, context)

    assert state == conversation.THRESHOLD
    assert context.user_data["months_ahead"] == 3


@pytest.mark.django_db(transaction=True)
async def test_conversation_rejects_invalid_origin_code():
    context = make_context()
    update = make_update(text="XX")

    state = await conversation.receive_origin(update, context)

    assert state == conversation.ORIGIN
    update.message.reply_text.assert_awaited_once()


@pytest.mark.django_db(transaction=True)
async def test_conversation_rejects_invalid_date_format():
    context = make_context(user_data={"period_type": Alert.PeriodType.FIXED_DATE})
    update = make_update(text="not-a-date")

    state = await conversation.receive_period_details(update, context)

    assert state == conversation.PERIOD_DETAILS


@pytest.mark.django_db(transaction=True)
async def test_conversation_rejects_invalid_threshold_value():
    context = make_context(user_data={"origin": "BEL", "destination": "LIS", "period_type": Alert.PeriodType.FIXED_DATE, "date_from": "2026-03-01"})
    update = make_update(text="not-a-number")

    state = await conversation.receive_threshold(update, context)

    assert state == conversation.THRESHOLD
    assert not await Alert.objects.aexists()


@pytest.mark.django_db(transaction=True)
async def test_cancel_clears_user_data_and_ends_conversation():
    context = make_context(user_data={"origin": "GRU"})
    update = make_update()

    state = await conversation.cancel(update, context)

    assert state == ConversationHandler.END
    assert context.user_data == {}


# -- /listaralertas, /pararalerta, /precoatual --------------------------------


@pytest.mark.django_db(transaction=True)
async def test_list_alerts_shows_active_alerts(telegram_user, alert_with_target_price):
    update = make_update()
    context = make_context()

    await list_alerts(update, context)

    update.message.reply_text.assert_awaited_once()
    message = update.message.reply_text.await_args.args[0]
    assert "BEL" in message
    assert "LIS" in message


@pytest.mark.django_db(transaction=True)
async def test_list_alerts_no_alerts_message():
    update = make_update()
    context = make_context()

    await list_alerts(update, context)

    message = update.message.reply_text.await_args.args[0]
    assert "não tem alertas" in message


@pytest.mark.django_db(transaction=True)
async def test_stop_alert_deactivates_own_alert(alert_with_target_price):
    update = make_update(args=[str(alert_with_target_price.id)])
    context = make_context(args=[str(alert_with_target_price.id)])

    await stop_alert(update, context)

    await alert_with_target_price.arefresh_from_db()
    assert alert_with_target_price.is_active is False


@pytest.mark.django_db(transaction=True)
async def test_stop_alert_rejects_missing_id():
    update = make_update()
    context = make_context(args=[])

    await stop_alert(update, context)

    message = update.message.reply_text.await_args.args[0]
    assert "Uso:" in message


@pytest.mark.django_db(transaction=True)
async def test_stop_alert_does_not_affect_other_users_alert(alert_with_target_price):
    other_user = await TelegramUser.objects.acreate(chat_id=999999, username="other")
    update = make_update(chat_id=other_user.chat_id)
    context = make_context(args=[str(alert_with_target_price.id)])

    await stop_alert(update, context)

    await alert_with_target_price.arefresh_from_db()
    assert alert_with_target_price.is_active is True
    message = update.message.reply_text.await_args.args[0]
    assert "não encontrado" in message


@pytest.mark.django_db(transaction=True)
async def test_current_price_is_rate_limited(alert_with_target_price, monkeypatch):
    from django.core.cache import cache

    from alerts.services.amadeus_client import FlightOffer

    mock_check = MagicMock(return_value=None)
    monkeypatch.setattr("alerts.bot.commands.alerts._check_alert_async", AsyncMock(
        return_value=None
    ))

    update = make_update(args=[str(alert_with_target_price.id)])
    context = make_context(args=[str(alert_with_target_price.id)])

    await current_price(update, context)
    first_call_count = update.message.reply_text.await_count

    update2 = make_update(args=[str(alert_with_target_price.id)])
    context2 = make_context(args=[str(alert_with_target_price.id)])
    await current_price(update2, context2)

    message = update2.message.reply_text.await_args.args[0]
    assert "recentemente" in message

    cache.clear()
