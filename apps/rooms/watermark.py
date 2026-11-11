"""Водяной знак на страницах предпросмотра.

Знак вшивается в пиксели картинки на сервере: убрать его средствами браузера нельзя. Кэш страниц общий и без знака;
знак накладывается при выдаче, поэтому доступ по-прежнему проверяется на каждый запрос.
Это сдерживающая мера: она не защищает от фотографии экрана, но позволяет найти источник утечки.
"""
import io
import re

from PIL import Image, ImageDraw, ImageFont

VIEW_CODE_RE = re.compile(r"^[0-9a-f]{8}$")
ANGLE = 30
OPACITY = 46  # из 255: текст виден, но чтение страницы не затрудняет


def stamp_lines(email, when, ip, code):
    """Строки знака. Только латиница и цифры: шрифт Pillow по умолчанию не содержит кириллицы."""
    safe_email = email.encode("ascii", "replace").decode()
    return [safe_email, f"{when:%Y-%m-%d %H:%M} UTC  {ip}".strip(), f"#{code}" if code else ""]


def _font(size):
    return ImageFont.load_default(size=size)


def apply(png, lines):
    """Возвращает PNG с повторяющимся диагональным знаком по всей странице."""
    lines = [line for line in lines if line]
    if not lines:
        return png
    base = Image.open(io.BytesIO(png)).convert("RGBA")
    width, height = base.size
    size = max(12, min(width, height) // 42)
    font = _font(size)

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    line_h = int(size * 1.35)
    text_w = max(int(probe.textlength(line, font=font)) for line in lines)
    tile = Image.new("RGBA", (text_w + size * 2, line_h * len(lines) + size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    for i, line in enumerate(lines):
        draw.text((size, size // 2 + i * line_h), line, font=font, fill=(30, 30, 30, OPACITY))
    tile = tile.rotate(ANGLE, expand=True, resample=Image.BICUBIC)

    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    step_x, step_y = int(tile.width * 1.05), int(tile.height * 1.15)
    row = 0
    for y in range(-tile.height, height + tile.height, step_y):
        shift = (row % 2) * step_x // 2  # со сдвигом по рядам: нет ровных полос, которые можно обрезать
        for x in range(-tile.width + shift, width + tile.width, step_x):
            _paste_clipped(layer, tile, x, y)
        row += 1
    out = io.BytesIO()
    Image.alpha_composite(base, layer).convert("RGB").save(out, format="PNG")
    return out.getvalue()


def _paste_clipped(layer, tile, x, y):
    """alpha_composite не принимает позицию вне холста, поэтому обрезаем плитку вручную."""
    left, top = max(0, -x), max(0, -y)
    right = min(tile.width, layer.width - x)
    bottom = min(tile.height, layer.height - y)
    if right <= left or bottom <= top:
        return
    layer.alpha_composite(tile.crop((left, top, right, bottom)), (max(0, x), max(0, y)))
