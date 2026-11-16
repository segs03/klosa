from django.apps import AppConfig


class NoticesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "notices"

    def ready(self):
        from . import signals  # noqa: F401