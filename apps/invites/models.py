from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class Invitation(models.Model):
    """Персональное приглашение. Токен хранится только как хеш; открытый вид известен лишь в момент создания."""

    token_hash = models.CharField(max_length=64, unique=True)
    email = models.EmailField()
    name = models.CharField(max_length=150, blank=True)
    language = models.CharField(max_length=5, choices=settings.LANGUAGES)
    room = models.ForeignKey("rooms.Room", on_delete=models.CASCADE, related_name="invitations")
    folder = models.ForeignKey("rooms.Folder", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    can_download = models.BooleanField(default=False)
    access_expires_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    consumed_at = models.DateTimeField(null=True, blank=True)
    consumed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    revoked_at = models.DateTimeField(null=True, blank=True)
    email_sent_at = models.DateTimeField(null=True, blank=True)
    email_failed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    PENDING, ACCEPTED, REVOKED, EXPIRED = "pending", "accepted", "revoked", "expired"

    def status(self, now=None):
        now = now or timezone.now()
        if self.consumed_at:
            return self.ACCEPTED
        if self.revoked_at:
            return self.REVOKED
        if self.expires_at <= now:
            return self.EXPIRED
        return self.PENDING

    def is_pending(self, now=None):
        return self.status(now) == self.PENDING


class AccessRequest(models.Model):
    """Заявка на доступ без приглашения. Пароль хранится только как хеш."""

    class Status(models.TextChoices):
        UNCONFIRMED = "unconfirmed"  # адрес ещё не подтверждён кодом
        PENDING = "pending"  # ждёт решения администратора
        APPROVED = "approved"
        REJECTED = "rejected"

    email = models.EmailField()
    name = models.CharField(max_length=150)
    password_hash = models.CharField(max_length=256)
    language = models.CharField(max_length=5, choices=settings.LANGUAGES)
    message = models.TextField(blank=True, max_length=500)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UNCONFIRMED)
    email_confirmed_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    notified_at = models.DateTimeField(null=True, blank=True)
    notify_failed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            # Одна заявка на рассмотрении на адрес. Неподтверждённых может быть несколько:
            # у каждой свой код и своя сессия, поэтому чужая заявка не подменяет данные владельца адреса.
            models.UniqueConstraint(
                Lower("email"), name="invites_request_one_pending_per_email",
                condition=models.Q(status="pending"),
            )
        ]


class EmailCode(models.Model):
    """Одноразовый код подтверждения адреса. Хранится только хеш; число попыток ограничено."""

    invitation = models.ForeignKey(Invitation, null=True, blank=True, on_delete=models.CASCADE, related_name="codes")
    access_request = models.ForeignKey(AccessRequest, null=True, blank=True, on_delete=models.CASCADE, related_name="codes")
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
