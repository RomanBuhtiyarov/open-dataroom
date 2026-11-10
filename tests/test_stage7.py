"""Этап 7: перемещение и удаление, стартовые комнаты, изоляция разбора PDF."""
import subprocess
import sys
from unittest import mock

import pytest
from django.urls import reverse

from apps.audit import events
from apps.audit.models import AuditEvent
from apps.rooms import files, previews, services
from apps.rooms.models import AccessGrant, Document, DocumentVersion, Folder, Room

from .helpers import PDF, make_pdf, pdf_upload, verified_admin_client, viewer


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = tmp_path
    return tmp_path


def setup_room(client_admin):
    admin = client_admin
    room = Room.objects.create(name="Alpha")
    a = services.create_folder(room, None, "A")
    b = services.create_folder(room, None, "B")
    child = services.create_folder(room, a, "Child")
    return admin, room, a, b, child


def grant(user, room, folder=None, download=False):
    return AccessGrant.objects.create(user=user, room=room, folder=folder, can_download=download)


# --- Перемещение папок -------------------------------------------------------


@pytest.mark.django_db
def test_move_folder_changes_effective_access():
    client, admin = verified_admin_client()
    _, room, a, b, child = setup_room(admin)
    doc, _v = services.create_document(child, "Plan", pdf_upload(), admin)
    vclient, user = viewer()
    grant(user, room, folder=b)
    assert vclient.get(reverse("document_detail", args=[doc.pk])).status_code == 404
    response = client.post(reverse("folder_move", args=[a.pk]), {"destination": b.pk})
    assert response.status_code == 302
    a.refresh_from_db()
    assert a.parent_id == b.pk
    # теперь документ лежит в потомках B и виден получателю права на B
    assert vclient.get(reverse("document_detail", args=[doc.pk])).status_code == 200
    ev = AuditEvent.objects.get(code=events.FOLDER_MOVED)
    assert ev.target == f"folder:{a.pk}>{b.pk}" and ev.actor == admin


@pytest.mark.django_db
def test_move_out_of_granted_folder_removes_access():
    client, admin = verified_admin_client()
    _, room, a, b, child = setup_room(admin)
    doc, _v = services.create_document(child, "Plan", pdf_upload(), admin)
    vclient, user = viewer()
    grant(user, room, folder=a)
    assert vclient.get(reverse("document_detail", args=[doc.pk])).status_code == 200
    client.post(reverse("folder_move", args=[child.pk]), {"destination": b.pk})
    assert vclient.get(reverse("document_detail", args=[doc.pk])).status_code == 404
    assert vclient.get(reverse("document_page", args=[doc.pk, 1])).status_code == 404


@pytest.mark.django_db
def test_folder_cannot_move_into_itself_descendant_or_other_room():
    client, admin = verified_admin_client()
    _, room, a, b, child = setup_room(admin)
    other = Room.objects.create(name="Beta")
    foreign = services.create_folder(other, None, "X")
    for target in (a.pk, child.pk):
        client.post(reverse("folder_move", args=[a.pk]), {"destination": target})
    assert client.post(reverse("folder_move", args=[a.pk]), {"destination": foreign.pk}).status_code == 404
    a.refresh_from_db()
    assert a.parent_id is None
    assert not AuditEvent.objects.filter(code=events.FOLDER_MOVED).exists()


@pytest.mark.django_db
def test_move_folder_refuses_name_clash_and_depth(settings):
    client, admin = verified_admin_client()
    _, room, a, b, child = setup_room(admin)
    services.create_folder(room, b, "Child")
    client.post(reverse("folder_move", args=[child.pk]), {"destination": b.pk})
    child.refresh_from_db()
    assert child.parent_id == a.pk  # имя занято
    deep = b
    for i in range(7):
        deep = services.create_folder(room, deep, f"L{i}")
    client.post(reverse("folder_move", args=[a.pk]), {"destination": deep.pk})
    a.refresh_from_db()
    assert a.parent_id is None  # дерево стало бы слишком глубоким


@pytest.mark.django_db
def test_folder_destinations_exclude_subtree():
    client, admin = verified_admin_client()
    _, room, a, b, child = setup_room(admin)
    ids = {f.pk for f in services.folder_destinations(a)}
    assert ids == {b.pk}  # не сама папка, не потомок, не текущий родитель


@pytest.mark.django_db
def test_viewer_cannot_move_or_delete():
    client, admin = verified_admin_client()
    _, room, a, b, child = setup_room(admin)
    doc, _v = services.create_document(a, "Plan", pdf_upload(), admin)
    vclient, user = viewer()
    grant(user, room)
    assert vclient.post(reverse("folder_move", args=[a.pk]), {"destination": b.pk}).status_code in (302, 403, 404)
    a.refresh_from_db()
    assert a.parent_id is None
    vclient.post(reverse("document_delete", args=[doc.pk]), {"confirm": "1"})
    assert Document.objects.filter(pk=doc.pk).exists()


# --- Документы ---------------------------------------------------------------


@pytest.mark.django_db
def test_move_document_within_room_only():
    client, admin = verified_admin_client()
    _, room, a, b, child = setup_room(admin)
    doc, _v = services.create_document(a, "Plan", pdf_upload(), admin)
    other = Room.objects.create(name="Beta")
    foreign = services.create_folder(other, None, "X")
    assert client.post(reverse("document_move", args=[doc.pk]), {"destination": foreign.pk}).status_code == 404
    assert client.post(reverse("document_move", args=[doc.pk]), {"destination": b.pk}).status_code == 302
    doc.refresh_from_db()
    assert doc.folder_id == b.pk
    assert AuditEvent.objects.filter(code=events.DOCUMENT_MOVED, target=f"document:{doc.pk}>{b.pk}").exists()


