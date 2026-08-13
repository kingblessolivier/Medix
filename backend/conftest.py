"""Test-wide configuration."""

from django.conf import settings


def pytest_configure(config):
    # Django's default hasher is deliberately slow — that is correct in
    # production and pure cost in tests, where fixtures create users on
    # every case.
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

    # Nothing under test should reach a broker.
    settings.CELERY_TASK_ALWAYS_EAGER = True
