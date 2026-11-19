# backoffice/apps.py
from django.apps import AppConfig


class BackofficeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "backoffice"

    def ready(self):
        from . import signals  # noqa: F401