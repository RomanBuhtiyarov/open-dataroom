from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

MAX_TEXT = 4000


class Question(models.Model):
    """Вопрос посетителя к комнате, папке или документу и ответ администратора.

    Один вопрос — один ответ; уточнение задаётся новым вопросом. Содержимое вопросов в журнал аудита и письма не попадает.
    """

    class Status(models.TextChoices):
        NEW = "new", _("New")
        IN_PROGRESS = "in_progress", _("In progress")
        ANSWERED = "answered", _("Answered")
        CLOSED = "closed", _("Closed")

    room = models.ForeignKey("rooms.Room", on_delete=models.CASCADE, related_name="questions")
    folder = models.ForeignKey("rooms.Folder", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    document = models.ForeignKey("rooms.Document", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    # Название предмета вопроса на момент создания: остаётся понятным, даже если документ удалён или переименован.
    subject = models.CharField(max_length=200)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="questions")
    text = models.TextField(max_length=MAX_TEXT)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.NEW, db_index=True)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    due_date = models.DateField(null=True, blank=True)
    # Ответ показывается другим посетителям с доступом к области, автор скрыт.
    shared = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    answer_text = models.TextField(blank=True, max_length=MAX_TEXT)
    answered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    answered_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    # Вложение к ответу (один файл), хранится в приватном томе: qa/<room>/<stored_name>.
    attachment_stored = models.CharField(max_length=64, blank=True)
    attachment_name = models.CharField(max_length=255, blank=True)
    attachment_ext = models.CharField(max_length=8, blank=True)
    attachment_size = models.PositiveBigIntegerField(default=0)
    attachment_sha256 = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["room", "status"])]

    def __str__(self):
        return f"{self.pk}: {self.subject}"

    @property
    def has_attachment(self):
        return bool(self.attachment_stored)

    @property
    def scope_folder_id(self):
        """Папка, по которой проверяется видимость: у документа берётся его текущая папка (документ мог переехать)."""
        if self.document_id:
            return self.document.folder_id
        return self.folder_id

    def is_overdue(self, today=None):
        today = today or timezone.localdate()
        return bool(self.due_date and self.due_date < today and self.status in (self.Status.NEW, self.Status.IN_PROGRESS))
