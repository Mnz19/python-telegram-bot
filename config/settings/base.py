"""
Django settings shared by every environment.

Environment-specific pieces (database, debug, allowed hosts) live in
dev.py / prod.py / test.py, selected via the DJANGO_SETTINGS_MODULE env var.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(env_file)

SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-dev-key-change-me")

INSTALLED_APPS = [
    "jazzmin",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_celery_beat",
    "alerts",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = env("DJANGO_TIME_ZONE", default="America/Belem")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Overridden with Redis in prod.py. Local dev/test run fine without Redis
# since the cache is only used to memoize the Amadeus OAuth token.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# --- Telegram bot ---
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")

# --- Flight price provider ---
# "skyscanner" (Sky Scrapper on RapidAPI, default — free instant signup) or
# "amadeus" (manual business review required for anything beyond sandbox).
FLIGHT_PROVIDER = env("FLIGHT_PROVIDER", default="skyscanner")

# --- Sky Scrapper (RapidAPI) ---
RAPIDAPI_KEY = env("RAPIDAPI_KEY", default="")

# --- Amadeus API ---
AMADEUS_CLIENT_ID = env("AMADEUS_CLIENT_ID", default="")
AMADEUS_CLIENT_SECRET = env("AMADEUS_CLIENT_SECRET", default="")
AMADEUS_BASE_URL = env("AMADEUS_BASE_URL", default="https://test.api.amadeus.com")

# --- Celery ---
CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# Default interval (minutes) for the price-check task, used to seed the
# django-celery-beat periodic task on first run. Adjustable later via admin.
PRICE_CHECK_INTERVAL_MINUTES = env.int("PRICE_CHECK_INTERVAL_MINUTES", default=60)

# Default origin airport suggested to users when creating an alert.
DEFAULT_ORIGIN_IATA = env("DEFAULT_ORIGIN_IATA", default="BEL")

# --- Admin theme (django-jazzmin) ---
JAZZMIN_SETTINGS = {
    "site_title": "Alertas de Passagens",
    "site_header": "Alertas de Passagens",
    "site_brand": "✈️ Alertas de Passagens",
    "welcome_sign": "Painel de alertas de passagens aéreas",
    "copyright": "Alertas de Passagens",
    "search_model": ["alerts.Alert", "alerts.TelegramUser"],
    "order_with_respect_to": ["alerts", "alerts.Alert", "alerts.TelegramUser", "alerts.PriceHistory"],
    "icons": {
        "auth": "fas fa-users-cog",
        "auth.user": "fas fa-user",
        "auth.Group": "fas fa-users",
        "alerts.TelegramUser": "fab fa-telegram",
        "alerts.Alert": "fas fa-bell",
        "alerts.PriceHistory": "fas fa-chart-line",
        "django_celery_beat.PeriodicTask": "fas fa-clock",
        "django_celery_beat.IntervalSchedule": "fas fa-hourglass-half",
        "django_celery_beat.CrontabSchedule": "fas fa-calendar-alt",
    },
    "default_icon_parents": "fas fa-chevron-circle-right",
    "default_icon_children": "fas fa-circle",
    "show_ui_builder": False,
    "changeform_format": "collapsible",
    "changeform_format_overrides": {"alerts.Alert": "vertical_tabs"},
    "related_modal_active": True,
}

JAZZMIN_UI_TWEAKS = {
    "theme": "flatly",
    "navbar": "navbar-dark",
    "no_navbar_border": True,
    "sidebar": "sidebar-dark-primary",
    "sidebar_nav_flat_style": True,
    "brand_colour": "navbar-primary",
    "accent": "accent-primary",
    "button_classes": {
        "primary": "btn-primary",
        "secondary": "btn-secondary",
        "info": "btn-info",
        "warning": "btn-warning",
        "danger": "btn-danger",
        "success": "btn-success",
    },
}
