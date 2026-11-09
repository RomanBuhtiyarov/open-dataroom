"""Фильтр журналов: вырезает токены приглашений (параметр t=) из записей о запросах."""
import logging
import re

TOKEN_RE = re.compile(r"([?&]t=)[^\s&\"']+")


def redact(text):
    return TOKEN_RE.sub(r"\1[redacted]", text)


class RedactTokenFilter(logging.Filter):
    def filter(self, record):
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:  # noqa: BLE001 - фильтр не должен ломать запись журнала
            pass
        return True
