from django.conf import settings
from django.db import models
from django.utils import timezone


class AuditEvent(models.Model):
    """Запись журнала. Хранится стабильный код события; подпись переводится при показе (этап 4).

    В журнал не попадают пароли, коды, токены, адреса почты и содержимое файлов:
    цель записи описывается идентификатором вида "user:12".
    """

    class Outcome(models.TextChoices):
        OK = "ok"
        DENIED = "denied"
        FAILED = "failed"

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    code = models.CharField(max_length=64, db_index=True)
    target = models.CharField(max_length=200, blank=True)
    outcome = models.CharField(max_length=16, choices=Outcome.choices, default=Outcome.OK)
    # Идентификатор запроса: связывает события одного запроса. Создаётся сервером, не берётся от клиента.
    request_id = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M:%S} {self.code} {self.outcome}"
