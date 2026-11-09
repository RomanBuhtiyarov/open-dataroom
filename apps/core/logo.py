"""Логотип экземпляра: проверка, перекодирование и хранение в приватном томе."""
import hashlib
import io
import os
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from PIL import Image, UnidentifiedImageError

FORMATS = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"), "WEBP": ("webp", "image/webp")}
CONTENT_TYPES = {ext: ctype for ext, ctype in FORMATS.values()}
FILENAME_RE = re.compile(r"^logo-[0-9a-f]{12}\.(png|jpg|webp)$")


def branding_dir():
    return settings.PRIVATE_MEDIA_ROOT / "branding"


def logo_path(filename):
    """Путь к файлу логотипа или None, если имя не соответствует ожидаемому шаблону."""
    if not FILENAME_RE.match(filename or ""):
        return None
    return branding_dir() / filename


def process_logo(uploaded):
    """Проверяет загруженный файл и возвращает (имя_файла, байты) после перекодирования.

    Принимаются только PNG, JPEG и WebP ограниченного размера. Перекодирование убирает
    метаданные и всё, что не является изображением.
    """
    if uploaded.size > settings.MAX_LOGO_BYTES:
        raise ValidationError(_("The logo file is too large."))
    data = uploaded.read()
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            width, height = probe.size
            probe.verify()
        if fmt not in FORMATS:
            raise ValidationError(_("Use a PNG, JPEG or WebP image."))
        if max(width, height) > settings.MAX_LOGO_PIXELS:
            raise ValidationError(_("The logo image is too large."))
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if fmt == "JPEG" and image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            out = io.BytesIO()
            image.save(out, format=fmt)
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
        raise ValidationError(_("Use a PNG, JPEG or WebP image."))
    payload = out.getvalue()
    ext = FORMATS[fmt][0]
    return f"logo-{hashlib.sha256(payload).hexdigest()[:12]}.{ext}", payload


def store_logo(filename, payload):
    directory = branding_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    tmp = directory / f".{filename}.tmp"
    tmp.write_bytes(payload)
    os.replace(tmp, target)


def delete_logo(filename):
    path = logo_path(filename)
    if path is not None and path.exists():
        path.unlink()
