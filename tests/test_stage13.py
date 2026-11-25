"""Этап 13: штамп в скачиваемых PDF."""
import os
import re
import time
from unittest import mock

import pypdfium2 as pdfium
import pytest
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.rooms import files, previews
from apps.rooms import services as room_services
from apps.rooms.models import AccessGrant, Room

from .helpers import make_pdf, office_upload, pdf_upload, verified_admin_client, viewer


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = tmp_path / "media"
    settings.PRIVATE_MEDIA_ROOT.mkdir()
    return settings.PRIVATE_MEDIA_ROOT


def body(response):
    return b"".join(response.streaming_content)


def text_of(data):
    document = pdfium.PdfDocument(data)
    return [document[i].get_textpage().get_text_range() for i in range(len(document))]


def setup(pages=2, email="investor@example.com", **room_extra):
    room = Room.objects.create(name="Alpha", **room_extra)
    folder = room_services.create_folder(room, None, "Docs")
    doc, _v = room_services.create_document(folder, "Plan", pdf_upload("plan.pdf", make_pdf(pages)), None)
    client, user = viewer(email)
    AccessGrant.objects.create(user=user, room=room, can_download=True)
    original = files.file_path(room.pk, doc.current_version.stored_name).read_bytes()
    return room, doc, client, user, original


@pytest.mark.django_db
def test_visitor_download_carries_a_stamp_on_every_page_as_real_text():
    room, doc, client, user, original = setup(pages=3)
    response = client.get(reverse("document_download", args=[doc.pk]))
    data = body(response)
    assert response.status_code == 200 and data.startswith(b"%PDF-") and data != original
    assert "attachment" in response["Content-Disposition"] and response["Cache-Control"] == "private, no-store"
    pages = text_of(data)
    assert len(pages) == 3
    for page in pages:
        assert "investor@example.com" in page and "UTC" in page and re.search(r"#[0-9a-f]{8}", page)


@pytest.mark.django_db
def test_original_file_on_disk_is_not_modified():
    room, doc, client, _user, original = setup()
    client.get(reverse("document_download", args=[doc.pk]))
    assert files.file_path(room.pk, doc.current_version.stored_name).read_bytes() == original


@pytest.mark.django_db
def test_admin_gets_the_original_without_a_stamp():
    room, doc, _client, _user, original = setup()
    admin_client, _admin = verified_admin_client()
    response = admin_client.get(reverse("document_download", args=[doc.pk]))
    assert body(response) == original
    event = AuditEvent.objects.get(code="document.downloaded")
    assert ":stamp:" not in event.target


@pytest.mark.django_db
def test_switched_off_watermark_gives_the_original():
    _room, doc, client, _user, original = setup(watermark_enabled=False)
    assert body(client.get(reverse("document_download", args=[doc.pk]))) == original


@pytest.mark.django_db
def test_audit_records_the_stamp_code_and_no_personal_data():
    room, doc, client, user, _original = setup()
    data = body(client.get(reverse("document_download", args=[doc.pk])))
    event = AuditEvent.objects.get(code="document.downloaded", actor=user)
    code = re.search(r"#([0-9a-f]{8})", text_of(data)[0]).group(1)
    assert event.target == f"document:{doc.pk}:v1:stamp:{code}"
    assert "@" not in event.target and "127.0.0.1" not in event.target


@pytest.mark.django_db
def test_ranges_and_repeat_requests_get_identical_bytes_and_one_code():
    _room, doc, client, user, _original = setup()
    url = reverse("document_download", args=[doc.pk])
    full = body(client.get(url))
    first = body(client.get(url, HTTP_RANGE="bytes=0-99"))
    middle_response = client.get(url, HTTP_RANGE="bytes=100-199")
    middle = body(middle_response)
    assert first == full[:100] and middle == full[100:200] and middle_response.status_code == 206
    assert middle_response["Content-Range"] == f"bytes 100-199/{len(full)}"
    assert body(client.get(url)) == full  # повторное скачивание: тот же файл
    codes = {e.target.rsplit(":", 1)[1] for e in AuditEvent.objects.filter(code="document.downloaded", actor=user)}
    assert len(codes) == 1


@pytest.mark.django_db
def test_each_visitor_gets_their_own_stamp():
    room, doc, first_client, _first, _original = setup(email="first@example.com")
    second_client, second = viewer("second@example.com")
    AccessGrant.objects.create(user=second, room=room, can_download=True)
    a = text_of(body(first_client.get(reverse("document_download", args=[doc.pk]))))[0]
    b = text_of(body(second_client.get(reverse("document_download", args=[doc.pk]))))[0]
    assert "first@example.com" in a and "second@example.com" not in a
    assert "second@example.com" in b and "first@example.com" not in b


@pytest.mark.django_db
def test_stamped_file_expires_and_is_rebuilt_with_a_new_code():
    room, doc, client, user, _original = setup()
    url = reverse("document_download", args=[doc.pk])
    first = body(client.get(url))
    directory = previews.stamped_dir(room.pk)
    old = time.time() - previews.STAMP_KEEP_SECONDS - 60
    for item in directory.iterdir():
        os.utime(item, (old, old))
    second = body(client.get(url))
    assert text_of(first)[0] != text_of(second)[0]  # другой код
    assert len(list(directory.iterdir())) == 1  # старый файл удалён
    assert AuditEvent.objects.filter(code="document.downloaded", actor=user).count() == 2


