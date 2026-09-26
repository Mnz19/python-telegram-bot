import logging

from celery import shared_task

from .models import Alert
from .services import alert_engine
from .services.amadeus_client import AmadeusClient

logger = logging.getLogger(__name__)


@shared_task
def check_prices() -> None:
    """Sweep every active Alert and check its current price.

    Runs on a schedule via django-celery-beat. Each alert is dispatched to
    its own task so a failure on one alert never blocks the others.
    """
    alert_ids = list(Alert.objects.filter(is_active=True).values_list("id", flat=True))
    logger.info("Disparando checagem de preço para %s alertas ativos", len(alert_ids))
    for alert_id in alert_ids:
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

    client = AmadeusClient()
    alert_engine.check_alert(alert, client=client, notify_fn=alert_engine.notify)
