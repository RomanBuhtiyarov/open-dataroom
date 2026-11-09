from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class InstanceSettings(models.Model):
    """Настройки экземпляра (одна строка, pk=1). Пустые значения означают «взять из окружения»."""

    class RoomTemplate(models.TextChoices):
        EMPTY = "empty", _("Empty room")
        FUNDRAISING = "fundraising", _("Fundraising")
        DUE_DILIGENCE = "due_diligence", _("Due diligence")

    site_name = models.CharField(max_length=80, blank=True)
    default_locale = models.CharField(max_length=5, blank=True, choices=settings.LANGUAGES)
    # Имя файла логотипа в PRIVATE_MEDIA_ROOT/branding (содержит хэш содержимого).
    logo_file = models.CharField(max_length=64, blank=True)
    room_template = models.CharField(max_length=32, choices=RoomTemplate.choices, default=RoomTemplate.EMPTY)
    self_registration_enabled = models.BooleanField(default=False)
    bootstrapped_at = models.DateTimeField(null=True, blank=True)
    setup_completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def current(cls):
        """Сохранённая строка или несохранённый экземпляр со значениями по умолчанию."""
        return cls.objects.filter(pk=1).first() or cls(pk=1)

    @property
    def effective_site_name(self):
        return self.site_name or settings.SITE_NAME

    @property
    def effective_locale(self):
        return self.default_locale if self.default_locale in settings.SUPPORTED_LANGUAGE_CODES else settings.DEFAULT_LOCALE

    @property
    def is_setup_complete(self):
        return self.setup_completed_at is not None


class RateLimitHit(models.Model):
    """Одно обращение для ограничения частоты. Ключ — хеш (область:значение), без открытых адресов."""

    key = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["key", "created_at"])]
