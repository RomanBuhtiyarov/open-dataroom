"""Резервная копия экземпляра: база и документы в одном архиве.

Порядок важен. Файлы документов неизменяемы и пишутся раньше строки в базе, поэтому
сначала снимается база, потом копируются файлы: каждая строка из копии ссылается на файл,
который есть в архиве. Лишние файлы (запись шла во время копирования) безвредны.

В архив не попадают: SECRET_KEY и остальные секреты (они в .env), кэш превью (строится заново).
Архив содержит документы и хэши паролей: храните его зашифрованным и отдельно от сервера.
"""
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.db.migrations.loader import MigrationLoader

FORMAT = 1
# Каталоги тома, которые входят в копию. previews/ — кэш, его не копируем.
MEDIA_DIRS = ("documents", "branding")


class BackupError(Exception):
    pass


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _engine():
    vendor = connection.vendor
    if vendor not in ("sqlite", "postgresql"):
        raise BackupError(f"Unsupported database: {vendor}.")
    return vendor


def _pg_env():
    db = settings.DATABASES["default"]
    env = dict(os.environ)
    if db.get("PASSWORD"):
        env["PGPASSWORD"] = db["PASSWORD"]  # только в окружении дочернего процесса, не в аргументах
    return env


def _pg_args():
    db = settings.DATABASES["default"]
    args = []
    if db.get("HOST"):
        args += ["-h", db["HOST"]]
    if db.get("PORT"):
        args += ["-p", str(db["PORT"])]
    if db.get("USER"):
        args += ["-U", db["USER"]]
    return args


def _run(cmd, env):
    try:
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    except FileNotFoundError:
        raise BackupError(f"{cmd[0]} is not installed.")
    if result.returncode != 0:
        raise BackupError(f"{cmd[0]} failed: {result.stderr.strip()[:500]}")


def _dump_db(target):
    if _engine() == "sqlite":
        destination = sqlite3.connect(str(target))
        try:
            connection.ensure_connection()
            connection.connection.backup(destination)
        finally:
            destination.close()
        return "db.sqlite3"
    name = settings.DATABASES["default"]["NAME"]
    _run(["pg_dump", "-Fc", "--no-owner", *_pg_args(), "-f", str(target), name], _pg_env())
    return "db.pgdump"


def applied_migrations():
    """Последняя применённая миграция каждого приложения: чтобы не восстановить в несовместимый код."""
    loader = MigrationLoader(connection)
    latest = {}
    for app, name in sorted(loader.applied_migrations):
        latest[app] = max(latest.get(app, ""), name)
    return latest


def create_backup(destination):
    """Создаёт архив по пути `destination`. Возвращает манифест."""
    destination = Path(destination)
    if destination.exists():
        raise BackupError("The target file already exists.")
    media = Path(settings.PRIVATE_MEDIA_ROOT)
    part = destination.with_name(destination.name + ".part")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        db_name = _dump_db(tmp / "dump")  # сначала база, потом файлы
        manifest = {
            "format": FORMAT,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "engine": _engine(),
            "db_file": db_name,
            "db_sha256": _sha256(tmp / "dump"),
            "migrations": applied_migrations(),
            "files": {},
        }
        try:
            with tarfile.open(part, "w:gz") as archive:
                archive.add(tmp / "dump", arcname=db_name)
                for top in MEDIA_DIRS:
                    base = media / top
                    if not base.is_dir():
                        continue
                    for path in sorted(base.rglob("*")):
                        if path.is_file() and not path.is_symlink():
                            rel = path.relative_to(media).as_posix()
                            manifest["files"][rel] = _sha256(path)
                            archive.add(path, arcname="media/" + rel)
                data = json.dumps(manifest, indent=2, sort_keys=True).encode()
                info = tarfile.TarInfo("manifest.json")
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            os.chmod(part, 0o600)
            os.replace(part, destination)
        finally:
            if part.exists():
                part.unlink()
    return manifest


def _safe_member(name):
    path = Path(name)
    return bool(path.parts) and not path.is_absolute() and ".." not in path.parts and "\\" not in name


def read_archive(archive_path, workdir):
    """Распаковывает и полностью проверяет архив. Возвращает манифест. Экземпляр не трогает."""
    workdir = Path(workdir)
    try:
        archive = tarfile.open(archive_path, "r:gz")
    except (tarfile.TarError, OSError):
        raise BackupError("The file is not a valid backup archive.")
    with archive:
        try:
            members = {m.name: m for m in archive.getmembers()}
            if "manifest.json" not in members or not members["manifest.json"].isfile():
                raise BackupError("The archive has no manifest.")
            manifest = json.loads(archive.extractfile("manifest.json").read())
            if manifest.get("format") != FORMAT:
                raise BackupError("Unsupported backup format.")
            wanted = {manifest["db_file"]: manifest["db_sha256"]}
            wanted.update({"media/" + rel: digest for rel, digest in manifest["files"].items()})
            for name in wanted:
                if not _safe_member(name) or name not in members or not members[name].isfile():
                    raise BackupError("The archive is incomplete or contains unsafe paths.")
            for name, digest in wanted.items():
                target = workdir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(name) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                if _sha256(target) != digest:
                    raise BackupError("A checksum does not match: the archive is damaged.")
        except (tarfile.TarError, EOFError, KeyError, ValueError, OSError):
            raise BackupError("The archive is damaged or malformed.")
    return manifest


def _check_compatible(manifest):
    if manifest["engine"] != _engine():
        raise BackupError("The backup was made on a different database engine.")
    known = {}
    for app, name in MigrationLoader(connection).graph.nodes:
        known.setdefault(app, set()).add(name)
    for app, name in manifest["migrations"].items():
        if name not in known.get(app, ()):
            raise BackupError("The backup is newer than this version of the program. Update the program first.")


def restore_backup(archive_path, replace=False):
    """Восстанавливает базу и файлы. Без `replace` отказывается, если в экземпляре уже есть пользователи."""
    from apps.accounts.models import User

    media = Path(settings.PRIVATE_MEDIA_ROOT)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        manifest = read_archive(archive_path, tmp)
        _check_compatible(manifest)
        if not replace:
            try:
                has_data = User.objects.exists()
            except Exception:
                has_data = False  # таблиц ещё нет: пустая база
            if has_data:
                raise BackupError("The instance already has data. Use --replace to overwrite it.")
        # Сначала файлы, потом база: строки в базе не должны ссылаться на отсутствующие файлы.
        for rel in manifest["files"]:
            target = media / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            staged = target.with_name(target.name + ".restoring")
            shutil.copyfile(tmp / "media" / rel, staged)
            os.replace(staged, target)
        dump = tmp / manifest["db_file"]
        if _engine() == "sqlite":
            source = sqlite3.connect(str(dump))
            try:
                connection.ensure_connection()
                source.backup(connection.connection)
            finally:
                source.close()
        else:
            name = settings.DATABASES["default"]["NAME"]
            connection.close()
            _run(["pg_restore", "--clean", "--if-exists", "--no-owner", "-d", name, *_pg_args(), str(dump)], _pg_env())
            connection.close()
    return manifest
