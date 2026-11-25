"""Проверки, которые имеют смысл только на PostgreSQL (в CI: TEST_DATABASE_URL). На SQLite пропускаются."""
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction

from apps.accounts.models import User
from apps.core import backup
from apps.rooms import services
from apps.rooms.models import Document, Folder, Room

from .helpers import make_admin, pdf_upload

pytestmark = pytest.mark.skipif(connection.vendor != "postgresql", reason="requires PostgreSQL")


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = tmp_path / "media"
    settings.PRIVATE_MEDIA_ROOT.mkdir()
    return settings.PRIVATE_MEDIA_ROOT


@pytest.mark.django_db(transaction=True)
def test_backup_and_restore_roundtrip_on_postgresql(tmp_path):
    """pg_dump -Fc и pg_restore: данные уходят в архив и возвращаются после потери."""
    admin = make_admin()
    room = Room.objects.create(name="Alpha")
    folder = Folder.objects.create(room=room, name="Finance")
    services.create_document(folder, "Plan", pdf_upload(), admin)
    archive = tmp_path / "backup.tar.gz"
    call_command("backup_instance", str(archive))
    assert archive.exists() and archive.stat().st_size > 0

    Document.objects.all().delete()
    Folder.objects.all().delete()
    Room.objects.all().delete()
    assert Room.objects.count() == 0

    call_command("restore_instance", str(archive), "--replace")
    assert Room.objects.get().name == "Alpha"
    assert Document.objects.get().title == "Plan"
    assert User.objects.filter(email=admin.email).exists()
    assert backup.applied_migrations()


@pytest.mark.django_db(transaction=True)
def test_restore_refuses_an_instance_with_data_without_replace(tmp_path):
    make_admin()
    archive = tmp_path / "backup.tar.gz"
    call_command("backup_instance", str(archive))
    with pytest.raises(CommandError):
        call_command("restore_instance", str(archive))
    assert User.objects.count() == 1


@pytest.mark.django_db
def test_case_insensitive_unique_names_hold_on_postgresql():
    room = Room.objects.create(name="Alpha")
    Folder.objects.create(room=room, name="Finance")
    with pytest.raises(IntegrityError), transaction.atomic():
        Folder.objects.create(room=room, name="FINANCE")
