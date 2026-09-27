"""ConversationHandler flow for /novaalerta.

Steps: origin -> destination -> period_type -> period details -> trip_type
-> [trip_duration if round trip] -> threshold -> airline preference ->
checked bag -> name -> save.
"""

from __future__ import annotations

import logging
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from ..models import Alert, TelegramUser

logger = logging.getLogger(__name__)

(
    ORIGIN,
    DESTINATION,
    PERIOD_TYPE,
    PERIOD_DETAILS,
    TRIP_TYPE,
    TRIP_DURATION,
    THRESHOLD,
    AIRLINE_PREF,
    CHECKED_BAG,
    NAME,
) = range(10)

PERIOD_LABELS = {
    "1": Alert.PeriodType.FIXED_DATE,
    "2": Alert.PeriodType.DATE_RANGE,
    "3": Alert.PeriodType.NEXT_MONTHS,
    "4": Alert.PeriodType.FLEXIBLE_WEEKDAY,
}

TRIP_TYPE_LABELS = {
    "1": Alert.TripType.ROUND_TRIP,
    "2": Alert.TripType.ONE_WAY,
}

DATE_FORMAT = "%Y-%m-%d"

WEEKDAY_NAME_TO_INT = {
    "SEGUNDA": 0,
    "TERCA": 1,
    "QUARTA": 2,
    "QUINTA": 3,
    "SEXTA": 4,
    "SABADO": 5,
    "DOMINGO": 6,
}


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char))


