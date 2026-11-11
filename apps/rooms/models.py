from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

MAX_FOLDER_DEPTH = 8


class Room(models.Model):
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, max_length=2000)
    nda_required = models.BooleanField(default=False)
    # Водяной знак (email, время, IP) на страницах предпросмотра.
    watermark_enabled = models.BooleanField(default=True)
    # Ключ комнаты из стартового набора: повторное применение набора не создаёт дубликатов.
    template_key = models.CharField(max_length=40, blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint("template_key", name="rooms_room_unique_template_key", condition=~models.Q(template_key="")),
        ]

    def __str__(self):
        return self.name

    @property
    def current_nda(self):
        return self.nda_versions.order_by("-number").first()


class Folder(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="folders")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    name = models.CharField(max_length=120)
    # Стабильный ключ папки из шаблона: повторное применение шаблона не создаёт дубликатов
    # и не переименовывает папки, даже если язык экземпляра изменился.
    template_key = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(Lower("name"), "room", "parent", name="rooms_folder_unique_name", condition=models.Q(parent__isnull=False)),
            models.UniqueConstraint(Lower("name"), "room", name="rooms_folder_unique_root_name", condition=models.Q(parent__isnull=True)),
        ]

    def __str__(self):
        return self.name

    def ancestor_ids(self):
        """Идентификаторы самой папки и всех её родителей."""
        ids, node = [], self
        while node is not None:
            ids.append(node.id)
            node = node.parent
        return ids

    @property
    def depth(self):
        return len(self.ancestor_ids())


class Document(models.Model):
    folder = models.ForeignKey(Folder, on_delete=models.PROTECT, related_name="documents")
    title = models.CharField(max_length=200)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["title", "id"]
        constraints = [models.UniqueConstraint(Lower("title"), "folder", name="rooms_document_unique_title")]

    def __str__(self):
        return self.title

    @property
    def room(self):
        return self.folder.room

    @property
    def current_version(self):
        return self.versions.order_by("-number").first()


class DocumentVersion(models.Model):
    """Неизменяемая версия. Строка создаётся только после успешного сохранения файла."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    original_name = models.CharField(max_length=255)
    stored_name = models.CharField(max_length=64, unique=True)  # случайное имя файла в приватном томе
    extension = models.CharField(max_length=8)
    size = models.PositiveBigIntegerField()
    page_count = models.PositiveIntegerField(null=True, blank=True)  # только для PDF
    sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-number"]
        constraints = [models.UniqueConstraint("document", "number", name="rooms_version_unique_number")]

    def __str__(self):
        return f"{self.document_id} v{self.number}"


class AccessGrant(models.Model):
    """Право пользователя на комнату целиком (folder пуст) или на папку с вложенными."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="grants")
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="grants")
    folder = models.ForeignKey(Folder, null=True, blank=True, on_delete=models.CASCADE, related_name="grants")
    can_download = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]

    def is_active(self, now=None):
        now = now or timezone.now()
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > now)


class NdaVersion(models.Model):
    """Версия NDA комнаты. Тексты хранятся по языкам; новая версия требует нового согласия."""

    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="nda_versions")
    number = models.PositiveIntegerField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-number"]
        constraints = [models.UniqueConstraint("room", "number", name="rooms_nda_unique_number")]

    def text_for(self, language, fallback):
        """Текст на нужном языке; иначе на языке экземпляра; иначе на любом доступном. Возвращает (язык, текст)."""
        texts = {t.language: t.body for t in self.texts.all()}
        for code in (language, fallback):
            if texts.get(code):
                return code, texts[code]
        for code, body in texts.items():
            if body:
                return code, body
        return "", ""


class NdaText(models.Model):
    version = models.ForeignKey(NdaVersion, on_delete=models.CASCADE, related_name="texts")
    language = models.CharField(max_length=5, choices=settings.LANGUAGES)
    body = models.TextField(max_length=20000)

    class Meta:
        constraints = [models.UniqueConstraint("version", "language", name="rooms_nda_text_unique")]


class NdaAcceptance(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="nda_acceptances")
    version = models.ForeignKey(NdaVersion, on_delete=models.CASCADE, related_name="acceptances")
    language = models.CharField(max_length=5)  # на каком языке показан текст
    accepted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint("user", "version", name="rooms_nda_acceptance_unique")]


class PageTime(models.Model):
    """Время, которое посетитель провёл на странице версии. Учёт приблизительный: секунды видимой страницы.

    Содержимого документов и персональных данных здесь нет; строка существует, если страницу открывали.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    version = models.ForeignKey(DocumentVersion, on_delete=models.CASCADE, related_name="page_times")
    page = models.PositiveIntegerField()
    seconds = models.PositiveIntegerField(default=0)
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint("user", "version", "page", name="rooms_pagetime_unique")]
