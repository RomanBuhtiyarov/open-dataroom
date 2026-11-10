"""Этап 4: просмотр страниц PDF без скачивания, Range, журнал аудита, сводка и активные права."""
import csv
import datetime
import io
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.accounts.models import User
from apps.audit import events, labels
from apps.audit.models import AuditEvent
from apps.audit.service import record
from apps.core.models import InstanceSettings
from apps.invites.models import AccessRequest
from apps.rooms import previews, serving, services
from apps.rooms.models import AccessGrant, DocumentVersion, Room

from .helpers import STRONG, make_pdf, office_upload, password_login, pdf_upload, verified_admin_client, viewer


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = tmp_path
    return tmp_path


def make_room(name="Alpha"):
    return Room.objects.create(name=name)


def make_doc(folder, title="Plan", payload=None, name="plan.pdf"):
    return services.create_document(folder, title, pdf_upload(name, payload or make_pdf(3)), None)[0]


def grant(user, room, folder=None, download=False, **extra):
    return AccessGrant.objects.create(user=user, room=room, folder=folder, can_download=download, **extra)


def png_size(response):
    return Image.open(io.BytesIO(response.content)).size


def body(response):
    return b"".join(response.streaming_content) if response.streaming else response.content


# --- Просмотр страниц -------------------------------------------------------


@pytest.mark.django_db
def test_view_only_user_sees_page_images_but_cannot_get_the_original():
    room = make_room()
    doc = make_doc(room_folder := services.create_folder(room, None, "F"))
    client, user = viewer()
    grant(user, room, download=False)

    page = client.get(reverse("document_view", args=[doc.pk]))
    html = page.content.decode()
    assert page.status_code == 200 and html.count('class="page"') == 3
    assert reverse("document_download", args=[doc.pk]) not in html
    assert "View only" in html

    image = client.get(reverse("document_page", args=[doc.pk, 2]))
    assert image.status_code == 200 and image["Content-Type"] == "image/png"
    assert image.content[:8] == b"\x89PNG\r\n\x1a\n" and image["Cache-Control"] == "private, no-store"
    assert image["Content-Disposition"] == "inline" and png_size(image) == (600, 800)  # масштаб 2x
    assert client.get(reverse("document_download", args=[doc.pk])).status_code == 403
    assert room_folder.documents.count() == 1


@pytest.mark.django_db
def test_opening_the_viewer_is_audited_once_not_per_page_image():
    room = make_room()
    doc = make_doc(services.create_folder(room, None, "F"))
    client, user = viewer()
    grant(user, room)
    client.get(reverse("document_view", args=[doc.pk]))
    for n in (1, 2, 3):
        client.get(reverse("document_page", args=[doc.pk, n]))
    events_ = AuditEvent.objects.filter(code="document.viewed", actor=user)
    assert events_.count() == 1 and events_.get().target == f"document:{doc.pk}:v1"


@pytest.mark.django_db
def test_page_numbers_outside_the_document_are_404():
    room = make_room()
    doc = make_doc(services.create_folder(room, None, "F"))
    client, user = viewer()
    grant(user, room)
    for n in (0, 4, 999):
        assert client.get(reverse("document_page", args=[doc.pk, n])).status_code == 404


@pytest.mark.django_db
def test_viewer_and_pages_are_closed_without_a_grant_and_follow_revocation_expiry_and_scope(commit=None):
    room = make_room()
    a = services.create_folder(room, None, "A")
    b = services.create_folder(room, None, "B")
    doc_a, doc_b = make_doc(a, "DocA"), make_doc(b, "DocB")
    client, user = viewer()
    urls = lambda d: (reverse("document_view", args=[d.pk]), reverse("document_page", args=[d.pk, 1]))  # noqa: E731
    for url in urls(doc_a):
        assert client.get(url).status_code == 404  # нет гранта

    g = grant(user, room, folder=a)
    assert [client.get(u).status_code for u in urls(doc_a)] == [200, 200]
    assert [client.get(u).status_code for u in urls(doc_b)] == [404, 404]  # соседняя папка

    admin_client, _admin = verified_admin_client()
    admin_client.post(reverse("grant_revoke", args=[g.pk]))
    assert [client.get(u).status_code for u in urls(doc_a)] == [404, 404]  # отзыв действует сразу

    grant(user, room, folder=a, expires_at=timezone.now() - datetime.timedelta(seconds=1))
    assert client.get(urls(doc_a)[1]).status_code == 404


