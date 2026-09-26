"""
Selects the concrete settings module based on DJANGO_ENV.

Usage:
    DJANGO_ENV=dev   (default) -> config.settings.dev  (SQLite, DEBUG=True)
    DJANGO_ENV=prod            -> config.settings.prod (Postgres, DEBUG=False)
    DJANGO_ENV=test            -> config.settings.test (in-memory SQLite)

pytest-django points DJANGO_SETTINGS_MODULE directly at config.settings.test,
so this switch only matters for manage.py / runbot / celery entry points.
"""

import os

_env = os.environ.get("DJANGO_ENV", "dev").lower()

if _env == "prod":
    from .prod import *  # noqa: F401,F403
elif _env == "test":
    from .test import *  # noqa: F401,F403
else:
    from .dev import *  # noqa: F401,F403
