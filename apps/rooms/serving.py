"""Выдача файлов с поддержкой HTTP Range. Вызывается только после проверки политики доступа."""
import os
import re

from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.utils.http import content_disposition_header

CHUNK = 64 * 1024
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def parse_range(header, size):
    """Один диапазон «bytes=a-b». Возвращает (start, end) включительно, None (отдать целиком) или "invalid" (416)."""
    match = RANGE_RE.match((header or "").strip())
    if not match:
        return None  # несколько диапазонов и прочее не поддерживаем: отдаём файл целиком
    first, last = match.groups()
    if not first and not last:
        return None
    if not first:  # суффикс: последние N байт
        length = int(last)
        if length == 0:
            return "invalid"
        return max(size - length, 0), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or end < start:
        return "invalid"
    return start, min(end, size - 1)


def _iter_range(path, start, length):
    with open(path, "rb") as handle:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def file_response(request, path, *, filename, content_type, attachment=True):
    size = os.path.getsize(path)
    requested = parse_range(request.headers.get("Range"), size)
    if requested == "invalid":
        response = HttpResponse(status=416)
        response["Content-Range"] = f"bytes */{size}"
    elif requested is None:
        response = FileResponse(open(path, "rb"), as_attachment=attachment, filename=filename, content_type=content_type)
    else:
        start, end = requested
        length = end - start + 1
        response = StreamingHttpResponse(_iter_range(path, start, length), status=206, content_type=content_type)
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
        response["Content-Length"] = str(length)
        response["Content-Disposition"] = content_disposition_header(attachment, filename)
    response["Accept-Ranges"] = "bytes"
    response["Cache-Control"] = "private, no-store"
    return response


def is_first_chunk(request):
    """Событие скачивания пишем один раз: для запроса целиком или диапазона, начинающегося с нуля."""
    header = (request.headers.get("Range") or "").strip()
    match = RANGE_RE.match(header)
    return not match or match.group(1) in ("0", "00")
