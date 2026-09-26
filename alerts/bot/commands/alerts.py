from __future__ import annotations

from django.core.cache import cache
from telegram import Update
from telegram.ext import ContextTypes

from ...models import Alert, PriceHistory, TelegramUser
from ...services import alert_engine
from ...services.amadeus_client import AmadeusClient, AmadeusError

PRECOATUAL_RATE_LIMIT_SECONDS = 300


def _format_alert_line(alert: Alert, last_price: PriceHistory | None) -> str:
    threshold = f"R$ {alert.target_price}" if alert.target_price else f"{alert.drop_percentage}% de queda"
    last_price_text = f"último preço: {last_price.price} {last_price.currency}" if last_price else "sem checagens ainda"
    return f"#{alert.id} {alert.origin} → {alert.destination} | alvo: {threshold} | {last_price_text}"


async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = await TelegramUser.objects.aget(chat_id=update.effective_chat.id)
    except TelegramUser.DoesNotExist:
        await update.message.reply_text("Você ainda não tem alertas. Use /novaalerta para criar um.")
        return

    lines = []
    async for alert in user.alerts.filter(is_active=True).order_by("-created_at").aiterator():
        last_price = await alert.price_history.order_by("-checked_at").afirst()
        lines.append(_format_alert_line(alert, last_price))

    if not lines:
        await update.message.reply_text("Você não tem alertas ativos no momento. Use /novaalerta para criar um.")
        return

    await update.message.reply_text("Seus alertas ativos:\n\n" + "\n".join(lines))


async def stop_alert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Uso: /pararalerta <id>")
        return

    try:
        alert_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("O id do alerta deve ser um número. Ex: /pararalerta 3")
        return

    updated = await Alert.objects.filter(
        id=alert_id, user__chat_id=update.effective_chat.id, is_active=True
    ).aupdate(is_active=False)

    if updated:
        await update.message.reply_text(f"Alerta #{alert_id} desativado.")
    else:
        await update.message.reply_text("Alerta não encontrado (ou já estava desativado).")


async def current_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Uso: /precoatual <id>")
        return

    try:
        alert_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("O id do alerta deve ser um número. Ex: /precoatual 3")
        return

    try:
        alert = await Alert.objects.select_related("user").aget(
            id=alert_id, user__chat_id=update.effective_chat.id
        )
    except Alert.DoesNotExist:
        await update.message.reply_text("Alerta não encontrado.")
        return

    rate_limit_key = f"precoatual:ratelimit:{alert.id}"
    if cache.get(rate_limit_key):
        await update.message.reply_text(
            "Você já forçou uma checagem recentemente para esse alerta. "
            "Tente novamente em alguns minutos."
        )
        return
    cache.set(rate_limit_key, True, timeout=PRECOATUAL_RATE_LIMIT_SECONDS)

    await update.message.reply_text("Consultando preço atual...")

    client = AmadeusClient()
    try:
        decision = await _check_alert_async(alert, client)
    except AmadeusError:
        await update.message.reply_text("Não foi possível consultar o preço agora. Tente novamente mais tarde.")
        return

    if decision is None:
        await update.message.reply_text("Nenhuma oferta encontrada para esse trecho/data.")
        return

    await update.message.reply_text(
        f"Preço atual: {decision.price} {decision.currency}\n{decision.reason}"
    )


async def _check_alert_async(alert: Alert, client: AmadeusClient):
    from asgiref.sync import sync_to_async

    return await sync_to_async(alert_engine.check_alert)(alert, client=client, notify_fn=None)
