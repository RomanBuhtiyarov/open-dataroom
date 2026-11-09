"""Проверка и хранение файлов документов в приватном томе."""
import hashlib
import os
import uuid
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

ALLOWED = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
REQUIRED_PARTS = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml"}
MAX_ZIP_ENTRIES = 5000
MAX_ZIP_UNCOMPRESSED = 1024 * 1024 * 1024  # защита от «бомб» в архивах Office


def documents_dir(room_id):
    return settings.PRIVATE_MEDIA_ROOT / "documents" / str(room_id)


def file_path(room_id, stored_name):
    return documents_dir(room_id) / stored_name


def _extension(name):
    return Path(name).suffix.lower().lstrip(".")


def check_upload(uploaded):
    """Проверяет тип, расширение и размер. Возвращает расширение. Бросает ValidationError."""
    ext = _extension(uploaded.name)
    if ext not in ALLOWED:
        raise ValidationError(_("Only PDF, DOCX and XLSX files are accepted."))
    if uploaded.size == 0:
        raise ValidationError(_("The file is empty."))
    if uploaded.size > settings.MAX_UPLOAD_BYTES:
        raise ValidationError(_("The file is too large."))
    uploaded.seek(0)
    if ext == "pdf":
        if b"%PDF-" not in uploaded.read(1024):
            raise ValidationError(_("The file content does not match its type."))
    else:
        _check_office(uploaded, ext)
    uploaded.seek(0)
    return ext


def _check_office(uploaded, ext):
    mismatch = ValidationError(_("The file content does not match its type."))
    try:
        with zipfile.ZipFile(uploaded) as archive:
            infos = archive.infolist()
            names = {info.filename for info in infos}
            if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_ZIP_UNCOMPRESSED:
                raise mismatch
            if any(n.startswith(("/", "\\")) or ".." in Path(n).parts for n in names):
                raise mismatch
            if "[Content_Types].xml" not in names or REQUIRED_PARTS[ext] not in names:
                raise mismatch
            if any(n.lower().endswith("vbaproject.bin") for n in names):
                raise ValidationError(_("Files with macros are not accepted."))
    except zipfile.BadZipFile:
        raise mismatch


def store_upload(room_id, uploaded):
    """Пишет файл во временный файл, затем атомарно переименовывает. Возвращает (stored_name, size, sha256).

    Строка версии в БД создаётся вызывающим кодом только после успеха; при сбое файл удаляется.
    """
    directory = documents_dir(room_id)
    directory.mkdir(parents=True, exist_ok=True)
    stored_name = uuid.uuid4().hex
    final = directory / stored_name
    tmp = directory / f".{stored_name}.part"
    digest, size = hashlib.sha256(), 0
    try:
        with open(tmp, "wb") as out:
            uploaded.seek(0)
            for chunk in uploaded.chunks():
                digest.update(chunk)
                size += len(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        final.unlink(missing_ok=True)
        raise
    return stored_name, size, digest.hexdigest()


def remove_file(room_id, stored_name):
    file_path(room_id, stored_name).unlink(missing_ok=True)