@pytest.mark.django_db
def test_viewer_requires_the_nda_and_pages_stay_closed_until_accepted():
    room = make_room()
    room.nda_required = True
    room.save()
    doc = make_doc(services.create_folder(room, None, "F"))
    client, user = viewer()
    grant(user, room)
    response = client.get(reverse("document_view", args=[doc.pk]))
    assert response.status_code == 302 and response["Location"] == reverse("nda_accept", args=[room.pk])
    assert client.get(reverse("document_page", args=[doc.pk, 1])).status_code == 404


@pytest.mark.django_db
def test_pages_are_rendered_once_and_then_served_from_the_private_cache(media):
    room = make_room()
    doc = make_doc(services.create_folder(room, None, "F"))
    client, user = viewer()
    grant(user, room)
    first = client.get(reverse("document_page", args=[doc.pk, 1]))
    cached = list((media / "previews").rglob("1.png"))
    assert len(cached) == 1 and str(cached[0]).startswith(str(media))
    with mock.patch("apps.rooms.previews.pdfium.PdfDocument", side_effect=AssertionError("must not reopen")):
        second = client.get(reverse("document_page", args=[doc.pk, 1]))
    assert second.content == first.content
    for url in (f"/media/previews/{room.pk}/x/1.png", f"/static/previews/{room.pk}/x/1.png"):
        assert Client().get(url).status_code == 404


@pytest.mark.django_db
def test_office_documents_have_no_preview_and_say_so():
    room = make_room()
    folder = services.create_folder(room, None, "F")
    doc = services.create_document(folder, "Sheet", office_upload("xlsx", "s.xlsx"), None)[0]
    client, user = viewer()
    grant(user, room, download=False)
    page = client.get(reverse("document_view", args=[doc.pk]))
    assert page.status_code == 200 and "only for PDF" in page.content.decode() and 'class="page"' not in page.content.decode()
    assert client.get(reverse("document_page", args=[doc.pk, 1])).status_code == 404


@pytest.mark.django_db
def test_admin_can_view_any_version_but_visitors_always_get_the_current_one():
    room = make_room()
    doc = make_doc(services.create_folder(room, None, "F"), payload=make_pdf(1, (300, 400)))
    services.add_version(doc, pdf_upload("v2.pdf", make_pdf(2, (600, 200))), None)
    admin_client, _admin = verified_admin_client()
    client, user = viewer()
    grant(user, room)
    url = reverse("document_page", args=[doc.pk, 1])
    assert png_size(admin_client.get(url + "?v=1")) == (600, 800)
    assert png_size(admin_client.get(url)) == (1200, 400)
    assert png_size(client.get(url + "?v=1")) == (1200, 400)  # посетитель не может выбрать старую версию
    assert client.get(reverse("document_view", args=[doc.pk]) + "?v=1").content.decode().count('class="page"') == 2
    assert admin_client.get(reverse("document_view", args=[doc.pk]) + "?v=1").content.decode().count('class="page"') == 1


@pytest.mark.django_db
def test_page_count_is_stored_at_upload_and_filled_in_for_old_versions():
    room = make_room()
    doc = make_doc(services.create_folder(room, None, "F"))
    version = doc.current_version
    assert version.page_count == 3
    DocumentVersion.objects.filter(pk=version.pk).update(page_count=None)
    client, user = viewer()
    grant(user, room)
    assert client.get(reverse("document_view", args=[doc.pk])).content.decode().count('class="page"') == 3
    assert DocumentVersion.objects.get(pk=version.pk).page_count == 3


