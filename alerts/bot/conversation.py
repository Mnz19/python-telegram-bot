"""ConversationHandler flow for /novaalerta.

Steps: origin -> destination -> period_type -> period details -> threshold.
"""

from __future__ import annotations

import logging
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

ORIGIN, DESTINATION, PERIOD_TYPE, PERIOD_DETAILS, THRESHOLD = range(5)

PERIOD_LABELS = {
    "1": Alert.PeriodType.FIXED_DATE,
    "2": Alert.PeriodType.DATE_RANGE,
    "3": Alert.PeriodType.NEXT_MONTHS,
}

DATE_FORMAT = "%Y-%m-%d"


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
    await update.message.reply_text("Qual o aeroporto de destino? (3 letras, ex: LIS)")
    return DESTINATION


async def receive_destination(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    destination = update.message.text.strip().upper()
    if len(destination) != 3 or not destination.isalpha():
        await update.message.reply_text("Código inválido. Envie um código IATA de 3 letras (ex: LIS).")
        return DESTINATION

    context.user_data["destination"] = destination
    keyboard = ReplyKeyboardMarkup(
        [["1 - Data fixa"], ["2 - Faixa de datas"], ["3 - Próximos N meses"]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "Qual o tipo de período?\n"
        "1 - Data fixa\n"
        "2 - Faixa de datas\n"
        "3 - Próximos N meses",
        reply_markup=keyboard,
    )
    return PERIOD_TYPE


async def receive_period_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text.strip()[:1]
    period_type = PERIOD_LABELS.get(choice)
    if not period_type:
        await update.message.reply_text("Opção inválida. Responda com 1, 2 ou 3.")
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

    await update.message.reply_text(
        "Agora o gatilho do alerta. Envie um preço-alvo em R$ (ex: 1500) "
        "ou uma porcentagem de queda (ex: 20%)."
    )
    return THRESHOLD


async def receive_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().replace(",", ".")
    target_price = None
    drop_percentage = None

    try:
        if text.endswith("%"):
            drop_percentage = Decimal(text[:-1].strip())
        else:
            target_price = Decimal(text)
    except InvalidOperation:
        await update.message.reply_text(
            "Valor inválido. Envie um número (ex: 1500) ou uma porcentagem (ex: 20%)."
        )
        return THRESHOLD

    user, _ = await TelegramUser.objects.aget_or_create(
        chat_id=update.effective_chat.id,
        defaults={"username": update.effective_user.username or ""},
    )

    data = context.user_data
    alert = Alert(
        user=user,
        origin=data["origin"],
        destination=data["destination"],
        period_type=data["period_type"],
        date_from=data.get("date_from"),
        date_to=data.get("date_to"),
        months_ahead=data.get("months_ahead"),
        target_price=target_price,
        drop_percentage=drop_percentage,
    )
    try:
        alert.clean()
    except ValidationError as exc:
        await update.message.reply_text(f"Não foi possível criar o alerta: {exc.message}")
        return ConversationHandler.END

    await alert.asave()

    await update.message.reply_text(
        f"Alerta #{alert.id} criado! {alert.origin} → {alert.destination}.\n"
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
    return {
        ORIGIN: [MessageHandler(text_filter, receive_origin)],
        DESTINATION: [MessageHandler(text_filter, receive_destination)],
        PERIOD_TYPE: [MessageHandler(text_filter, receive_period_type)],
        PERIOD_DETAILS: [MessageHandler(text_filter, receive_period_details)],
        THRESHOLD: [MessageHandler(text_filter, receive_threshold)],
    }
