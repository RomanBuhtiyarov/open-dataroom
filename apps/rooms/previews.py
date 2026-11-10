"""Растровые превью страниц PDF (pypdfium2, без внешних программ).

Файл-оригинал не отдаётся: посетитель без права скачивания видит только картинки страниц.
Картинки строятся по требованию и кэшируются в приватном томе; доступ к ним проверяет политика на каждый запрос.
Ограничения: число страниц и размер картинки заданы, чтобы один документ не мог исчерпать ресурсы.
"""
import io
import os

import pypdfium2 as pdfium
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

MAX_PAGES = 500
MAX_SIDE_PX = 1600


class PreviewError(Exception):
    """Страницу не удалось построить (файл недоступен, номер вне диапазона, повреждённый PDF)."""


def pages_from_bytes(data):
    """Число страниц загружаемого PDF. Нечитаемые и защищённые паролем файлы отклоняются."""
    try:
        document = pdfium.PdfDocument(data)
    except Exception:  # noqa: BLE001 - pdfium сообщает об ошибках разными классами
        raise ValidationError(_("The PDF file cannot be read or is protected by a password."))
    try:
        count = len(document)
    finally:
        document.close()
    if count < 1:
        raise ValidationError(_("The PDF file cannot be read or is protected by a password."))
    if count > MAX_PAGES:
        raise ValidationError(_("The PDF has too many pages."))
    return count


def pages_from_path(path):
    try:
        document = pdfium.PdfDocument(str(path))
    except Exception as exc:  # noqa: BLE001
        raise PreviewError("cannot open document") from exc
    try:
        return len(document)
    finally:
        document.close()


def cache_path(room_id, stored_name, number):
    return settings.PRIVATE_MEDIA_ROOT / "previews" / str(room_id) / stored_name / f"{number}.png"


def render_page(room_id, stored_name, source_path, number):
    """PNG страницы `number` (с 1). Использует кэш; результат записывается атомарно."""
    cached = cache_path(room_id, stored_name, number)
    if cached.exists():
        return cached.read_bytes()
    try:
        document = pdfium.PdfDocument(str(source_path))
    except Exception as exc:  # noqa: BLE001
        raise PreviewError("cannot open document") from exc
    try:
        if not 1 <= number <= len(document):
            raise PreviewError("page out of range")
        page = document[number - 1]
        width, height = page.get_size()
        scale = min(2.0, MAX_SIDE_PX / max(width, height, 1))
        image = page.render(scale=scale).to_pil().convert("RGB")
        out = io.BytesIO()
        image.save(out, format="PNG")
    except PreviewError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PreviewError("render failed") from exc
    finally:
        document.close()
    payload = out.getvalue()
    cached.parent.mkdir(parents=True, exist_ok=True)
    tmp = cached.with_name(f".{cached.name}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, cached)
    return payload
