import segno
from django.utils.safestring import mark_safe


def qr_svg(data, scale=5):
    """QR-код как встроенный SVG. Строится на сервере: данные не уходят во внешние сервисы."""
    svg = segno.make(data, error="m").svg_inline(scale=scale, border=2, dark="#000000", light="#ffffff")
    return mark_safe(svg)  # noqa: S308 - SVG собран библиотекой из наших данных
