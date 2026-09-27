"""Named alerts act as reusable "profiles" (e.g. "Rio de férias") that can
be cloned into a fresh alert without going through /novaalerta from scratch.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from ...models import Alert, TelegramUser


async def list_profiles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await TelegramUser.objects.aget(chat_id=update.effective_chat.id)
    except TelegramUser.DoesNotExist:
        await update.message.reply_text("Você ainda não tem alertas nomeados.")
        return

    lines = []
    async for alert in user.alerts.exclude(name="").order_by("name").aiterator():
        destination = alert.destination or "vários destinos"
        status = "ativo" if alert.is_active else "pausado"
        lines.append(f"#{alert.id} \"{alert.name}\" ({status}) — {alert.origin} → {destination}")

    if not lines:
        await update.message.reply_text(
            "Você não tem alertas nomeados ainda. Dê um nome a um alerta em /novaalerta "
            "para poder reutilizá-lo depois com /duplicar."
        )
        return

    await update.message.reply_text(
        "Seus alertas nomeados (use /duplicar <id> para reutilizar um deles):\n\n" + "\n".join(lines)
    )


async def duplicate_alert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Uso: /duplicar <id>")
        return

    try:
        alert_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("O id do alerta deve ser um número. Ex: /duplicar 3")
        return

    try:
        source = await Alert.objects.aget(id=alert_id, user__chat_id=update.effective_chat.id)
    except Alert.DoesNotExist:
        await update.message.reply_text("Alerta não encontrado.")
        return

    clone = Alert(
        user_id=source.user_id,
        name=source.name,
        origin=source.origin,
        destination=source.destination,
        period_type=source.period_type,
        date_from=source.date_from,
        date_to=source.date_to,
        months_ahead=source.months_ahead,
        flexible_weekday=source.flexible_weekday,
        trip_type=source.trip_type,
        trip_duration_days=source.trip_duration_days,
        target_price=source.target_price,
        drop_percentage=source.drop_percentage,
        preferred_airlines=source.preferred_airlines,
        require_checked_bag=source.require_checked_bag,
        is_active=True,
    )
    await clone.asave()

    await update.message.reply_text(
        f"Alerta #{clone.id} criado a partir do #{source.id} (mesma configuração, já ativo).\n"
        "Use /pararalerta ou /retomar para ajustar o status, ou crie um novo do zero com /novaalerta "
        "se quiser mudar as datas."
    )
