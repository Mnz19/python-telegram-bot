from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import ConversationHandler

from alerts.bot import conversation
from alerts.bot.commands.alerts import current_price, list_alerts, resume_alert, stop_alert
from alerts.bot.commands.profiles import duplicate_alert, list_profiles
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


async def _run_full_conversation(context, *, destination_text="LIS", period_choice="1",
                                  period_details_text="2026-03-01", trip_choice="1",
                                  trip_duration_text="7", threshold_text="1500",
                                  airline_text="/pular", checked_bag_text="nao",
                                  name_text="/pular"):
    """Drives the whole /novaalerta flow and returns the final state."""
    update = make_update()
    await conversation.start_new_alert(update, context)

    update = make_update(text="GRU")
    await conversation.receive_origin(update, context)

    update = make_update(text=destination_text)
    await conversation.receive_destination(update, context)

    update = make_update(text=period_choice)
    await conversation.receive_period_type(update, context)

    update = make_update(text=period_details_text)
    state = await conversation.receive_period_details(update, context)

    update = make_update(text=trip_choice)
    state = await conversation.receive_trip_type(update, context)

    if state == conversation.TRIP_DURATION:
        update = make_update(text=trip_duration_text)
        state = await conversation.receive_trip_duration(update, context)

    update = make_update(text=threshold_text)
    await conversation.receive_threshold(update, context)

    update = make_update(text=airline_text)
    await conversation.receive_airline_preference(update, context)

    update = make_update(text=checked_bag_text)
    await conversation.receive_checked_bag(update, context)

    update = make_update(text=name_text)
    state = await conversation.receive_name(update, context)
    return state


# -- /novaalerta conversation flow: individual steps -------------------------


@pytest.mark.django_db(transaction=True)
async def test_start_new_alert_resets_state_and_asks_origin():
    context = make_context(user_data={"leftover": "data"})
    update = make_update()

    state = await conversation.start_new_alert(update, context)

    assert state == conversation.ORIGIN
    assert context.user_data == {}


@pytest.mark.django_db(transaction=True)
async def test_receive_origin_accepts_skip_and_uses_default():
    context = make_context()
    update = make_update(text="/pular")

    state = await conversation.receive_origin(update, context)

    assert state == conversation.DESTINATION
    assert context.user_data["origin"] == "BEL"


@pytest.mark.django_db(transaction=True)
async def test_receive_origin_rejects_invalid_code():
    context = make_context()
    update = make_update(text="XX")

    state = await conversation.receive_origin(update, context)

    assert state == conversation.ORIGIN
    update.message.reply_text.assert_awaited_once()


@pytest.mark.django_db(transaction=True)
async def test_receive_destination_skip_enables_multi_destino():
    context = make_context(user_data={"origin": "BEL"})
    update = make_update(text="/pular")

    state = await conversation.receive_destination(update, context)

    assert state == conversation.PERIOD_TYPE
    assert context.user_data["destination"] == ""


@pytest.mark.django_db(transaction=True)
async def test_receive_destination_rejects_invalid_code():
    context = make_context(user_data={"origin": "BEL"})
    update = make_update(text="XX")

    state = await conversation.receive_destination(update, context)

    assert state == conversation.DESTINATION


@pytest.mark.django_db(transaction=True)
async def test_receive_period_type_flexible_weekday_prompts_correctly():
    context = make_context()
    update = make_update(text="4")

    state = await conversation.receive_period_type(update, context)

    assert state == conversation.PERIOD_DETAILS
    assert context.user_data["period_type"] == Alert.PeriodType.FLEXIBLE_WEEKDAY


@pytest.mark.django_db(transaction=True)
async def test_receive_period_type_rejects_invalid_choice():
    context = make_context()
    update = make_update(text="9")

    state = await conversation.receive_period_type(update, context)

    assert state == conversation.PERIOD_TYPE


@pytest.mark.django_db(transaction=True)
async def test_receive_period_details_date_range_parsing():
    context = make_context(user_data={"period_type": Alert.PeriodType.DATE_RANGE})
    update = make_update(text="2026-01-10 a 2026-01-20")

    state = await conversation.receive_period_details(update, context)

    assert state == conversation.TRIP_TYPE
    assert context.user_data["date_from"].isoformat() == "2026-01-10"
    assert context.user_data["date_to"].isoformat() == "2026-01-20"


@pytest.mark.django_db(transaction=True)
async def test_receive_period_details_next_months_parsing():
    context = make_context(user_data={"period_type": Alert.PeriodType.NEXT_MONTHS})
    update = make_update(text="3")

    state = await conversation.receive_period_details(update, context)

    assert state == conversation.TRIP_TYPE
    assert context.user_data["months_ahead"] == 3


