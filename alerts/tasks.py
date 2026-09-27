import logging

from celery import shared_task
from django.utils import timezone
from datetime import timedelta

from .models import Alert, TelegramUser
from .services import alert_engine
from .services.flight_client import get_flight_client

logger = logging.getLogger(__name__)


@shared_task
def check_prices() -> None:
    """Sweep every active Alert and check its current price.

    Runs on a schedule via django-celery-beat. Each alert is dispatched to
    its own task so a failure on one alert never blocks the others. Alerts
    with a far-off departure date are skipped on most runs (see
    alert_engine.should_check_now) to conserve rate-limited Amadeus calls.
    """
    alerts = Alert.objects.filter(is_active=True)
    due_alert_ids = [alert.id for alert in alerts if alert_engine.should_check_now(alert)]

    logger.info(
        "Disparando checagem de preço para %s de %s alertas ativos",
        len(due_alert_ids), alerts.count(),
    )
    for alert_id in due_alert_ids:
        check_single_alert.delay(alert_id)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def check_single_alert(self, alert_id: int) -> None:
    try:
        alert = Alert.objects.select_related("user").get(id=alert_id, is_active=True)
    except Alert.DoesNotExist:
        logger.info("Alert #%s não existe mais ou foi desativado, ignorando", alert_id)
        return

    client = get_flight_client()
    alert_engine.check_alert(alert, client=client, notify_fn=alert_engine.notify)


@shared_task
def send_weekly_summaries() -> None:
    """Sends each user a digest of the lowest price seen per active alert
    over the past 7 days. Scheduled for Monday mornings via django-celery-beat.
    """
    since = timezone.now() - timedelta(days=7)
    users = TelegramUser.objects.filter(alerts__is_active=True).distinct()
    logger.info("Enviando resumo semanal para %s usuários", users.count())
    for user in users:
        try:
            alert_engine.send_weekly_summary_to_user(user, since)
        except Exception:
            logger.exception("Falha ao enviar resumo semanal para user #%s", user.id)
