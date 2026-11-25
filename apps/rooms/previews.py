"""Растровые превью страниц PDF (pypdfium2, без внешних программ).

Файл-оригинал не отдаётся: посетитель без права скачивания видит только картинки страниц.
Картинки строятся по требованию и кэшируются в приватном томе; доступ к ним проверяет политика на каждый запрос.

Разбор PDF выполняет отдельный процесс (render_worker) с ограничениями: время, память, процессорное
время (на Linux через setrlimit), чистое окружение без секретов приложения, размер ответа.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from functools import partial

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

MAX_PAGES = 500
MAX_SIDE_PX = 1600
WORKER_TIMEOUT = 30  # секунд
WORKER_MEMORY = 1024 * 1024 * 1024  # 1 ГиБ адресного пространства (Linux)
WORKER_CPU = 25  # секунд процессорного времени (Linux)
MAX_OUTPUT = 40 * 1024 * 1024
STAMP_TIMEOUT = 120  # штамп на каждой странице большого PDF строится дольше одной картинки
STAMP_CPU = 100
STAMP_MAX_SOURCE = 100 * 1024 * 1024
STAMP_MAX_RESULT = 400 * 1024 * 1024
STAMP_KEEP_SECONDS = 15 * 60  # столько живёт штампованный файл: за это время докачка получает тот же файл
WORKER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "render_worker.py")


class PreviewError(Exception):
    """Страницу не удалось построить (файл недоступен, номер вне диапазона, повреждённый PDF, превышен лимит)."""


def _limits(cpu=WORKER_CPU):
    """Выполняется в дочернем процессе до запуска (только POSIX)."""
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (WORKER_MEMORY, WORKER_MEMORY))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _clean_env():
    """Ни одна переменная приложения (SECRET_KEY, пароль базы, SMTP) в процесс не попадает."""
    keep = ("PATH", "SYSTEMROOT", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP")
    return {k: v for k, v in os.environ.items() if k in keep}


def _run_worker(args, stdin=None, timeout=WORKER_TIMEOUT, cpu=WORKER_CPU):
    env = _clean_env()
    kwargs = {"preexec_fn": partial(_limits, cpu)} if os.name == "posix" else {}
    with tempfile.TemporaryDirectory() as cwd:
        try:
            result = subprocess.run(
                [sys.executable, "-I", WORKER_SCRIPT, *args],
                input=stdin, capture_output=True, timeout=timeout, env=env, cwd=cwd, **kwargs,
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


# --- PDF со знаком для скачивания ---------------------------------------------


def stamped_dir(room_id):
    return settings.PRIVATE_MEDIA_ROOT / "stamped" / str(room_id)


def _stamp_name(user_id, stored_name, code):
    return f"u{int(user_id)}-{stored_name}-{code}.pdf"


def _purge_stamped(now):
    """Старые штампованные файлы всех комнат удаляются при каждой новой выдаче: отдельная очередь не нужна."""
    root = settings.PRIVATE_MEDIA_ROOT / "stamped"
    if not root.is_dir():
        return
    for item in root.rglob("*"):
        try:
            if item.is_file() and now - item.stat().st_mtime > STAMP_KEEP_SECONDS:
                item.unlink()
        except OSError:
            pass


def recent_stamped(room_id, user_id, stored_name):
    """(путь, код) свежего штампованного файла этого пользователя для этой версии или None."""
    directory = stamped_dir(room_id)
    if not directory.is_dir():
        return None
    prefix, suffix, now, best = f"u{int(user_id)}-{stored_name}-", ".pdf", time.time(), None
    for item in directory.iterdir():
        name = item.name
        if not (name.startswith(prefix) and name.endswith(suffix)):
            continue
        try:
            age = now - item.stat().st_mtime
        except OSError:
            continue
        if age <= STAMP_KEEP_SECONDS and (best is None or age < best[0]):
            best = (age, item, name[len(prefix):-len(suffix)])
    return (best[1], best[2]) if best else None


def stamp_pdf(room_id, user_id, stored_name, source_path, code, lines):
    """Строит копию PDF со знаком в отдельном процессе и возвращает путь к ней.

    Исходный файл не меняется. При любой ошибке бросает PreviewError: вызывающий не должен отдавать файл без знака.
    """
    if os.path.getsize(source_path) > STAMP_MAX_SOURCE:
        raise PreviewError("source too large")
    directory = stamped_dir(room_id)
    directory.mkdir(parents=True, exist_ok=True)
    _purge_stamped(time.time())
    target = directory / _stamp_name(user_id, stored_name, code)
    tmp = directory / f".{target.name}.tmp"
    try:
        _run_worker(["stamp", str(source_path), str(tmp)], stdin=json.dumps(list(lines)).encode("utf-8"),
                    timeout=STAMP_TIMEOUT, cpu=STAMP_CPU)
        if not tmp.exists() or tmp.stat().st_size == 0 or tmp.stat().st_size > STAMP_MAX_RESULT:
            raise PreviewError("bad result")
        with open(tmp, "rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise PreviewError("not a PDF")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return target
