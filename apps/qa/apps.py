from django.apps import AppConfig


class QaConfig(AppConfig):
    name = "apps.qa"
    label = "qa"

    def ready(self):
        from . import signals  # noqa: F401 - подключает приёмник удаления папки
