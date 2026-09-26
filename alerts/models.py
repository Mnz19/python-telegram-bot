from django.core.exceptions import ValidationError
from django.db import models


class TelegramUser(models.Model):
    chat_id = models.BigIntegerField(unique=True)
    username = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"@{self.username}" if self.username else str(self.chat_id)


class Alert(models.Model):
    class PeriodType(models.TextChoices):
        FIXED_DATE = "fixed_date", "Data fixa"
        DATE_RANGE = "date_range", "Faixa de datas"
        NEXT_MONTHS = "next_months", "Próximos N meses"

    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE, related_name="alerts")
    origin = models.CharField(max_length=3)
    destination = models.CharField(max_length=3)

    period_type = models.CharField(max_length=20, choices=PeriodType.choices)
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    months_ahead = models.PositiveSmallIntegerField(null=True, blank=True)

    target_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    drop_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(target_price__isnull=False) | models.Q(drop_percentage__isnull=False),
                name="alert_has_target_price_or_drop_percentage",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.origin}->{self.destination} ({self.get_period_type_display()})"

    def clean(self):
        if self.target_price is None and self.drop_percentage is None:
            raise ValidationError("Informe um preço-alvo ou uma porcentagem de queda.")
        if self.period_type == self.PeriodType.FIXED_DATE and not self.date_from:
            raise ValidationError("Data fixa requer 'date_from'.")
        if self.period_type == self.PeriodType.DATE_RANGE and not (self.date_from and self.date_to):
            raise ValidationError("Faixa de datas requer 'date_from' e 'date_to'.")
        if self.period_type == self.PeriodType.NEXT_MONTHS and not self.months_ahead:
            raise ValidationError("'Próximos N meses' requer 'months_ahead'.")


class PriceHistory(models.Model):
    alert = models.ForeignKey(Alert, on_delete=models.CASCADE, related_name="price_history")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="BRL")
    checked_at = models.DateTimeField(auto_now_add=True)
    raw_response = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-checked_at"]
        verbose_name_plural = "price histories"

    def __str__(self) -> str:
        return f"{self.alert} @ {self.price} {self.currency} ({self.checked_at:%Y-%m-%d %H:%M})"