@pytest.mark.django_db
def test_upload_rejects_unreadable_and_oversized_pdfs(media):
    room = make_room()
    folder = services.create_folder(room, None, "F")
    client, _admin = verified_admin_client()
    broken = SimpleUploadedFile("broken.pdf", b"%PDF-1.7\nthis is not a real pdf body", content_type="application/pdf")
    response = client.post(reverse("document_upload", args=[room.pk]), {"folder": folder.pk, "title": "B", "file": broken})
    assert response.status_code == 200 and "cannot be read" in response.content.decode()
    with mock.patch.object(previews, "MAX_PAGES", 2):
        many = client.post(reverse("document_upload", args=[room.pk]), {"folder": folder.pk, "title": "M", "file": pdf_upload("m.pdf", make_pdf(3))})
    assert many.status_code == 200 and "too many pages" in many.content.decode()
    assert not (media / "documents").exists() or not any(p.is_file() for p in (media / "documents").rglob("*"))


# --- Скачивание с Range -----------------------------------------------------


@pytest.fixture
def download_setup():
    room = make_room()
    doc = make_doc(services.create_folder(room, None, "F"), payload=make_pdf(2))
    client, user = viewer()
    g = grant(user, room, download=True)
    data = doc.current_version
    from apps.rooms import files

    content = files.file_path(room.pk, data.stored_name).read_bytes()
    return client, user, g, doc, content


@pytest.mark.django_db
def test_download_supports_single_ranges(download_setup):
    client, _user, _g, doc, content = download_setup
    url = reverse("document_download", args=[doc.pk])
    size = len(content)

    full = client.get(url)
    assert full.status_code == 200 and full["Accept-Ranges"] == "bytes" and body(full) == content

    first = client.get(url, HTTP_RANGE="bytes=0-9")
    assert first.status_code == 206 and first["Content-Range"] == f"bytes 0-9/{size}" and body(first) == content[:10]
    assert first["Content-Length"] == "10" and "attachment" in first["Content-Disposition"]
    rest = client.get(url, HTTP_RANGE="bytes=10-")
    assert rest.status_code == 206 and body(rest) == content[10:] and rest["Content-Range"] == f"bytes 10-{size - 1}/{size}"
    tail = client.get(url, HTTP_RANGE="bytes=-5")
    assert tail.status_code == 206 and body(tail) == content[-5:]
    clipped = client.get(url, HTTP_RANGE=f"bytes=5-{size + 1000}")
    assert clipped.status_code == 206 and body(clipped) == content[5:]

    unsatisfiable = client.get(url, HTTP_RANGE=f"bytes={size + 10}-")
    assert unsatisfiable.status_code == 416 and unsatisfiable["Content-Range"] == f"bytes */{size}"
    for odd in ("bytes=0-1,5-6", "items=0-4", "bytes=abc", "bytes=-"):
        assert body(client.get(url, HTTP_RANGE=odd)) == content  # непонятное или составное: целиком


@pytest.mark.django_db
def test_range_requests_go_through_the_same_policy(download_setup):
    client, user, g, doc, _content = download_setup
    url = reverse("document_download", args=[doc.pk])
    assert client.get(url, HTTP_RANGE="bytes=0-9").status_code == 206
    g.can_download = False
    g.save()
    assert client.get(url, HTTP_RANGE="bytes=10-19").status_code == 403  # право снято: продолжить загрузку нельзя
    g.can_download, g.revoked_at = True, timezone.now()
    g.save()
    assert client.get(url, HTTP_RANGE="bytes=10-19").status_code == 404


