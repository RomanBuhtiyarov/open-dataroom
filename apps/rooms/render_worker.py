"""Отдельный процесс для разбора PDF. Не импортирует Django и не видит настройки приложения.

Запуск: `python -I apps/rooms/render_worker.py count` (PDF в stdin, в stdout число страниц)
        `python -I apps/rooms/render_worker.py count-file ПУТЬ`
        `python -I apps/rooms/render_worker.py render ПУТЬ НОМЕР` (в stdout PNG)
        `python -I apps/rooms/render_worker.py stamp ИСТОЧНИК РЕЗУЛЬТАТ` (строки знака JSON-списком в stdin)

Недоверенный PDF разбирает именно этот процесс: сбой, зависание или чрезмерное потребление памяти
убивают его, а не веб-процесс. Ограничения ресурсов ставит родитель (см. previews._limits).
"""
import io
import json
import math
import sys

MAX_SIDE_PX = 1600
STAMP_ANGLE = 30
STAMP_ALPHA = 70  # из 255: знак виден, но чтение страницы не затрудняет
STAMP_MAX_LINES = 5
STAMP_MAX_LINE_LENGTH = 200


def _open(source):
    import pypdfium2 as pdfium

    return pdfium.PdfDocument(source)


def _wide(text):
    """Строка в виде FPDF_WIDESTRING: UTF-16LE с завершающим нулём."""
    import ctypes

    raw = text.encode("utf-16-le") + b"\x00\x00"
    return (ctypes.c_ushort * (len(raw) // 2)).from_buffer_copy(raw)


def _stamp(source, target, lines):
    """Векторный знак на каждой странице: текст остаётся текстом, страницы не растрируются."""
    import pypdfium2.raw as raw

    lines = [str(line)[:STAMP_MAX_LINE_LENGTH] for line in lines if line][:STAMP_MAX_LINES]
    if not lines:
        raise ValueError("no stamp lines")
    document = _open(source)
    font = raw.FPDFText_LoadStandardFont(document.raw, b"Helvetica")
    if not font:
        raise RuntimeError("font")
    cos, sin = math.cos(math.radians(STAMP_ANGLE)), math.sin(math.radians(STAMP_ANGLE))
    for index in range(len(document)):
        page = document[index]
        x0, y0, x1, y1 = page.get_mediabox()
        width, height = x1 - x0, y1 - y0
        size = max(9.0, min(width, height) / 42)
        line_h = size * 1.35
        text_w = max(len(line) for line in lines) * size * 0.56
        step_u, step_v = (text_w + size * 2) * 1.1, (line_h * len(lines) + size) * 1.7
        cx, cy = x0 + width / 2, y0 + height / 2
        reach = math.hypot(width, height) / 2 + step_u
        row = 0
        v = -reach
        while v <= reach:
            u = -reach + (row % 2) * step_u / 2
            while u <= reach:
                tx, ty = cx + u * cos - v * sin, cy + u * sin + v * cos
                if x0 - text_w <= tx <= x1 + text_w and y0 - text_w <= ty <= y1 + text_w:
                    for n, line in enumerate(lines):
                        obj = raw.FPDFPageObj_CreateTextObj(document.raw, font, size)
                        if not obj or not raw.FPDFText_SetText(obj, _wide(line)):
                            raise RuntimeError("text object")
                        raw.FPDFPageObj_SetFillColor(obj, 30, 30, 30, STAMP_ALPHA)
                        raw.FPDFPageObj_Transform(obj, cos, sin, -sin, cos, tx + n * line_h * sin, ty - n * line_h * cos)
                        raw.FPDFPage_InsertObject(page.raw, obj)
                u += step_u
            v += step_v
            row += 1
        if not raw.FPDFPage_GenerateContent(page.raw):
            raise RuntimeError("content")
    document.save(target)


def main(argv):
    mode = argv[1]
    if mode == "count":
        document = _open(sys.stdin.buffer.read())
        sys.stdout.write(str(len(document)))
    elif mode == "count-file":
        document = _open(argv[2])
        sys.stdout.write(str(len(document)))
    elif mode == "render":
        document = _open(argv[2])
        number = int(argv[3])
        if not 1 <= number <= len(document):
            return 3
        page = document[number - 1]
        width, height = page.get_size()
        scale = min(2.0, MAX_SIDE_PX / max(width, height, 1))
        image = page.render(scale=scale).to_pil().convert("RGB")
        out = io.BytesIO()
        image.save(out, format="PNG")
        sys.stdout.buffer.write(out.getvalue())
    elif mode == "stamp":
        lines = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
            return 2
        _stamp(argv[2], argv[3], lines)
    else:
        return 2
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
