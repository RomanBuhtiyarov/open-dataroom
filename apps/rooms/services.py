"""Операции над комнатами, папками и документами."""
import os
import re

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.db.models.functions import Lower
from django.utils import translation
from django.utils.translation import gettext, gettext_lazy as _

from . import files
from .models import MAX_FOLDER_DEPTH, Document, DocumentVersion, Folder

# Шаблоны комнат. Ключ папки стабилен, название берётся на языке экземпляра в момент создания.
ROOM_TEMPLATES = {
    "empty": [],
    "fundraising": [
        ("summary", _("Summary and pitch deck")),
        ("financials", _("Financials")),
        ("team", _("Team")),
        ("product", _("Product and technology")),
        ("legal", _("Legal and corporate")),
    ],
    "due_diligence": [
        ("corporate", _("Corporate")),
        ("financial", _("Financial")),
        ("legal", _("Legal")),
        ("tax", _("Tax")),
        ("hr", _("Employees")),
        ("ip", _("Intellectual property")),
        ("commercial", _("Commercial")),
        ("it", _("IT and security")),
    ],
}


def clean_name(value):
    return " ".join((value or "").split())


def create_folder(room, parent, name, template_key=""):
    name = clean_name(name)
    if not name:
        raise ValidationError(_("Enter a name."))
    if parent is not None:
        if parent.room_id != room.id:
            raise ValidationError(_("The folder does not belong to this room."))
        if parent.depth >= MAX_FOLDER_DEPTH:
            raise ValidationError(_("Folders are nested too deeply."))
    if Folder.objects.filter(room=room, parent=parent).annotate(n=Lower("name")).filter(n=name.lower()).exists():
        raise ValidationError(_("A folder with this name already exists here."))
    try:
        with transaction.atomic():
            return Folder.objects.create(room=room, parent=parent, name=name, template_key=template_key)
    except IntegrityError:
        raise ValidationError(_("A folder with this name already exists here."))


def rename_folder(folder, name):
    name = clean_name(name)
    if not name:
        raise ValidationError(_("Enter a name."))
    siblings = Folder.objects.filter(room=folder.room, parent=folder.parent).exclude(pk=folder.pk)
    if siblings.annotate(n=Lower("name")).filter(n=name.lower()).exists():
        raise ValidationError(_("A folder with this name already exists here."))
    folder.name = name
    try:
        with transaction.atomic():
            folder.save(update_fields=["name"])
    except IntegrityError:
        raise ValidationError(_("A folder with this name already exists here."))
    return folder


def apply_template(room, key, language):
    """Создаёт недостающие папки шаблона. Повторный вызов ничего не дублирует и не переименовывает."""
    created = 0
    with translation.override(language):
        for template_key, label in ROOM_TEMPLATES.get(key, []):
            if Folder.objects.filter(room=room, parent=None, template_key=template_key).exists():
                continue
            name = clean_name(gettext(label))
            # Если имя занято вручную созданной папкой, пропускаем: данные администратора не трогаем.
            if Folder.objects.filter(room=room, parent=None).annotate(n=Lower("name")).filter(n=name.lower()).exists():
                continue
            Folder.objects.create(room=room, parent=None, name=name, template_key=template_key)
            created += 1
    return created


def _safe_original_name(name):
    base = os.path.basename((name or "").replace("\\", "/"))
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return base[-255:] or "document"


def _title_taken(folder, title, exclude_id=None):
    qs = Document.objects.filter(folder=folder).annotate(t=Lower("title")).filter(t=title.lower())
    return qs.exclude(pk=exclude_id).exists() if exclude_id else qs.exists()


def create_document(folder, title, uploaded, user):
    """Новый документ с версией 1. Файл сохраняется до записи в БД; при сбое БД файл удаляется."""
    title = clean_name(title) or os.path.splitext(_safe_original_name(uploaded.name))[0]
    title = title[:200]
    if _title_taken(folder, title):
        raise ValidationError(_("A document with this title already exists in the folder."))
    ext, pages = files.check_upload(uploaded)
    stored, size, sha = files.store_upload(folder.room_id, uploaded)
    try:
        with transaction.atomic():
            document = Document.objects.create(folder=folder, title=title, created_by=user)
            version = DocumentVersion.objects.create(
                document=document, number=1, original_name=_safe_original_name(uploaded.name),
                stored_name=stored, extension=ext, size=size, sha256=sha, page_count=pages, uploaded_by=user,
            )
    except IntegrityError:
        files.remove_file(folder.room_id, stored)
        raise ValidationError(_("A document with this title already exists in the folder."))
    except BaseException:
        files.remove_file(folder.room_id, stored)
        raise
    return document, version


def add_version(document, uploaded, user):
    """Публикует новую версию только после успешного сохранения файла. Старые версии не меняются."""
    ext, pages = files.check_upload(uploaded)
    room_id = document.folder.room_id
    stored, size, sha = files.store_upload(room_id, uploaded)
    try:
        with transaction.atomic():
            Document.objects.select_for_update().get(pk=document.pk)
            number = (DocumentVersion.objects.filter(document=document).aggregate(m=Max("number"))["m"] or 0) + 1
            version = DocumentVersion.objects.create(
                document=document, number=number, original_name=_safe_original_name(uploaded.name),
                stored_name=stored, extension=ext, size=size, sha256=sha, page_count=pages, uploaded_by=user,
            )
    except BaseException:
        files.remove_file(room_id, stored)
        raise
    return version
