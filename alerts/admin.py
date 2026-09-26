from django.contrib import admin

from .models import Alert, PriceHistory, TelegramUser


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = ("chat_id", "username", "created_at")
    search_fields = ("chat_id", "username")


class PriceHistoryInline(admin.TabularInline):
    model = PriceHistory
    extra = 0
    fields = ("price", "currency", "checked_at")
    readonly_fields = ("price", "currency", "checked_at")
    ordering = ("-checked_at",)
    can_delete = False
    max_num = 20


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "origin",
        "destination",
        "period_type",
        "target_price",
        "drop_percentage",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active", "period_type", "origin", "destination")
    search_fields = ("origin", "destination", "user__username", "user__chat_id")
    inlines = [PriceHistoryInline]


@admin.register(PriceHistory)
class PriceHistoryAdmin(admin.ModelAdmin):
    list_display = ("alert", "price", "currency", "checked_at")
    list_filter = ("currency",)
    date_hierarchy = "checked_at"
