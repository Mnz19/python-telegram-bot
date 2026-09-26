from .base import *  # noqa: F401,F403

DEBUG = False

ALLOWED_HOSTS = ["*"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

TELEGRAM_BOT_TOKEN = "test-token"
AMADEUS_CLIENT_ID = "test-client-id"
AMADEUS_CLIENT_SECRET = "test-client-secret"
