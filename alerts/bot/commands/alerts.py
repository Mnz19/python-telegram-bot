from __future__ import annotations

from django.core.cache import cache
from telegram import Update
from telegram.ext import ContextTypes

from ...models import Alert, PriceHistory, TelegramUser
from ...services import alert_engine
from ...services.exceptions import FlightProviderError
from ...services.flight_client import FlightClient, get_flight_client

PRECOATUAL_RATE_LIMIT_SECONDS = 300


def _format_threshold(alert: Alert) -> str:
    parts = []
    if alert.target_price is not None:
        parts.append(f"R$ {alert.target_price}")
    if alert.drop_percentage is not None:
        parts.append(f"{alert.drop_percentage}% de queda")
    if not parts:
        parts.append("apenas menor preço histórico / erro de tarifa")
    return " ou ".join(parts)


def _format_alert_line(alert: Alert, last_price: PriceHistory | None) -> str:
    name = f"{alert.name} " if alert.name else ""
    destination = alert.destination or "vários destinos"
    trip = "ida e volta" if alert.trip_type == Alert.TripType.ROUND_TRIP else "só ida"
    status = "ativo" if alert.is_active else "pausado"
    last_price_text = (
        f"último preço: {last_price.price} {last_price.currency}" if last_price else "sem checagens ainda"
    )
    return (
        f"#{alert.id} {name}({status}) {alert.origin} → {destination} | {trip} | "
        f"alvo: {_format_threshold(alert)} | {last_price_text}"
    )


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


async def _set_alert_active(update: Update, context: ContextTypes.DEFAULT_TYPE, active: bool, usage: str) -> None:
    if not context.args:
        await update.message.reply_text(f"Uso: {usage}")
        return

    try:
        alert_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(f"O id do alerta deve ser um número. Ex: {usage.replace('<id>', '3')}")
        return

    updated = await Alert.objects.filter(
        id=alert_id, user__chat_id=update.effective_chat.id, is_active=not active
    ).aupdate(is_active=active)

    if updated:
        action = "reativado" if active else "pausado"
        await update.message.reply_text(f"Alerta #{alert_id} {action}.")
    else:
        state = "ativo" if active else "pausado"
        await update.message.reply_text(f"Alerta não encontrado (ou já estava {state}).")


async def stop_alert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles both /pararalerta and /pausar — they're the same operation."""
    await _set_alert_active(update, context, active=False, usage="/pararalerta <id>")


async def resume_alert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_alert_active(update, context, active=True, usage="/retomar <id>")


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

    client = get_flight_client()
    try:
        decision = await _check_alert_async(alert, client)
    except FlightProviderError:
        await update.message.reply_text("Não foi possível consultar o preço agora. Tente novamente mais tarde.")
        return

    if decision is None:
        await update.message.reply_text("Nenhuma oferta encontrada para esse trecho/data.")
        return

    destination = decision.matched_destination or alert.destination
    await update.message.reply_text(
        f"Preço atual ({destination}): {decision.price} {decision.currency}\n{decision.reason}"
    )


async def _check_alert_async(alert: Alert, client: FlightClient):
    from asgiref.sync import sync_to_async

    return await sync_to_async(alert_engine.check_alert)(alert, client=client, notify_fn=None)