@pytest.mark.django_db
def test_when_stamping_fails_the_file_is_never_given_without_a_stamp():
    _room, doc, client, user, original = setup()
    with mock.patch.object(previews, "stamp_pdf", side_effect=previews.PreviewError("boom")):
        response = client.get(reverse("document_download", args=[doc.pk]))
    assert response.status_code == 503 and response["Retry-After"] and original not in response.content
    assert not AuditEvent.objects.filter(code="document.downloaded").exists()


@pytest.mark.django_db
def test_a_broken_pdf_on_disk_gives_503_not_a_server_error_and_not_the_file():
    room, doc, client, _user, _original = setup()
    files.file_path(room.pk, doc.current_version.stored_name).write_bytes(b"%PDF-1.4 not really a pdf")
    response = client.get(reverse("document_download", args=[doc.pk]))
    assert response.status_code == 503 and b"not really" not in response.content
    assert not any(previews.stamped_dir(room.pk).glob("*")) if previews.stamped_dir(room.pk).exists() else True


@pytest.mark.django_db
def test_new_stamped_files_are_rate_limited_per_user_but_cached_downloads_are_not():
    from apps.rooms import views

    _room, doc, client, _user, _original = setup()
    url = reverse("document_download", args=[doc.pk])
    for _ in range(views.STAMP_LIMIT + 5):
        assert client.get(url).status_code == 200  # один файл, переиспользуется
    with mock.patch.object(previews, "recent_stamped", return_value=None):
        statuses = [client.get(url).status_code for _ in range(views.STAMP_LIMIT + 2)]
    assert 429 in statuses


@pytest.mark.django_db
def test_access_checks_still_come_first():
    room, doc, _client, _user, _original = setup()
    outsider, _o = viewer("out@example.com")
    assert outsider.get(reverse("document_download", args=[doc.pk])).status_code == 404
    reader, reader_user = viewer("read@example.com")
    AccessGrant.objects.create(user=reader_user, room=room, can_download=False)
    assert reader.get(reverse("document_download", args=[doc.pk])).status_code == 403
    assert not previews.stamped_dir(room.pk).exists()


@pytest.mark.django_db
def test_office_files_are_given_unchanged_with_a_note():
    room = Room.objects.create(name="Alpha")
    folder = room_services.create_folder(room, None, "Docs")
    doc, _v = room_services.create_document(folder, "Sheet", office_upload("xlsx", "sheet.xlsx"), None)
    client, user = viewer()
    AccessGrant.objects.create(user=user, room=room, can_download=True)
    original = files.file_path(room.pk, doc.current_version.stored_name).read_bytes()
    assert body(client.get(reverse("document_download", args=[doc.pk]))) == original
    page = client.get(reverse("room_detail", args=[room.pk]) + f"?folder={folder.pk}").content.decode()
    assert "without a stamp" in page
    assert ":stamp:" not in AuditEvent.objects.get(code="document.downloaded").target


@pytest.mark.django_db
def test_pdf_pages_with_rotation_and_offset_boxes_are_stamped():
    document = pdfium.PdfDocument.new()
    page = document.new_page(300, 500)
    page.set_rotation(90)
    other = document.new_page(200, 200)
    other.set_mediabox(50, 50, 250, 250)
    import io

    buffer = io.BytesIO()
    document.save(buffer)
    room = Room.objects.create(name="Alpha")
    folder = room_services.create_folder(room, None, "Docs")
    doc, _v = room_services.create_document(folder, "Odd", pdf_upload("odd.pdf", buffer.getvalue()), None)
    client, user = viewer()
    AccessGrant.objects.create(user=user, room=room, can_download=True)
    pages = text_of(body(client.get(reverse("document_download", args=[doc.pk]))))
    assert len(pages) == 2 and all("investor" in p or "v@example.com" in p for p in pages)


def test_stamp_lines_are_ascii_so_the_standard_font_can_draw_them():
    from apps.rooms import watermark

    from datetime import datetime, timezone

    lines = watermark.stamp_lines("иван@example.com", datetime(2026, 11, 25, tzinfo=timezone.utc), "10.0.0.1", "abcd1234")
    assert all(line.isascii() for line in lines)


def test_worker_rejects_bad_input_and_does_not_import_django(tmp_path):
    import json
    import subprocess
    import sys

    script = previews.WORKER_SCRIPT
    source = tmp_path / "a.pdf"
    source.write_bytes(make_pdf(1))
    target = tmp_path / "b.pdf"
    bad = subprocess.run([sys.executable, "-I", script, "stamp", str(source), str(target)], input=b'{"x": 1}', capture_output=True)
    assert bad.returncode == 2 and not target.exists()
    empty = subprocess.run([sys.executable, "-I", script, "stamp", str(source), str(target)], input=json.dumps([]).encode(), capture_output=True)
    assert empty.returncode != 0 and not target.exists()
    ok = subprocess.run([sys.executable, "-I", script, "stamp", str(source), str(target)], input=json.dumps(["x@example.com"]).encode(), capture_output=True)
    assert ok.returncode == 0 and target.read_bytes().startswith(b"%PDF-")
    assert "django" not in open(script, encoding="utf-8").read().lower().replace("не импортирует django", "")
