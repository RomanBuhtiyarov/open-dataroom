"""Растровые превью страниц PDF (pypdfium2, без внешних программ).

Файл-оригинал не отдаётся: посетитель без права скачивания видит только картинки страниц.
Картинки строятся по требованию и кэшируются в приватном томе; доступ к ним проверяет политика на каждый запрос.

Разбор PDF выполняет отдельный процесс (render_worker) с ограничениями: время, память, процессорное
время (на Linux через setrlimit), чистое окружение без секретов приложения, размер ответа.
"""
import os
import subprocess
import sys
import tempfile

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

MAX_PAGES = 500
MAX_SIDE_PX = 1600
WORKER_TIMEOUT = 30  # секунд
WORKER_MEMORY = 1024 * 1024 * 1024  # 1 ГиБ адресного пространства (Linux)
WORKER_CPU = 25  # секунд процессорного времени (Linux)
MAX_OUTPUT = 40 * 1024 * 1024
WORKER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "render_worker.py")


class PreviewError(Exception):
    """Страницу не удалось построить (файл недоступен, номер вне диапазона, повреждённый PDF, превышен лимит)."""


def _limits():
    """Выполняется в дочернем процессе до запуска (только POSIX)."""
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (WORKER_MEMORY, WORKER_MEMORY))
    resource.setrlimit(resource.RLIMIT_CPU, (WORKER_CPU, WORKER_CPU))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _clean_env():
    """Ни одна переменная приложения (SECRET_KEY, пароль базы, SMTP) в процесс не попадает."""
    keep = ("PATH", "SYSTEMROOT", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP")
    return {k: v for k, v in os.environ.items() if k in keep}


def _run_worker(args, stdin=None):
    env = _clean_env()
    kwargs = {"preexec_fn": _limits} if os.name == "posix" else {}
    with tempfile.TemporaryDirectory() as cwd:
        try:
            result = subprocess.run(
                [sys.executable, "-I", WORKER_SCRIPT, *args],
                input=stdin, capture_output=True, timeout=WORKER_TIMEOUT, env=env, cwd=cwd, **kwargs,
            )
        except subprocess.TimeoutExpired as exc:
            raise PreviewError("worker timed out") from exc
        except OSError as exc:
            raise PreviewError("worker cannot start") from exc
    if result.returncode != 0 or len(result.stdout) > MAX_OUTPUT:
        raise PreviewError("worker failed")
    return result.stdout


def pages_from_bytes(data):
    """Число страниц загружаемого PDF. Нечитаемые и защищённые паролем файлы отклоняются."""
    unreadable = ValidationError(_("The PDF file cannot be read or is protected by a password."))
    try:
        count = int(_run_worker(["count"], stdin=data).decode() or 0)
    except (PreviewError, ValueError):
        raise unreadable
    if count < 1:
        raise unreadable
    if count > MAX_PAGES:
        raise ValidationError(_("The PDF has too many pages."))
    return count


def pages_from_path(path):
    try:
        return int(_run_worker(["count-file", str(path)]).decode())
    except ValueError as exc:
        raise PreviewError("bad page count") from exc


def cache_path(room_id, stored_name, number):
    return settings.PRIVATE_MEDIA_ROOT / "previews" / str(room_id) / stored_name / f"{number}.png"


def render_page(room_id, stored_name, source_path, number):
    """PNG страницы `number` (с 1). Использует кэш; результат записывается атомарно."""
    cached = cache_path(room_id, stored_name, number)
    if cached.exists():
        return cached.read_bytes()
    payload = _run_worker(["render", str(source_path), str(int(number))])
    if not payload.startswith(b"\x89PNG"):
        raise PreviewError("not a PNG")
    cached.parent.mkdir(parents=True, exist_ok=True)
    tmp = cached.with_name(f".{cached.name}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, cached)
    return payload
