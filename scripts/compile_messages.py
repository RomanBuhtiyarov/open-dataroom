"""Компиляция .po в .mo без утилит GNU gettext (для разработки на Windows).

В Docker-образе используется штатная `manage.py compilemessages`. Файлы .mo в репозиторий не входят.
"""
from pathlib import Path

import polib

LOCALE_DIR = Path(__file__).resolve().parent.parent / "locale"


def compile_all():
    compiled = []
    for po_path in LOCALE_DIR.glob("*/LC_MESSAGES/django.po"):
        polib.pofile(str(po_path)).save_as_mofile(str(po_path.with_suffix(".mo")))
        compiled.append(po_path.parent.parent.name)
    return sorted(compiled)


if __name__ == "__main__":
    print("compiled:", ", ".join(compile_all()))
