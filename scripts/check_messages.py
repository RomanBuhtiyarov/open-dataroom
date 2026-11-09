"""Сверка строк в коде и шаблонах с каталогами переводов (замена makemessages без GNU gettext).

Запуск: python scripts/check_messages.py  — печатает отсутствующие msgid и завершается с кодом 1.
"""
import re
import sys
from pathlib import Path

import polib

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ["ru", "uk", "de"]  # английский — язык исходных строк, его каталог пуст

PY_RE = re.compile(r"""\b(?:_|gettext|gettext_lazy)\(\s*(["'])((?:\\.|(?!\1).)*)\1""")
TPL_RE = re.compile(r"""\{%\s*(?:translate|trans)\s+(["'])((?:\\.|(?!\1).)*)\1""")


BLOCK_RE = re.compile(r"\{%\s*blocktranslate[^%]*%\}(.*?)\{%\s*endblocktranslate\s*%\}", re.S)
VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def used_msgids():
    found = {}
    for base in (ROOT / "apps", ROOT / "dataroom"):
        for path in base.rglob("*.py"):
            if "migrations" in path.parts:
                continue
            for match in PY_RE.finditer(path.read_text(encoding="utf-8")):
                found.setdefault(match.group(2).replace('\\"', '"'), path.relative_to(ROOT).as_posix())
    for path in (ROOT / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for match in TPL_RE.finditer(text):
            found.setdefault(match.group(2), path.relative_to(ROOT).as_posix())
        for match in BLOCK_RE.finditer(text):
            found.setdefault(VAR_RE.sub(r"%(\1)s", match.group(1).strip()), path.relative_to(ROOT).as_posix())
    return found


def catalog(locale):
    po = polib.pofile(str(ROOT / "locale" / locale / "LC_MESSAGES" / "django.po"))
    return {entry.msgid: entry.msgstr for entry in po if entry.msgid}


def missing():
    used = used_msgids()
    result = {}
    for locale in LOCALES:
        cat = catalog(locale)
        absent = sorted(m for m in used if not cat.get(m))
        if absent:
            result[locale] = absent
    return result


if __name__ == "__main__":
    problems = missing()
    for locale, ids in problems.items():
        print(f"[{locale}] {len(ids)} missing")
        for msgid in ids:
            print("   ", msgid)
    sys.exit(1 if problems else 0)