@pytest.mark.django_db
def test_download_is_audited_once_per_transfer_not_per_chunk(download_setup):
    client, user, _g, doc, _content = download_setup
    url = reverse("document_download", args=[doc.pk])
    client.get(url, HTTP_RANGE="bytes=0-9")
    client.get(url, HTTP_RANGE="bytes=10-19")
    client.get(url, HTTP_RANGE="bytes=-5")
    assert AuditEvent.objects.filter(code="document.downloaded", actor=user).count() == 1
    client.get(url)
    assert AuditEvent.objects.filter(code="document.downloaded", actor=user).count() == 2


def test_parse_range_unit():
    assert serving.parse_range("bytes=0-9", 100) == (0, 9)
    assert serving.parse_range("bytes=90-", 100) == (90, 99)
    assert serving.parse_range("bytes=-10", 100) == (90, 99)
    assert serving.parse_range("bytes=-500", 100) == (0, 99)
    assert serving.parse_range("bytes=50-10", 100) == "invalid"
    assert serving.parse_range("bytes=100-", 100) == "invalid"
    assert serving.parse_range("bytes=-0", 100) == "invalid"
    assert serving.parse_range(None, 100) is None and serving.parse_range("bytes=1-2,4-5", 100) is None


# --- Журнал аудита ----------------------------------------------------------


@pytest.mark.django_db
def test_audit_screen_shows_translated_labels_codes_and_utc_times():
    client, admin = verified_admin_client()
    record(events.GRANT_CREATED, actor=admin, target="grant:7")
    record(events.DOCUMENT_DOWNLOAD_DENIED, actor=admin, target="document:3", outcome="denied")
    html = client.get(reverse("audit_list")).content.decode()
    assert "Access granted" in html and "grant:7" in html and "<code class=\"muted\">grant.created</code>" in html
    assert "boss@example.com" in html and "UTC" in html and "denied" in html

    client.post(reverse("set_language"), {"language": "ru", "next": "/"})
    ru = client.get(reverse("audit_list")).content.decode()
    assert "Доступ предоставлен" in ru and "Журнал аудита" in ru and "grant.created" in ru  # код не переводится
    client.post(reverse("set_language"), {"language": "de", "next": "/"})
    assert "Zugriff gewährt" in client.get(reverse("audit_list")).content.decode()


@pytest.mark.django_db
def test_audit_filters_by_event_outcome_person_and_dates():
    client, admin = verified_admin_client()
    other = User.objects.create_user("other@example.com", STRONG)
    old = record(events.ROOM_CREATED, actor=other, target="room:1")
    AuditEvent.objects.filter(pk=old.pk).update(created_at=timezone.now() - datetime.timedelta(days=10))
    record(events.DOCUMENT_DOWNLOAD_DENIED, actor=other, target="document:9", outcome="denied")
    record(events.GRANT_REVOKED, actor=admin, target="grant:2")
    url = reverse("audit_list")
    assert "document:9" not in client.get(url, {"code": "grant.revoked"}).content.decode()
    assert "grant:2" in client.get(url, {"code": "grant.revoked"}).content.decode()
    by_outcome = client.get(url, {"outcome": "denied"}).content.decode()
    assert "document:9" in by_outcome and "grant:2" not in by_outcome
    by_actor = client.get(url, {"actor": "OTHER@"}).content.decode()
    assert "document:9" in by_actor and "grant:2" not in by_actor
    since = (timezone.now() - datetime.timedelta(days=2)).date().isoformat()
    recent = client.get(url, {"date_from": since}).content.decode()
    assert "room:1" not in recent and "grant:2" in recent
    until = (timezone.now() - datetime.timedelta(days=5)).date().isoformat()
    assert "room:1" in client.get(url, {"date_to": until}).content.decode()
    assert "grant:2" not in client.get(url, {"date_to": until}).content.decode()


