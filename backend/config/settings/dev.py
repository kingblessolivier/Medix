"""Local development settings."""

from .base import *  # noqa: F403
from .base import INSTALLED_APPS, env

DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS += ["django_extensions"]

# Mock integrations by default. Never point local at RRA production.
FISCAL_BACKEND = env("FISCAL_BACKEND", default="mock")
PAYMENTS_BACKEND = env("PAYMENTS_BACKEND", default="mock")

CORS_ALLOWED_ORIGINS = ["http://localhost:5173"]

CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_EAGER", default=False)
