"""Отдельный процесс для разбора PDF. Не импортирует Django и не видит настройки приложения.

Запуск: `python -I apps/rooms/render_worker.py count` (PDF в stdin, в stdout число страниц)
        `python -I apps/rooms/render_worker.py count-file ПУТЬ`
        `python -I apps/rooms/render_worker.py render ПУТЬ НОМЕР` (в stdout PNG)

Недоверенный PDF разбирает именно этот процесс: сбой, зависание или чрезмерное потребление памяти
убивают его, а не веб-процесс. Ограничения ресурсов ставит родитель (см. previews._limits).
"""
import io
import sys

MAX_SIDE_PX = 1600


def _open(source):
    import pypdfium2 as pdfium

    return pdfium.PdfDocument(source)


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
    else:
        return 2
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