async def start_new_alert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    default_origin = settings.DEFAULT_ORIGIN_IATA
    await update.message.reply_text(
        "Vamos criar um novo alerta!\n\n"
        f"Qual o aeroporto de origem? (3 letras, ex: {default_origin})\n"
        "Ou envie /pular para usar o padrão.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ORIGIN


async def receive_origin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().upper()
    if text == "/PULAR":
        origin = settings.DEFAULT_ORIGIN_IATA
    else:
        origin = text

    if len(origin) != 3 or not origin.isalpha():
        await update.message.reply_text("Código inválido. Envie um código IATA de 3 letras (ex: GRU).")
        return ORIGIN

    context.user_data["origin"] = origin
    await update.message.reply_text(
        "Qual o aeroporto de destino? (3 letras, ex: LIS)\n"
        "Ou envie /pular para \"me surpreenda\" — eu varro vários destinos populares "
        "e aviso qual ficou mais barato."
    )
    return DESTINATION


async def receive_destination(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().upper()

    if text == "/PULAR":
        context.user_data["destination"] = ""
        await update.message.reply_text(
            "Combinado, vou varrer vários destinos populares. "
            "Recomendo definir um preço-alvo mais adiante para esse tipo de alerta."
        )
    else:
        if len(text) != 3 or not text.isalpha():
            await update.message.reply_text("Código inválido. Envie um código IATA de 3 letras (ex: LIS) ou /pular.")
            return DESTINATION
        context.user_data["destination"] = text

    keyboard = ReplyKeyboardMarkup(
        [["1 - Data fixa"], ["2 - Faixa de datas"], ["3 - Próximos N meses"], ["4 - Dia da semana flexível"]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "Qual o tipo de período?\n"
        "1 - Data fixa\n"
        "2 - Faixa de datas\n"
        "3 - Próximos N meses\n"
        "4 - Dia da semana flexível (ex: toda sexta de um período)",
        reply_markup=keyboard,
    )
    return PERIOD_TYPE


async def receive_period_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text.strip()[:1]
    period_type = PERIOD_LABELS.get(choice)
    if not period_type:
        await update.message.reply_text("Opção inválida. Responda com 1, 2, 3 ou 4.")
        return PERIOD_TYPE

    context.user_data["period_type"] = period_type

    if period_type == Alert.PeriodType.FIXED_DATE:
        await update.message.reply_text(
            "Informe a data de ida (AAAA-MM-DD):", reply_markup=ReplyKeyboardRemove()
        )
    elif period_type == Alert.PeriodType.DATE_RANGE:
        await update.message.reply_text(
            "Informe a faixa de datas no formato AAAA-MM-DD a AAAA-MM-DD "
            "(ex: 2026-01-10 a 2026-01-20):",
            reply_markup=ReplyKeyboardRemove(),
        )
    elif period_type == Alert.PeriodType.FLEXIBLE_WEEKDAY:
        await update.message.reply_text(
            "Informe o dia da semana e o período no formato "
            "'DIA AAAA-MM-DD a AAAA-MM-DD' (ex: sexta 2026-10-01 a 2026-12-31):",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await update.message.reply_text(
            "Quantos meses a partir de hoje? (ex: 3)", reply_markup=ReplyKeyboardRemove()
        )
    return PERIOD_DETAILS


async def receive_period_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    period_type = context.user_data["period_type"]
    text = update.message.text.strip()

    try:
        if period_type == Alert.PeriodType.FIXED_DATE:
            context.user_data["date_from"] = datetime.strptime(text, DATE_FORMAT).date()
        elif period_type == Alert.PeriodType.DATE_RANGE:
            date_from_str, date_to_str = [part.strip() for part in text.split(" a ")]
            context.user_data["date_from"] = datetime.strptime(date_from_str, DATE_FORMAT).date()
            context.user_data["date_to"] = datetime.strptime(date_to_str, DATE_FORMAT).date()
        elif period_type == Alert.PeriodType.FLEXIBLE_WEEKDAY:
            weekday_word, _, rest = text.partition(" ")
            weekday_key = _strip_accents(weekday_word).upper()
            if weekday_key not in WEEKDAY_NAME_TO_INT:
                raise ValueError
            date_from_str, date_to_str = [part.strip() for part in rest.split(" a ")]
            context.user_data["flexible_weekday"] = WEEKDAY_NAME_TO_INT[weekday_key]
            context.user_data["date_from"] = datetime.strptime(date_from_str, DATE_FORMAT).date()
            context.user_data["date_to"] = datetime.strptime(date_to_str, DATE_FORMAT).date()
        else:
            months = int(text)
            if months <= 0:
                raise ValueError
            context.user_data["months_ahead"] = months
    except (ValueError, IndexError):
        await update.message.reply_text(
            "Não entendi o período informado. Tente novamente no formato solicitado."
        )
        return PERIOD_DETAILS

    keyboard = ReplyKeyboardMarkup([["1 - Ida e volta"], ["2 - Só ida"]], one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text(
        "É ida e volta ou só ida?\n1 - Ida e volta\n2 - Só ida", reply_markup=keyboard
    )
    return TRIP_TYPE


async def receive_trip_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text.strip()[:1]
    trip_type = TRIP_TYPE_LABELS.get(choice)
    if not trip_type:
        await update.message.reply_text("Opção inválida. Responda com 1 ou 2.")
        return TRIP_TYPE

    context.user_data["trip_type"] = trip_type

    if trip_type == Alert.TripType.ROUND_TRIP:
        await update.message.reply_text("Quantas noites de viagem?", reply_markup=ReplyKeyboardRemove())
        return TRIP_DURATION

    await update.message.reply_text(
        "Agora o gatilho do alerta. Envie um preço-alvo em R$ (ex: 1500), uma "
        "porcentagem de queda (ex: 20%), ou /pular para monitorar só o menor "
        "preço histórico e possíveis erros de tarifa.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return THRESHOLD


async def receive_trip_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        nights = int(text)
        if nights <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Informe um número de noites válido (ex: 7).")
        return TRIP_DURATION

    context.user_data["trip_duration_days"] = nights
    await update.message.reply_text(
        "Agora o gatilho do alerta. Envie um preço-alvo em R$ (ex: 1500), uma "
        "porcentagem de queda (ex: 20%), ou /pular para monitorar só o menor "
        "preço histórico e possíveis erros de tarifa."
    )
    return THRESHOLD


async def receive_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()

    if text.upper() != "/PULAR":
        text = text.replace(",", ".")
        try:
            if text.endswith("%"):
                context.user_data["drop_percentage"] = Decimal(text[:-1].strip())
            else:
                context.user_data["target_price"] = Decimal(text)
        except InvalidOperation:
            await update.message.reply_text(
                "Valor inválido. Envie um número (ex: 1500), uma porcentagem (ex: 20%) ou /pular."
            )
            return THRESHOLD

    await update.message.reply_text(
        "Prefere alguma(s) companhia(s) aérea(s)? Envie os códigos IATA separados "
        "por vírgula (ex: LA,G3) ou /pular."
    )
    return AIRLINE_PREF


async def receive_airline_preference(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text.upper() != "/PULAR":
        codes = [code.strip().upper() for code in text.split(",") if code.strip()]
        context.user_data["preferred_airlines"] = codes

    await update.message.reply_text("Precisa de bagagem despachada incluída? (sim/não)")
    return CHECKED_BAG


async def receive_checked_bag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = _strip_accents(update.message.text.strip()).upper()
    if text in ("SIM", "S", "YES", "Y"):
        context.user_data["require_checked_bag"] = True
    elif text in ("NAO", "N", "NO"):
        context.user_data["require_checked_bag"] = False
    else:
        await update.message.reply_text("Responda com 'sim' ou 'não'.")
        return CHECKED_BAG

    await update.message.reply_text(
        "Quer dar um nome a esse alerta, para reutilizar depois (ex: 'Rio de férias')? "
        "Envie o nome ou /pular."
    )
    return NAME


async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text.upper() != "/PULAR":
        context.user_data["name"] = text[:100]

    user, _ = await TelegramUser.objects.aget_or_create(
        chat_id=update.effective_chat.id,
        defaults={"username": update.effective_user.username or ""},
    )

    data = context.user_data
    alert = Alert(
        user=user,
        name=data.get("name", ""),
        origin=data["origin"],
        destination=data.get("destination", ""),
        period_type=data["period_type"],
        date_from=data.get("date_from"),
        date_to=data.get("date_to"),
        months_ahead=data.get("months_ahead"),
        flexible_weekday=data.get("flexible_weekday"),
        trip_type=data["trip_type"],
        trip_duration_days=data.get("trip_duration_days"),
        target_price=data.get("target_price"),
        drop_percentage=data.get("drop_percentage"),
        preferred_airlines=data.get("preferred_airlines", []),
        require_checked_bag=data.get("require_checked_bag", False),
    )
    try:
        alert.clean()
    except ValidationError as exc:
        await update.message.reply_text(f"Não foi possível criar o alerta: {exc.message}")
        return ConversationHandler.END

    await alert.asave()

    destination_label = alert.destination or "vários destinos"
    await update.message.reply_text(
        f"Alerta #{alert.id} criado! {alert.origin} → {destination_label}.\n"
        "Você será avisado por aqui quando o preço cair. Use /listaralertas para ver todos."
    )
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Criação de alerta cancelada.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def build_conversation_states() -> dict:
    text_filter = filters.TEXT & ~filters.COMMAND
    # Accepts plain text as usual, plus the literal "/pular" command (used to
    # skip an optional step) without opening the door to every other command
    # (e.g. /cancelar must still fall through to the ConversationHandler's
    # fallback instead of being swallowed here).
    accept_skip = filters.Regex(r"(?i)^/pular$") | text_filter
    return {
        ORIGIN: [MessageHandler(accept_skip, receive_origin)],
        DESTINATION: [MessageHandler(accept_skip, receive_destination)],
        PERIOD_TYPE: [MessageHandler(text_filter, receive_period_type)],
        PERIOD_DETAILS: [MessageHandler(text_filter, receive_period_details)],
        TRIP_TYPE: [MessageHandler(text_filter, receive_trip_type)],
        TRIP_DURATION: [MessageHandler(text_filter, receive_trip_duration)],
        THRESHOLD: [MessageHandler(accept_skip, receive_threshold)],
        AIRLINE_PREF: [MessageHandler(accept_skip, receive_airline_preference)],
        CHECKED_BAG: [MessageHandler(text_filter, receive_checked_bag)],
        NAME: [MessageHandler(accept_skip, receive_name)],
    }