@pytest.mark.django_db(transaction=True)
async def test_receive_period_details_flexible_weekday_parsing():
    context = make_context(user_data={"period_type": Alert.PeriodType.FLEXIBLE_WEEKDAY})
    update = make_update(text="sexta 2026-10-01 a 2026-12-31")

    state = await conversation.receive_period_details(update, context)

    assert state == conversation.TRIP_TYPE
    assert context.user_data["flexible_weekday"] == 4
    assert context.user_data["date_from"].isoformat() == "2026-10-01"
    assert context.user_data["date_to"].isoformat() == "2026-12-31"


@pytest.mark.django_db(transaction=True)
async def test_receive_period_details_flexible_weekday_accepts_accents():
    context = make_context(user_data={"period_type": Alert.PeriodType.FLEXIBLE_WEEKDAY})
    update = make_update(text="sábado 2026-10-01 a 2026-12-31")

    state = await conversation.receive_period_details(update, context)

    assert state == conversation.TRIP_TYPE
    assert context.user_data["flexible_weekday"] == 5


@pytest.mark.django_db(transaction=True)
async def test_receive_period_details_rejects_invalid_date_format():
    context = make_context(user_data={"period_type": Alert.PeriodType.FIXED_DATE})
    update = make_update(text="not-a-date")

    state = await conversation.receive_period_details(update, context)

    assert state == conversation.PERIOD_DETAILS


@pytest.mark.django_db(transaction=True)
async def test_receive_trip_type_round_trip_asks_duration():
    context = make_context()
    update = make_update(text="1")

    state = await conversation.receive_trip_type(update, context)

    assert state == conversation.TRIP_DURATION
    assert context.user_data["trip_type"] == Alert.TripType.ROUND_TRIP


@pytest.mark.django_db(transaction=True)
async def test_receive_trip_type_one_way_skips_duration():
    context = make_context()
    update = make_update(text="2")

    state = await conversation.receive_trip_type(update, context)

    assert state == conversation.THRESHOLD
    assert context.user_data["trip_type"] == Alert.TripType.ONE_WAY


@pytest.mark.django_db(transaction=True)
async def test_receive_trip_duration_rejects_non_positive():
    context = make_context()
    update = make_update(text="0")

    state = await conversation.receive_trip_duration(update, context)

    assert state == conversation.TRIP_DURATION


@pytest.mark.django_db(transaction=True)
async def test_receive_threshold_accepts_skip():
    context = make_context()
    update = make_update(text="/pular")

    state = await conversation.receive_threshold(update, context)

    assert state == conversation.AIRLINE_PREF
    assert "target_price" not in context.user_data
    assert "drop_percentage" not in context.user_data


@pytest.mark.django_db(transaction=True)
async def test_receive_threshold_rejects_invalid_value():
    context = make_context()
    update = make_update(text="not-a-number")

    state = await conversation.receive_threshold(update, context)

    assert state == conversation.THRESHOLD


@pytest.mark.django_db(transaction=True)
async def test_receive_airline_preference_parses_codes():
    context = make_context()
    update = make_update(text="LA, g3")

    state = await conversation.receive_airline_preference(update, context)

    assert state == conversation.CHECKED_BAG
    assert context.user_data["preferred_airlines"] == ["LA", "G3"]


@pytest.mark.django_db(transaction=True)
async def test_receive_checked_bag_accepts_sim_and_nao():
    context = make_context()

    state = await conversation.receive_checked_bag(make_update(text="sim"), context)
    assert state == conversation.NAME
    assert context.user_data["require_checked_bag"] is True

    state = await conversation.receive_checked_bag(make_update(text="não"), context)
    assert state == conversation.NAME
    assert context.user_data["require_checked_bag"] is False


@pytest.mark.django_db(transaction=True)
async def test_receive_checked_bag_rejects_unrecognized_answer():
    context = make_context()
    update = make_update(text="talvez")

    state = await conversation.receive_checked_bag(update, context)

    assert state == conversation.CHECKED_BAG


@pytest.mark.django_db(transaction=True)
async def test_cancel_clears_user_data_and_ends_conversation():
    context = make_context(user_data={"origin": "GRU"})
    update = make_update()

    state = await conversation.cancel(update, context)

    assert state == ConversationHandler.END
    assert context.user_data == {}


# -- /novaalerta conversation flow: end-to-end --------------------------------


@pytest.mark.django_db(transaction=True)
async def test_full_conversation_creates_round_trip_alert_with_all_fields():
    context = make_context()

    state = await _run_full_conversation(
        context,
        destination_text="LIS",
        period_choice="1",
        period_details_text="2026-03-01",
        trip_choice="1",
        trip_duration_text="7",
        threshold_text="1500",
        airline_text="LA,G3",
        checked_bag_text="sim",
        name_text="Rio de ferias",
    )

    assert state == ConversationHandler.END
    alert = await Alert.objects.aget(origin="GRU", destination="LIS")
    assert alert.target_price == Decimal("1500")
    assert alert.trip_type == Alert.TripType.ROUND_TRIP
    assert alert.trip_duration_days == 7
    assert alert.preferred_airlines == ["LA", "G3"]
    assert alert.require_checked_bag is True
    assert alert.name == "Rio de ferias"

    user = await TelegramUser.objects.aget(chat_id=123456789)
    assert user.username == "tester"