@pytest.mark.django_db
def test_audit_is_paginated_and_keeps_filters_in_page_links():
    client, admin = verified_admin_client()
    AuditEvent.objects.bulk_create([AuditEvent(code="folder.created", target=f"folder:{i}") for i in range(120)])
    first = client.get(reverse("audit_list"), {"code": "folder.created"}).content.decode()
    assert first.count("folder:") == 50 and "page=2" in first and "code=folder.created" in first
    third = client.get(reverse("audit_list"), {"code": "folder.created", "page": 3}).content.decode()
    assert third.count("folder:") == 20


@pytest.mark.django_db
def test_audit_screens_are_for_verified_administrators_only():
    User.objects.create_user("v@example.com", STRONG)
    viewer_client = password_login("v@example.com")
    for name in ("audit_list", "audit_export"):
        assert viewer_client.get(reverse(name)).status_code == 403
        anonymous = Client().get(reverse(name))
        assert anonymous.status_code == 302 and anonymous["Location"].startswith("/login/")
    from .helpers import make_admin

    make_admin("a2@example.com")
    unverified = password_login("a2@example.com")
    assert unverified.get(reverse("audit_list"))["Location"].startswith("/2fa/")


@pytest.mark.django_db
def test_sign_in_and_out_are_recorded_but_failures_store_no_address():
    User.objects.create_user("v@example.com", STRONG)
    client = Client()
    client.post(reverse("login"), {"username": "stranger@example.com", "password": "wrong-password-1"})
    failed = AuditEvent.objects.get(code="auth.sign_in_failed")
    assert failed.actor is None and failed.target == "" and failed.outcome == "failed"
    client.post(reverse("login"), {"username": "v@example.com", "password": STRONG})
    signed_in = AuditEvent.objects.get(code="auth.signed_in")
    assert signed_in.actor.email == "v@example.com"
    client.post(reverse("logout"))
    assert AuditEvent.objects.filter(code="auth.signed_out", actor__email="v@example.com").exists()
    stored = " ".join(f"{e.code}{e.target}" for e in AuditEvent.objects.all())
    assert "stranger" not in stored and "wrong-password" not in stored


