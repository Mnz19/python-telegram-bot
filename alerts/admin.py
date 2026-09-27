from django import forms
from django.contrib import admin

from .models import Alert, PriceHistory, TelegramUser


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = ("chat_id", "username", "alert_count", "created_at")
    search_fields = ("chat_id", "username")
    ordering = ("-created_at",)

    @admin.display(description="Alertas")
    def alert_count(self, obj: TelegramUser) -> int:
        return obj.alerts.count()


class AlertAdminForm(forms.ModelForm):
    preferred_airlines_text = forms.CharField(
        label="Companhias aéreas preferidas",
        required=False,
        help_text="Códigos IATA separados por vírgula (ex: LA, G3, AD). Deixe em branco para aceitar qualquer companhia.",
        widget=forms.TextInput(attrs={"placeholder": "LA, G3, AD"}),
    )

    class Meta:
        model = Alert
        exclude = ("preferred_airlines",)
        widgets = {
            "period_type": forms.RadioSelect,
            "trip_type": forms.RadioSelect,
        }
        help_texts = {
            "destination": "Deixe em branco para um alerta multi-destino (\"me surpreenda\") — "
            "o bot varre vários destinos populares a cada checagem.",
            "date_from": "Data fixa: data de ida. Faixa de datas / dia da semana flexível: início do período.",
            "date_to": "Usado só em 'Faixa de datas' e 'Dia da semana flexível' (fim do período).",
            "flexible_weekday": "Usado só quando o período é 'Dia da semana flexível'.",
            "months_ahead": "Usado só quando o período é 'Próximos N meses'.",
            "trip_duration_days": "Número de noites de viagem — usado só quando é 'Ida e volta'.",
            "target_price": "Dispara notificação quando o preço encontrado for igual ou menor. Pode deixar em branco.",
            "drop_percentage": "Dispara quando o preço cair essa % ou mais em relação ao menor preço já visto. Pode deixar em branco.",
            "require_checked_bag": "Com o provedor Sky Scrapper esse filtro é ignorado (a API não expõe bagagem por tarifa).",
            "name": "Opcional — dá pra reutilizar esse alerta depois com /duplicar no bot.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.preferred_airlines:
            self.fields["preferred_airlines_text"].initial = ", ".join(self.instance.preferred_airlines)

    def clean(self):
        cleaned = super().clean()
        raw = cleaned.get("preferred_airlines_text", "")
        cleaned["preferred_airlines"] = [code.strip().upper() for code in raw.split(",") if code.strip()]
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.preferred_airlines = self.cleaned_data.get("preferred_airlines", [])
        if commit:
            instance.save()
        return instance


class PriceHistoryInline(admin.TabularInline):
    model = PriceHistory
    extra = 0
    fields = ("price", "currency", "matched_destination", "matched_departure_date", "is_mistake_fare", "checked_at")
    readonly_fields = fields
    ordering = ("-checked_at",)
    can_delete = False
    max_num = 20


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    form = AlertAdminForm
    autocomplete_fields = ("user",)
    list_display = (
        "id",
        "name_or_dash",
        "user",
        "origin",
        "destination_display",
        "period_type",
        "trip_type",
        "threshold_display",
        "is_active",
        "created_at",
    )
    list_display_links = ("id", "name_or_dash")
    list_editable = ("is_active",)
    list_filter = ("is_active", "period_type", "trip_type", "require_checked_bag", "origin")
    search_fields = ("name", "origin", "destination", "user__username", "user__chat_id")
    ordering = ("-created_at",)
    inlines = [PriceHistoryInline]

    fieldsets = (
        ("Identificação", {"fields": ("user", "name", "is_active")}),
        ("Trecho", {"fields": ("origin", "destination")}),
        (
            "Período",
            {
                "fields": ("period_type", "date_from", "date_to", "flexible_weekday", "months_ahead"),
                "description": "Os campos usados dependem do tipo de período escolhido — veja a ajuda de cada campo.",
            },
        ),
        ("Viagem", {"fields": ("trip_type", "trip_duration_days")}),
        (
            "Gatilho de notificação",
            {
                "fields": ("target_price", "drop_percentage"),
                "description": "Se deixar os dois em branco, o alerta ainda avisa em novo menor preço "
                "histórico e em possíveis erros de tarifa.",
            },
        ),
        (
            "Preferências",
            {
                "classes": ("collapse",),
                "fields": ("preferred_airlines_text", "require_checked_bag"),
            },
        ),
    )

    @admin.display(description="Nome", ordering="name")
    def name_or_dash(self, obj: Alert) -> str:
        return obj.name or "—"

    @admin.display(description="Destino")
    def destination_display(self, obj: Alert) -> str:
        return obj.destination or "🌍 multi-destino"

    @admin.display(description="Gatilho")
    def threshold_display(self, obj: Alert) -> str:
        if obj.target_price is not None:
            return f"R$ {obj.target_price}"
        if obj.drop_percentage is not None:
            return f"↓ {obj.drop_percentage}%"
        return "menor preço / erro de tarifa"


@admin.register(PriceHistory)
class PriceHistoryAdmin(admin.ModelAdmin):
    list_display = ("alert", "price", "currency", "matched_destination", "is_mistake_fare", "checked_at")
    list_filter = ("currency", "is_mistake_fare")
    search_fields = ("alert__name", "alert__origin", "alert__destination")
    date_hierarchy = "checked_at"
    ordering = ("-checked_at",)
