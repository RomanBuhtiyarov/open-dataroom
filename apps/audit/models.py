from django.conf import settings
from django.db import models, transaction
from django.utils import timezone


class AuditEvent(models.Model):
    """Запись журнала. Хранится стабильный код события; подпись переводится при показе (этап 4).

    В журнал не попадают пароли, коды, токены, адреса почты и содержимое файлов:
    цель записи описывается идентификатором вида "user:12".

    Этап 14: каждая запись получает порядковый номер и подпись, связанную с подписью предыдущей
    записи (см. chain.py). Подпись ставится при первом сохранении.
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
    # Цепочка. actor_ref хранит номер человека отдельно от внешнего ключа: при удалении пользователя
    # actor обнуляется, а подпись не должна из-за этого ломаться.
    seq = models.PositiveBigIntegerField(null=True, blank=True, unique=True)
    actor_ref = models.PositiveBigIntegerField(null=True, blank=True)
    legacy = models.BooleanField(default=False)
    chain_mac = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M:%S} {self.code} {self.outcome}"

    def save(self, *args, **kwargs):
        if self._state.adding and self.seq is None:
            from . import chain

            with transaction.atomic():
                chain.seal(self)
                super().save(*args, **kwargs)
                chain.advance(self)
            return
        super().save(*args, **kwargs)


class AuditChainHead(models.Model):
    """Одна строка: номер и подпись последней записи. Строка блокируется на время записи,
    поэтому два запроса не получают один номер."""

    last_seq = models.PositiveBigIntegerField(default=0)
    last_mac = models.CharField(max_length=64, blank=True, default="")