@pytest.mark.django_db
def test_csv_export_respects_filters_neutralises_formulas_and_is_itself_audited():
    client, admin = verified_admin_client()
    record(events.ROOM_CREATED, actor=admin, target="=HYPERLINK(\"http://evil\")")
    record(events.GRANT_REVOKED, actor=admin, target="grant:2")
    response = client.get(reverse("audit_export"), {"code": "room.created"})
    assert response.status_code == 200 and response["Content-Type"].startswith("text/csv")
    assert "attachment" in response["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(response.content.decode())))
    assert rows[0] == ["time_utc", "actor", "event_code", "event", "target", "outcome", "request_id"]
    assert len(rows) == 2 and rows[1][2] == "room.created" and rows[1][4].startswith("'=")
    assert AuditEvent.objects.filter(code="audit.exported", actor=admin, target="rows:1").exists()


def test_every_event_code_has_a_label_and_unknown_codes_fall_back():
    codes = {value for name, value in vars(events).items() if name.isupper() and isinstance(value, str)}
    assert codes <= set(labels.LABELS), codes - set(labels.LABELS)
    assert labels.label("something.unknown") == "something.unknown"
    assert labels.outcome_label("ok") and labels.outcome_label("weird") == "weird"


# --- Сводка, активные права, статус заявки ----------------------------------


@pytest.mark.django_db
def test_admin_overview_counts_and_is_hidden_from_visitors():
    room = make_room()
    user = User.objects.create_user("v@example.com", STRONG)
    grant(user, room)
    grant(user, room, revoked_at=timezone.now())
    grant(user, room, expires_at=timezone.now() - datetime.timedelta(days=1))
    AccessRequest.objects.create(email="p@example.com", name="P", password_hash="x", language="en", status="pending")
    AccessRequest.objects.create(email="u@example.com", name="U", password_hash="x", language="en", status="unconfirmed")
    InstanceSettings.objects.update_or_create(pk=1, defaults={"setup_completed_at": timezone.now()})
    client, admin = verified_admin_client()
    record(events.ROOM_CREATED, actor=admin, target="room:1")
    html = client.get(reverse("home")).content.decode()
    assert "<h2>Overview</h2>" in html
    assert "<strong>1</strong> rooms" in html and "<strong>1</strong> active access rights" in html
    assert "<strong>1</strong> requests waiting" in html and "Room created" in html
    visitor, _u = viewer("w@example.com")
    assert "Overview" not in visitor.get(reverse("home")).content.decode()


@pytest.mark.django_db
def test_active_access_screen_lists_only_live_rights_and_revokes_in_place():
    room = make_room()
    folder = services.create_folder(room, None, "Finance")
    live = User.objects.create_user("live@example.com", STRONG)
    gone = User.objects.create_user("gone@example.com", STRONG)
    late = User.objects.create_user("late@example.com", STRONG)
    g = grant(live, room, folder=folder, download=True)
    grant(gone, room, revoked_at=timezone.now())
    grant(late, room, expires_at=timezone.now() - datetime.timedelta(days=1))
    client, _admin = verified_admin_client()
    html = client.get(reverse("access_overview")).content.decode()
    assert "live@example.com" in html and "gone@example.com" not in html and "late@example.com" not in html
    assert "Finance" in html and "can download" in html

    response = client.post(reverse("grant_revoke", args=[g.pk]), {"next": reverse("access_overview")})
    assert response.status_code == 302 and response["Location"] == reverse("access_overview")
    assert "live@example.com" not in client.get(reverse("access_overview")).content.decode()
    g2 = grant(live, room)
    unsafe = client.post(reverse("grant_revoke", args=[g2.pk]), {"next": "https://evil.example/"})
    assert unsafe.status_code == 302 and unsafe["Location"] == reverse("access_list", args=[room.pk])
    visitor, _u = viewer("w@example.com")
    assert visitor.get(reverse("access_overview")).status_code == 403


@pytest.mark.django_db
def test_request_status_page_follows_the_decision(django_capture_on_commit_callbacks):
    InstanceSettings.objects.update_or_create(pk=1, defaults={"self_registration_enabled": True})
    client = Client()
    assert client.get(reverse("request_status"))["Location"] == reverse("request_access")  # нет заявки в сессии
    data = {"email": "new@example.com", "name": "Nina", "language": "en", "message": "", "password1": STRONG, "password2": STRONG}
    from django.core import mail

    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("request_access"), data)
    import re

    code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
    client.post(reverse("request_confirm"), {"code": code})
    assert "will review your request" in client.get(reverse("request_status")).content.decode()

    req = AccessRequest.objects.get()
    admin_client, _admin = verified_admin_client()
    with django_capture_on_commit_callbacks(execute=True):
        admin_client.post(reverse("request_detail", args=[req.pk]), {"action": "approve"})
    html = client.get(reverse("request_status")).content.decode()
    assert "is approved" in html and reverse("login") in html

    InstanceSettings.objects.filter(pk=1).update(self_registration_enabled=False)
    assert client.get(reverse("request_status")).status_code == 404


# --- Мобильная вёрстка (проверяемая часть) ----------------------------------


@pytest.mark.django_db
def test_pages_declare_a_viewport_and_the_viewer_images_scale_down():
    room = make_room()
    doc = make_doc(services.create_folder(room, None, "F"))
    client, user = viewer()
    grant(user, room)
    html = client.get(reverse("document_view", args=[doc.pk])).content.decode()
    assert 'name="viewport" content="width=device-width,initial-scale=1"' in html
    from django.conf import settings

    css = (settings.BASE_DIR / "static/css/app.css").read_text(encoding="utf-8")
    assert ".page{width:100%;max-width:900px;height:auto" in css and ".table-wrap{overflow-x:auto" in css
