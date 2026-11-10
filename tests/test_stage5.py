"""Этап 5: резервная копия и восстановление, изоляция каталогов экземпляров."""
import io
import json
import shutil
import tarfile

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import User
from apps.audit import events
from apps.audit.models import AuditEvent
from apps.core import backup
from apps.rooms import services
from apps.rooms.models import Document, DocumentVersion, Folder, Room

from .helpers import PDF, make_admin, pdf_upload


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = tmp_path / "media"
    settings.PRIVATE_MEDIA_ROOT.mkdir()
    return settings.PRIVATE_MEDIA_ROOT


def seed():
    admin = make_admin()
    room = Room.objects.create(name="Alpha")
    folder = Folder.objects.create(room=room, name="Finance")
    document, version = services.create_document(folder, "Plan", pdf_upload(), admin)
    return admin, room, document, version


def stored_path(media, version):
    return media / "documents" / str(version.document.folder.room_id) / version.stored_name


def rewrite(source, target, edit):
    """Копирует архив, давая `edit(members: dict)` возможность подменить содержимое."""
    with tarfile.open(source) as src:
        members = {m.name: src.extractfile(m).read() for m in src.getmembers() if m.isfile()}
    edit(members)
    with tarfile.open(target, "w:gz") as out:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            out.addfile(info, io.BytesIO(data))


@pytest.mark.django_db(transaction=True)
def test_backup_and_restore_roundtrip(media, tmp_path):
    admin, room, document, version = seed()
    original = stored_path(media, version).read_bytes()
    # Кэш превью не должен попасть в копию.
    (media / "previews" / "1").mkdir(parents=True)
    (media / "previews" / "1" / "x.png").write_bytes(b"cache")
    archive = tmp_path / "b.tar.gz"
    call_command("backup_instance", str(archive))
    assert archive.exists() and not (tmp_path / "b.tar.gz.part").exists()
    with tarfile.open(archive) as t:
        names = t.getnames()
    assert not any(n.startswith("media/previews") for n in names)
    assert AuditEvent.objects.filter(code=events.BACKUP_CREATED).exists()

    # Катастрофа: база пуста, файлы потеряны.
    Document.objects.all().delete()
    User.objects.all().delete()
    shutil.rmtree(media)
    media.mkdir()
    call_command("restore_instance", str(archive), "--replace")

    assert User.objects.filter(email="boss@example.com").exists()
    restored = DocumentVersion.objects.get()
    assert stored_path(media, restored).read_bytes() == original
    assert AuditEvent.objects.filter(code=events.BACKUP_RESTORED).exists()


@pytest.mark.django_db(transaction=True)
def test_backup_refuses_to_overwrite_a_file(tmp_path):
    seed()
    target = tmp_path / "b.tar.gz"
    target.write_bytes(b"keep")
    with pytest.raises(CommandError):
        call_command("backup_instance", str(target))
    assert target.read_bytes() == b"keep"


@pytest.mark.django_db(transaction=True)
def test_restore_refuses_when_instance_has_data(tmp_path):
    seed()
    archive = tmp_path / "b.tar.gz"
    call_command("backup_instance", str(archive))
    with pytest.raises(CommandError, match="already has data"):
        call_command("restore_instance", str(archive))


@pytest.mark.django_db(transaction=True)
def test_damaged_archive_changes_nothing(media, tmp_path):
    admin, room, document, version = seed()
    archive = tmp_path / "b.tar.gz"
    call_command("backup_instance", str(archive))
    broken = tmp_path / "broken.tar.gz"
    key = f"media/documents/{room.pk}/{version.stored_name}"
    rewrite(archive, broken, lambda m: m.__setitem__(key, b"tampered"))
    path = stored_path(media, version)
    path.write_bytes(b"live")
    with pytest.raises(CommandError, match="checksum"):
        call_command("restore_instance", str(broken), "--replace")
    assert path.read_bytes() == b"live"  # живые данные не тронуты
    assert User.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_archive_with_unsafe_path_is_rejected(tmp_path):
    seed()
    archive = tmp_path / "b.tar.gz"
    call_command("backup_instance", str(archive))
    evil = tmp_path / "evil.tar.gz"

    def edit(members):
        manifest = json.loads(members["manifest.json"])
        manifest["files"]["../../escape.txt"] = "0" * 64
        members["manifest.json"] = json.dumps(manifest).encode()
        members["media/../../escape.txt"] = b"x"

    rewrite(archive, evil, edit)
    with pytest.raises(CommandError, match="unsafe"):
        call_command("restore_instance", str(evil), "--replace")
    assert not (tmp_path.parent / "escape.txt").exists()


@pytest.mark.django_db(transaction=True)
def test_backup_from_newer_program_is_refused(tmp_path):
    seed()
    archive = tmp_path / "b.tar.gz"
    call_command("backup_instance", str(archive))
    newer = tmp_path / "newer.tar.gz"

    def edit(members):
        manifest = json.loads(members["manifest.json"])
        manifest["migrations"]["rooms"] = "9999_from_the_future"
        members["manifest.json"] = json.dumps(manifest).encode()

    rewrite(archive, newer, edit)
    with pytest.raises(CommandError, match="newer"):
        call_command("restore_instance", str(newer), "--replace")


def test_not_an_archive(tmp_path):
    bogus = tmp_path / "x.tar.gz"
    bogus.write_bytes(b"not an archive")
    with pytest.raises(backup.BackupError):
        backup.read_archive(bogus, tmp_path / "w")


@pytest.mark.django_db(transaction=True)
def test_backup_contains_no_secrets(settings, tmp_path):
    seed()
    archive = tmp_path / "b.tar.gz"
    call_command("backup_instance", str(archive))
    with tarfile.open(archive) as t:
        manifest = t.extractfile("manifest.json").read().decode()
    assert settings.SECRET_KEY not in manifest


def test_two_instances_use_separate_cookies_and_volumes():
    """Экземпляры на одном домене не делят cookie; тома и базы задаются окружением."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    code = "from django.conf import settings as s;print(s.SESSION_COOKIE_NAME,s.CSRF_COOKIE_NAME,s.PRIVATE_MEDIA_ROOT.as_posix(),s.ALLOWED_HOSTS)"

    def run(**extra):
        env = {k: v for k, v in os.environ.items() if k not in ("DJANGO_SETTINGS_MODULE", "DATABASE_URL")}
        env.update(DJANGO_DEBUG="1", SECRET_KEY="k", **extra)
        out = subprocess.run(
            [sys.executable, "-c", "import django,os;django.setup();" + code],
            cwd=root, env={**env, "DJANGO_SETTINGS_MODULE": "dataroom.settings"}, capture_output=True, text=True,
        )
        assert out.returncode == 0, out.stderr
        return out.stdout.strip()

    a = run(PUBLIC_BASE_URL="https://a.example.com", SESSION_COOKIE_NAME="a_s", CSRF_COOKIE_NAME="a_c", PRIVATE_MEDIA_ROOT="/data/a")
    b = run(PUBLIC_BASE_URL="https://b.example.com", SESSION_COOKIE_NAME="b_s", CSRF_COOKIE_NAME="b_c", PRIVATE_MEDIA_ROOT="/data/b")
    assert "a_s" in a and "/data/a" in a and "a.example.com" in a and "b.example.com" not in a
    assert "b_s" in b and "/data/b" in b and "b.example.com" in b and "a.example.com" not in b
