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
        FLEXIBLE_WEEKDAY = "flexible_weekday", "Dia da semana flexível"

    class TripType(models.TextChoices):
        ROUND_TRIP = "round_trip", "Ida e volta"
        ONE_WAY = "one_way", "Só ida"

    WEEKDAY_CHOICES = [
        (0, "Segunda"),
        (1, "Terça"),
        (2, "Quarta"),
        (3, "Quinta"),
        (4, "Sexta"),
        (5, "Sábado"),
        (6, "Domingo"),
    ]

    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE, related_name="alerts")

    # Optional friendly label so an alert can be reused as a "profile"
    # (e.g. "Rio de férias") instead of recreated from scratch.
    name = models.CharField(max_length=100, blank=True, default="")

    origin = models.CharField(max_length=3)
    # Blank destination means "multi-destino / me surpreenda": the engine
    # scans every airport in services.constants.POPULAR_DESTINATIONS.
    destination = models.CharField(max_length=3, blank=True, default="")

    period_type = models.CharField(max_length=20, choices=PeriodType.choices)
    # FIXED_DATE: date_from is the departure date.
    # DATE_RANGE: departure can be any date between date_from and date_to.
    # FLEXIBLE_WEEKDAY: any `flexible_weekday` date between date_from and date_to.
    # NEXT_MONTHS: departure is computed as today + months_ahead*30 days.
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    months_ahead = models.PositiveSmallIntegerField(null=True, blank=True)
    flexible_weekday = models.PositiveSmallIntegerField(
        null=True, blank=True, choices=WEEKDAY_CHOICES
    )

    trip_type = models.CharField(max_length=20, choices=TripType.choices, default=TripType.ROUND_TRIP)
    # Only meaningful when trip_type == ROUND_TRIP: return date is computed
    # as departure_date + trip_duration_days for each candidate searched.
    trip_duration_days = models.PositiveSmallIntegerField(null=True, blank=True)

    # Both optional: an alert can rely solely on the automatic "new
    # historical low" / "mistake fare" triggers (see alert_engine.evaluate_alert).
    target_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    drop_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    preferred_airlines = models.JSONField(default=list, blank=True)
    require_checked_bag = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        label = f"{self.name} - " if self.name else ""
        destination = self.destination or "qualquer destino"
        return f"{label}{self.origin}->{destination} ({self.get_period_type_display()})"

    @property
    def is_multi_destination(self) -> bool:
        return not self.destination

    def clean(self):
        if self.period_type == self.PeriodType.FIXED_DATE and not self.date_from:
            raise ValidationError("Data fixa requer 'date_from'.")
        if self.period_type == self.PeriodType.DATE_RANGE and not (self.date_from and self.date_to):
            raise ValidationError("Faixa de datas requer 'date_from' e 'date_to'.")
        if self.period_type == self.PeriodType.NEXT_MONTHS and not self.months_ahead:
            raise ValidationError("'Próximos N meses' requer 'months_ahead'.")
        if self.period_type == self.PeriodType.FLEXIBLE_WEEKDAY and not (
            self.date_from and self.date_to and self.flexible_weekday is not None
        ):
            raise ValidationError(
                "Dia da semana flexível requer 'date_from', 'date_to' e 'flexible_weekday'."
            )
        if self.trip_type == self.TripType.ROUND_TRIP and not self.trip_duration_days:
            raise ValidationError("Ida e volta requer 'trip_duration_days'.")


class PriceHistory(models.Model):
    alert = models.ForeignKey(Alert, on_delete=models.CASCADE, related_name="price_history")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="BRL")
    checked_at = models.DateTimeField(auto_now_add=True)
    raw_response = models.JSONField(null=True, blank=True)

    # Which concrete (date, destination) this price came from — relevant
    # for multi-destino and flexible-weekday alerts that search several
    # candidates per check and only record the cheapest one found.
    matched_departure_date = models.DateField(null=True, blank=True)
    matched_destination = models.CharField(max_length=3, blank=True, default="")

    is_mistake_fare = models.BooleanField(default=False)

    class Meta:
        ordering = ["-checked_at"]
        verbose_name_plural = "price histories"

    def __str__(self) -> str:
        return f"{self.alert} @ {self.price} {self.currency} ({self.checked_at:%Y-%m-%d %H:%M})"