@pytest.mark.django_db
def test_move_document_title_clash_refused():
    client, admin = verified_admin_client()
    _, room, a, b, child = setup_room(admin)
    doc, _v = services.create_document(a, "Plan", pdf_upload(), admin)
    services.create_document(b, "Plan", pdf_upload(), admin)
    client.post(reverse("document_move", args=[doc.pk]), {"destination": b.pk})
    doc.refresh_from_db()
    assert doc.folder_id == a.pk


@pytest.mark.django_db(transaction=True)
def test_delete_document_removes_rows_files_and_previews(media):
    client, admin = verified_admin_client()
    _, room, a, b, child = setup_room(admin)
    doc, version = services.create_document(a, "Plan", pdf_upload(), admin)
    path = files.file_path(room.pk, version.stored_name)
    assert path.exists()
    previews.render_page(room.pk, version.stored_name, path, 1)
    cache = previews.cache_path(room.pk, version.stored_name, 1)
    assert cache.exists()
    # без подтверждения ничего не удаляется
    client.post(reverse("document_delete", args=[doc.pk]))
    assert Document.objects.filter(pk=doc.pk).exists() and path.exists()
    response = client.post(reverse("document_delete", args=[doc.pk]), {"confirm": "1"})
    assert response.status_code == 302
    assert not Document.objects.filter(pk=doc.pk).exists()
    assert not DocumentVersion.objects.filter(pk=version.pk).exists()
    assert not path.exists() and not cache.exists()
    assert AuditEvent.objects.filter(code=events.DOCUMENT_DELETED, target=f"document:{doc.pk}").exists()
    vclient, user = viewer()
    grant(user, room)
    assert vclient.get(reverse("document_page", args=[doc.pk, 1])).status_code == 404


# --- Стартовые комнаты ---------------------------------------------------------


@pytest.mark.django_db
def test_starter_rooms_are_created_once_without_grants():
    client, admin = verified_admin_client()
    first = client.post(reverse("starter_rooms"))
    assert first.status_code == 302
    assert Room.objects.count() == 3
    names = set(Room.objects.values_list("name", flat=True))
    assert names == {"Investor overview", "Due diligence", "Restricted legal"}
    assert Folder.objects.filter(room__template_key="restricted_legal").count() == 3
    # повтор не создаёт дубликатов и не трогает права
    room = Room.objects.get(template_key="overview")
    vclient, user = viewer()
    g = grant(user, room)
    client.post(reverse("starter_rooms"))
    assert Room.objects.count() == 3 and AccessGrant.objects.get() == g
    assert AuditEvent.objects.filter(code=events.ROOM_STARTER_APPLIED).count() == 2
    # посетитель без права не видит закрытую комнату
    restricted = Room.objects.get(template_key="restricted_legal")
    assert vclient.get(reverse("room_detail", args=[restricted.pk])).status_code == 404


@pytest.mark.django_db
def test_starter_rooms_only_for_admin():
    vclient, user = viewer()
    assert vclient.post(reverse("starter_rooms")).status_code in (302, 403)
    assert Room.objects.count() == 0


# --- Изоляция разбора PDF --------------------------------------------------------


def test_parsing_runs_in_a_separate_process(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(make_pdf(pages=3))
    seen = {}
    real = subprocess.run

    def spy(cmd, **kwargs):
        seen["cmd"], seen["env"] = cmd, kwargs.get("env", {})
        return real(cmd, **kwargs)

    with mock.patch("apps.rooms.previews.subprocess.run", side_effect=spy):
        assert previews.pages_from_path(pdf) == 3
    assert seen["cmd"][0] == sys.executable and "-I" in seen["cmd"]
    assert seen["cmd"][2].endswith("render_worker.py")


def test_worker_does_not_receive_application_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "super-secret")
    monkeypatch.setenv("DATABASE_URL", "postgres://u:pw@h/db")
    monkeypatch.setenv("EMAIL_HOST_PASSWORD", "smtp-secret")
    env = previews._clean_env()
    assert not {"SECRET_KEY", "DATABASE_URL", "EMAIL_HOST_PASSWORD"} & set(env)


def test_worker_timeout_and_crash_become_preview_errors(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(PDF)
    with mock.patch("apps.rooms.previews.subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)):
        with pytest.raises(previews.PreviewError):
            previews.render_page(1, "name", pdf, 1)
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 not really")
    with pytest.raises(previews.PreviewError):
        previews.render_page(1, "name2", broken, 1)


def test_non_png_output_is_rejected(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(PDF)
    fake = subprocess.CompletedProcess([], 0, stdout=b"<html>", stderr=b"")
    with mock.patch("apps.rooms.previews.subprocess.run", return_value=fake):
        with pytest.raises(previews.PreviewError):
            previews.render_page(1, "name3", pdf, 1)


@pytest.mark.django_db
def test_render_failure_gives_503_after_access_check():
    client, admin = verified_admin_client()
    room = Room.objects.create(name="Alpha")
    folder = services.create_folder(room, None, "F")
    doc, version = services.create_document(folder, "Plan", pdf_upload(), admin)
    vclient, user = viewer()
    grant(user, room)
    with mock.patch("apps.rooms.previews._run_worker", side_effect=previews.PreviewError("x")):
        ok = vclient.get(reverse("document_page", args=[doc.pk, 1]))
    assert ok.status_code == 503 and ok["Retry-After"]
    stranger, _u = viewer("s@example.com")
    with mock.patch("apps.rooms.previews._run_worker", side_effect=previews.PreviewError("x")):
        assert stranger.get(reverse("document_page", args=[doc.pk, 1])).status_code == 404  # доступ проверен раньше