@pytest.mark.django_db(transaction=True)
async def test_full_conversation_multi_destino_one_way_no_threshold():
    context = make_context()

    state = await _run_full_conversation(
        context,
        destination_text="/pular",
        trip_choice="2",
        threshold_text="/pular",
    )

    assert state == ConversationHandler.END
    alert = await Alert.objects.aget(origin="GRU", destination="")
    assert alert.is_multi_destination is True
    assert alert.trip_type == Alert.TripType.ONE_WAY
    assert alert.trip_duration_days is None
    assert alert.target_price is None
    assert alert.drop_percentage is None


@pytest.mark.django_db(transaction=True)
async def test_full_conversation_with_drop_percentage_threshold():
    context = make_context()

    await _run_full_conversation(context, threshold_text="20%", trip_choice="2")

    alert = await Alert.objects.aget(origin="GRU", destination="LIS")
    assert alert.drop_percentage == Decimal("20")
    assert alert.target_price is None


# -- /listaralertas, /pararalerta, /retomar, /precoatual ----------------------


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
async def test_resume_alert_reactivates_paused_alert(alert_with_target_price):
    alert_with_target_price.is_active = False
    await alert_with_target_price.asave()

    update = make_update(args=[str(alert_with_target_price.id)])
    context = make_context(args=[str(alert_with_target_price.id)])

    await resume_alert(update, context)

    await alert_with_target_price.arefresh_from_db()
    assert alert_with_target_price.is_active is True


@pytest.mark.django_db(transaction=True)
async def test_resume_alert_rejects_missing_id():
    update = make_update()
    context = make_context(args=[])

    await resume_alert(update, context)

    message = update.message.reply_text.await_args.args[0]
    assert "Uso:" in message


@pytest.mark.django_db(transaction=True)
async def test_current_price_is_rate_limited(alert_with_target_price, monkeypatch):
    monkeypatch.setattr(
        "alerts.bot.commands.alerts._check_alert_async", AsyncMock(return_value=None)
    )

    update = make_update(args=[str(alert_with_target_price.id)])
    context = make_context(args=[str(alert_with_target_price.id)])
    await current_price(update, context)

    update2 = make_update(args=[str(alert_with_target_price.id)])
    context2 = make_context(args=[str(alert_with_target_price.id)])
    await current_price(update2, context2)

    message = update2.message.reply_text.await_args.args[0]
    assert "recentemente" in message

    from django.core.cache import cache
    cache.clear()


# -- /perfis, /duplicar --------------------------------------------------------


@pytest.mark.django_db(transaction=True)
async def test_list_profiles_shows_only_named_alerts(telegram_user, alert_with_target_price):
    alert_with_target_price.name = "Rio de ferias"
    await alert_with_target_price.asave()

    update = make_update()
    context = make_context()
    await list_profiles(update, context)

    message = update.message.reply_text.await_args.args[0]
    assert "Rio de ferias" in message


@pytest.mark.django_db(transaction=True)
async def test_list_profiles_empty_message_when_no_named_alerts(telegram_user, alert_with_target_price):
    update = make_update()
    context = make_context()

    await list_profiles(update, context)

    message = update.message.reply_text.await_args.args[0]
    assert "não tem alertas nomeados" in message


@pytest.mark.django_db(transaction=True)
async def test_duplicate_alert_clones_configuration(alert_with_target_price):
    alert_with_target_price.name = "Rio de ferias"
    await alert_with_target_price.asave()

    update = make_update(args=[str(alert_with_target_price.id)])
    context = make_context(args=[str(alert_with_target_price.id)])

    await duplicate_alert(update, context)

    clones = [a async for a in Alert.objects.filter(name="Rio de ferias").exclude(id=alert_with_target_price.id)]
    assert len(clones) == 1
    clone = clones[0]
    assert clone.origin == alert_with_target_price.origin
    assert clone.destination == alert_with_target_price.destination
    assert clone.target_price == alert_with_target_price.target_price
    assert clone.is_active is True


@pytest.mark.django_db(transaction=True)
async def test_duplicate_alert_rejects_missing_id():
    update = make_update()
    context = make_context(args=[])

    await duplicate_alert(update, context)

    message = update.message.reply_text.await_args.args[0]
    assert "Uso:" in message


@pytest.mark.django_db(transaction=True)
async def test_duplicate_alert_not_found_for_other_user(alert_with_target_price):
    other_user = await TelegramUser.objects.acreate(chat_id=999999, username="other")
    update = make_update(chat_id=other_user.chat_id, args=[str(alert_with_target_price.id)])
    context = make_context(args=[str(alert_with_target_price.id)])

    await duplicate_alert(update, context)

    message = update.message.reply_text.await_args.args[0]
    assert "não encontrado" in message
