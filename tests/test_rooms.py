"""Этап 2: комнаты, папки, документы, единая политика доступа, NDA, шаблоны."""
import datetime
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.core.models import InstanceSettings
from apps.rooms import files, policy, services
from apps.rooms.models import AccessGrant, Document, DocumentVersion, Folder, NdaAcceptance, Room

from .helpers import PDF, STRONG, make_admin, office_upload, password_login, pdf_upload, verified_admin_client, viewer


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = tmp_path
    return tmp_path


def make_room(name="Alpha", **extra):
    return Room.objects.create(name=name, **extra)


def make_doc(folder, title="Plan", payload=PDF, name="plan.pdf"):
    return services.create_document(folder, title, pdf_upload(name, payload), None)[0]


def grant(user, room, folder=None, download=False, **extra):
    return AccessGrant.objects.create(user=user, room=room, folder=folder, can_download=download, **extra)


def body(response):
    return b"".join(response.streaming_content)


# --- Закрыто по умолчанию ---------------------------------------------------


@pytest.mark.django_db
def test_closed_by_default_nothing_is_visible_without_a_grant():
    room = make_room("Secret Room")
    folder = services.create_folder(room, None, "Secret Folder")
    doc = make_doc(folder, "Secret Doc")
    client, _user = viewer()

    listing = client.get(reverse("home"))
    assert listing.status_code == 200
    assert "Secret Room" not in listing.content.decode()
    for url in (
        reverse("room_detail", args=[room.pk]),
        reverse("room_detail", args=[room.pk]) + f"?folder={folder.pk}",
        reverse("document_detail", args=[doc.pk]),
        reverse("document_download", args=[doc.pk]),
        reverse("nda_accept", args=[room.pk]),
    ):
        assert client.get(url).status_code == 404, url


@pytest.mark.django_db
def test_anonymous_gets_login_redirect_everywhere():
    room = make_room()
    folder = services.create_folder(room, None, "F")
    doc = make_doc(folder)
    for url in (reverse("home"), reverse("room_detail", args=[room.pk]), reverse("document_download", args=[doc.pk])):
        response = Client().get(url)
        assert response.status_code == 302 and response["Location"].startswith("/login/")


@pytest.mark.django_db
def test_room_grant_allows_viewing_but_download_needs_the_download_right():
    room = make_room()
    folder = services.create_folder(room, None, "Finance")
    doc = make_doc(folder, "Budget")
    client, user = viewer()
    g = grant(user, room)

    assert "Alpha" in client.get(reverse("home")).content.decode()
    page = client.get(reverse("room_detail", args=[room.pk]) + f"?folder={folder.pk}")
    assert page.status_code == 200 and "Budget" in page.content.decode()
    assert reverse("document_download", args=[doc.pk]) not in page.content.decode()
    assert client.get(reverse("document_detail", args=[doc.pk])).status_code == 200

    denied = client.get(reverse("document_download", args=[doc.pk]))
    assert denied.status_code == 403
    assert AuditEvent.objects.filter(code="document.download_denied", outcome="denied", actor=user).exists()

    g.can_download = True
    g.save()
    ok = client.get(reverse("document_download", args=[doc.pk]))
    assert ok.status_code == 200 and body(ok) == PDF
    assert "attachment" in ok["Content-Disposition"] and "plan.pdf" in ok["Content-Disposition"]
    assert ok["Cache-Control"] == "private, no-store" and ok["Accept-Ranges"] == "none"
    assert ok["X-Content-Type-Options"] == "nosniff" and ok["Content-Type"] == "application/pdf"
    assert AuditEvent.objects.filter(code="document.downloaded", target=f"document:{doc.pk}:v1").exists()


@pytest.mark.django_db
def test_folder_grant_covers_descendants_but_not_siblings_and_hides_other_names():
    room = make_room()
    legal = services.create_folder(room, None, "Legal")
    contracts = services.create_folder(room, legal, "Contracts")
    finance = services.create_folder(room, None, "Finance Secret")
    inside = make_doc(contracts, "Lease")
    outside = make_doc(finance, "Payroll")
    client, user = viewer()
    grant(user, room, folder=legal)

    top = client.get(reverse("room_detail", args=[room.pk])).content.decode()
    assert "Legal" in top and "Finance Secret" not in top
    nested = client.get(reverse("room_detail", args=[room.pk]) + f"?folder={contracts.pk}")
    assert nested.status_code == 200 and "Lease" in nested.content.decode()
    assert client.get(reverse("document_detail", args=[inside.pk])).status_code == 200
    assert client.get(reverse("document_detail", args=[outside.pk])).status_code == 404
    assert client.get(reverse("room_detail", args=[room.pk]) + f"?folder={finance.pk}").status_code == 404


@pytest.mark.django_db
def test_grant_on_a_subfolder_does_not_reveal_parent_names():
    room = make_room()
    legal = services.create_folder(room, None, "Legal Parent")
    contracts = services.create_folder(room, legal, "Contracts")
    make_doc(contracts, "Lease")
    client, user = viewer()
    grant(user, room, folder=contracts)
    top = client.get(reverse("room_detail", args=[room.pk])).content.decode()
    assert "Contracts" in top and "Legal Parent" not in top
    assert client.get(reverse("room_detail", args=[room.pk]) + f"?folder={legal.pk}").status_code == 404
    deep = client.get(reverse("room_detail", args=[room.pk]) + f"?folder={contracts.pk}").content.decode()
    assert "Legal Parent" not in deep


@pytest.mark.django_db
def test_grants_combine_view_room_plus_download_in_one_folder():
    room = make_room()
    a = services.create_folder(room, None, "A")
    b = services.create_folder(room, None, "B")
    da, db = make_doc(a, "DocA"), make_doc(b, "DocB")
    client, user = viewer()
    grant(user, room, download=False)
    grant(user, room, folder=a, download=True)
    assert client.get(reverse("document_download", args=[da.pk])).status_code == 200
    assert client.get(reverse("document_download", args=[db.pk])).status_code == 403
    assert client.get(reverse("document_detail", args=[db.pk])).status_code == 200


@pytest.mark.django_db
def test_grant_in_one_room_gives_nothing_in_another():
    room_a, room_b = make_room("A"), make_room("B")
    doc_b = make_doc(services.create_folder(room_b, None, "F"))
    client, user = viewer()
    grant(user, room_a)
    assert client.get(reverse("room_detail", args=[room_b.pk])).status_code == 404
    assert client.get(reverse("document_detail", args=[doc_b.pk])).status_code == 404


@pytest.mark.django_db
def test_expired_grant_gives_no_access():
    room = make_room()
    client, user = viewer()
    grant(user, room, expires_at=timezone.now() - datetime.timedelta(seconds=1))
    assert client.get(reverse("room_detail", args=[room.pk])).status_code == 404
    assert list(policy.visible_rooms(user)) == []
    grant(user, room, expires_at=timezone.now() + datetime.timedelta(days=1))
    assert client.get(reverse("room_detail", args=[room.pk])).status_code == 200


@pytest.mark.django_db
def test_revocation_takes_effect_on_the_very_next_request():
    room = make_room()
    folder = services.create_folder(room, None, "F")
    doc = make_doc(folder)
    admin_client, _admin = verified_admin_client()
    client, user = viewer()
    g = grant(user, room, download=True)
    assert client.get(reverse("document_download", args=[doc.pk])).status_code == 200

    response = admin_client.post(reverse("grant_revoke", args=[g.pk]))
    assert response.status_code == 302
    assert client.get(reverse("document_download", args=[doc.pk])).status_code == 404
    assert client.get(reverse("room_detail", args=[room.pk])).status_code == 404
    assert AuditEvent.objects.filter(code="grant.revoked", target=f"grant:{g.pk}").exists()


@pytest.mark.django_db
def test_suspended_user_and_unverified_admin_get_nothing_from_the_policy():
    room = make_room()
    suspended = User.objects.create_user("s@example.com", STRONG, status=User.Status.SUSPENDED)
    grant(suspended, room)
    assert policy.access_for(suspended, room) is None
    assert list(policy.visible_rooms(suspended)) == []

    admin = make_admin()
    assert policy.access_for(admin, room) is None  # объект без is_verified: второй фактор не подтверждён
    assert list(policy.visible_rooms(admin)) == []
    admin.is_verified = lambda: True
    assert policy.access_for(admin, room).admin is True


@pytest.mark.django_db
def test_unverified_admin_cannot_open_rooms_over_http():
    room = make_room()
    make_admin()
    client = password_login("boss@example.com")
    response = client.get(reverse("room_detail", args=[room.pk]))
    assert response.status_code == 302 and response["Location"].startswith("/2fa/")


@pytest.mark.django_db
def test_viewer_cannot_use_admin_actions():
    room = make_room()
    folder = services.create_folder(room, None, "F")
    doc = make_doc(folder)
    client, user = viewer()
    grant(user, room, download=True)
    for name, args in (("room_create", []), ("room_edit", [room.pk]), ("access_list", [room.pk]), ("nda_edit", [room.pk]),
                       ("document_upload", [room.pk])):
        assert client.get(reverse(name, args=args)).status_code == 403, name
    posts = (
        ("folder_create", [room.pk], {"name": "X"}), ("folder_rename", [folder.pk], {"name": "X"}),
        ("folder_delete", [folder.pk], {}), ("version_upload", [doc.pk], {"file": pdf_upload()}),
        ("room_apply_template", [room.pk], {"template": "fundraising"}), ("grant_revoke", [1], {}),
    )
    for name, args, data in posts:
        assert client.post(reverse(name, args=args), data).status_code == 403, name
    assert Folder.objects.count() == 1 and DocumentVersion.objects.count() == 1


@pytest.mark.django_db
def test_viewer_only_gets_the_current_version_even_when_asking_for_an_old_one():
    room = make_room()
    doc = make_doc(services.create_folder(room, None, "F"))
    services.add_version(doc, pdf_upload("v2.pdf", PDF + b"second"), None)
    client, user = viewer()
    grant(user, room, download=True)
    response = client.get(reverse("document_download", args=[doc.pk]) + "?version=1")
    assert body(response) == PDF + b"second"
    detail = client.get(reverse("document_detail", args=[doc.pk])).content.decode()
    assert "<strong>v2</strong>" in detail and "<strong>v1</strong>" not in detail


# --- Папки, шаблоны ---------------------------------------------------------


@pytest.mark.django_db
def test_create_room_with_template_uses_instance_language_and_is_audited():
    InstanceSettings.objects.create(default_locale="de", room_template="due_diligence")
    client, admin = verified_admin_client()
    response = client.post(reverse("room_create"), {"name": "Deal", "description": "d", "template": "due_diligence"})
    assert response.status_code == 302
    room = Room.objects.get(name="Deal")
    names = set(room.folders.values_list("name", flat=True))
    assert {"Steuern", "Recht", "Finanzen"} <= names
    assert AuditEvent.objects.filter(code="room.created", target=f"room:{room.pk}", actor=admin).exists()
    assert AuditEvent.objects.filter(code="room.template_applied").exists()


@pytest.mark.django_db
def test_applying_a_template_twice_creates_no_duplicates_and_keeps_grants_and_renames():
    room = make_room()
    user = User.objects.create_user("v@example.com", STRONG)
    g = grant(user, room)
    assert services.apply_template(room, "fundraising", "en") == 5
    team = room.folders.get(template_key="team")
    services.rename_folder(team, "Founders")
    assert services.apply_template(room, "fundraising", "ru") == 0  # язык сменился, дублей нет
    assert room.folders.count() == 5
    assert room.folders.get(template_key="team").name == "Founders"
    assert AccessGrant.objects.get(pk=g.pk).revoked_at is None
    # Вручную созданная папка с тем же именем не затирается и не дублируется.
    room2 = make_room("Other")
    services.create_folder(room2, None, "Team")
    assert services.apply_template(room2, "fundraising", "en") == 4
    assert room2.folders.filter(name__iexact="team").count() == 1


@pytest.mark.django_db
def test_template_view_is_idempotent_over_http():
    client, _admin = verified_admin_client()
    room = make_room()
    for _ in range(2):
        assert client.post(reverse("room_apply_template", args=[room.pk]), {"template": "due_diligence"}).status_code == 302
    assert room.folders.count() == 8
    assert client.post(reverse("room_apply_template", args=[room.pk]), {"template": "nope"}).status_code == 404


@pytest.mark.django_db
def test_folder_rules_duplicates_depth_rename_delete():
    room = make_room()
    root = services.create_folder(room, None, "Legal")
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        services.create_folder(room, None, "  legal ")
    child = services.create_folder(room, root, "Legal")  # то же имя на другом уровне допустимо
    node = child
    for i in range(2, 8):
        node = services.create_folder(room, node, f"L{i}")
    with pytest.raises(ValidationError):
        services.create_folder(room, node, "too deep")
    with pytest.raises(ValidationError):
        services.rename_folder(child, "")
    other_room = make_room("B")
    with pytest.raises(ValidationError):
        services.create_folder(other_room, root, "cross-room")

    client, _admin = verified_admin_client()
    make_doc(root, "D")
    delete = client.post(reverse("folder_delete", args=[root.pk]))
    assert delete.status_code == 302 and Folder.objects.filter(pk=root.pk).exists()
    leaf = services.create_folder(room, None, "Empty")
    client.post(reverse("folder_delete", args=[leaf.pk]))
    assert not Folder.objects.filter(pk=leaf.pk).exists()
    assert AuditEvent.objects.filter(code="folder.deleted", target=f"folder:{leaf.pk}").exists()
    client.post(reverse("folder_rename", args=[root.pk]), {"name": "Law"})
    assert Folder.objects.get(pk=root.pk).name == "Law"


# --- Загрузка и версии ------------------------------------------------------


@pytest.mark.django_db
def test_upload_pdf_docx_xlsx_stores_a_private_file_with_hash(media):
    room = make_room()
    folder = services.create_folder(room, None, "F")
    client, admin = verified_admin_client()
    for payload in (pdf_upload("a.pdf"), office_upload("docx", "b.docx"), office_upload("xlsx", "c.xlsx")):
        response = client.post(reverse("document_upload", args=[room.pk]), {"folder": folder.pk, "title": "", "file": payload})
        assert response.status_code == 302, response.content[:300]
    assert Document.objects.count() == 3
    version = DocumentVersion.objects.get(document__title="a")
    path = files.file_path(room.pk, version.stored_name)
    assert path.exists() and path.read_bytes() == PDF
    assert "a.pdf" not in path.name and version.original_name == "a.pdf"
    import hashlib

    assert version.sha256 == hashlib.sha256(PDF).hexdigest() and version.size == len(PDF)
    assert str(path).startswith(str(media))
    assert AuditEvent.objects.filter(code="document.uploaded", actor=admin).count() == 3


@pytest.mark.django_db
@pytest.mark.parametrize(
    "upload,fragment",
    [
        (SimpleUploadedFile("evil.exe", b"MZ", content_type="application/pdf"), "Only PDF"),
        (SimpleUploadedFile("fake.pdf", b"<html>not a pdf</html>", content_type="application/pdf"), "does not match"),
        (SimpleUploadedFile("fake.docx", b"plain text", content_type="application/octet-stream"), "does not match"),
        (SimpleUploadedFile("empty.pdf", b"", content_type="application/pdf"), "empty"),
        (office_upload("xlsx", "wrong.docx"), "does not match"),  # xlsx внутри файла .docx
        (office_upload("docx", "macro.docx", extra=("word/vbaProject.bin",)), "macros"),
        (office_upload("docx", "evil.docx", extra=("../../evil.txt",)), "does not match"),
    ],
)
def test_upload_rejects_wrong_or_dangerous_files(upload, fragment, media):
    room = make_room()
    folder = services.create_folder(room, None, "F")
    client, _admin = verified_admin_client()
    response = client.post(reverse("document_upload", args=[room.pk]), {"folder": folder.pk, "title": "T", "file": upload})
    assert response.status_code == 200 and fragment in response.content.decode()
    assert Document.objects.count() == 0
    assert not (media / "documents").exists() or not any(p.is_file() for p in (media / "documents").rglob("*"))


@pytest.mark.django_db
def test_upload_rejects_oversized_files_and_duplicate_titles(settings):
    room = make_room()
    folder = services.create_folder(room, None, "F")
    client, _admin = verified_admin_client()
    settings.MAX_UPLOAD_BYTES = 10
    big = client.post(reverse("document_upload", args=[room.pk]), {"folder": folder.pk, "title": "Big", "file": pdf_upload()})
    assert big.status_code == 200 and "too large" in big.content.decode()
    settings.MAX_UPLOAD_BYTES = 10_000
    assert client.post(reverse("document_upload", args=[room.pk]), {"folder": folder.pk, "title": "One", "file": pdf_upload()}).status_code == 302
    dup = client.post(reverse("document_upload", args=[room.pk]), {"folder": folder.pk, "title": "one", "file": pdf_upload("x.pdf")})
    assert dup.status_code == 200 and "already exists" in dup.content.decode()
    assert Document.objects.count() == 1


@pytest.mark.django_db
def test_upload_into_a_folder_of_another_room_is_refused():
    room_a, room_b = make_room("A"), make_room("B")
    services.create_folder(room_a, None, "FA")
    foreign = services.create_folder(room_b, None, "FB")
    client, _admin = verified_admin_client()
    response = client.post(reverse("document_upload", args=[room_a.pk]), {"folder": foreign.pk, "title": "T", "file": pdf_upload()})
    assert response.status_code == 200 and Document.objects.count() == 0


@pytest.mark.django_db
def test_versions_are_immutable_numbered_and_admin_can_fetch_old_ones(media):
    room = make_room()
    doc = make_doc(services.create_folder(room, None, "F"))
    client, admin = verified_admin_client()
    v1 = doc.current_version
    response = client.post(reverse("version_upload", args=[doc.pk]), {"file": pdf_upload("two.pdf", PDF + b"2")})
    assert response.status_code == 302
    v2 = Document.objects.get(pk=doc.pk).current_version
    assert v2.number == 2 and v2.original_name == "two.pdf"
    assert files.file_path(room.pk, v1.stored_name).read_bytes() == PDF  # первая версия не изменилась
    assert body(client.get(reverse("document_download", args=[doc.pk]) + "?version=1")) == PDF
    assert body(client.get(reverse("document_download", args=[doc.pk]))) == PDF + b"2"
    assert client.get(reverse("document_download", args=[doc.pk]) + "?version=9").status_code == 404
    assert AuditEvent.objects.filter(code="document.version_added", target=f"document:{doc.pk}:v2").exists()


@pytest.mark.django_db
def test_failed_database_write_leaves_no_file_and_no_version(media):
    room = make_room()
    folder = services.create_folder(room, None, "F")
    doc = make_doc(folder)
    before = sorted(p.name for p in (media / "documents").rglob("*") if p.is_file())
    with mock.patch.object(DocumentVersion.objects, "create", side_effect=RuntimeError("db down")):
        with pytest.raises(RuntimeError):
            services.add_version(doc, pdf_upload("new.pdf", PDF + b"x"), None)
    assert DocumentVersion.objects.filter(document=doc).count() == 1
    assert sorted(p.name for p in (media / "documents").rglob("*") if p.is_file()) == before
    with mock.patch.object(Document.objects, "create", side_effect=RuntimeError("db down")):
        with pytest.raises(RuntimeError):
            services.create_document(folder, "Another", pdf_upload("n.pdf"), None)
    assert sorted(p.name for p in (media / "documents").rglob("*") if p.is_file()) == before


@pytest.mark.django_db
def test_stored_files_are_not_reachable_by_url(media, settings):
    room = make_room()
    doc = make_doc(services.create_folder(room, None, "F"))
    stored = doc.current_version.stored_name
    for url in (f"/media/documents/{room.pk}/{stored}", f"/private_media/documents/{room.pk}/{stored}", f"/static/documents/{room.pk}/{stored}"):
        assert Client().get(url).status_code == 404
    assert not settings.MEDIA_ROOT  # публичной раздачи пользовательских файлов нет


@pytest.mark.django_db
def test_original_filename_is_sanitised_in_download_header():
    room = make_room()
    folder = services.create_folder(room, None, "F")
    doc, version = services.create_document(folder, "T", pdf_upload("..\\..\\etc\\pass\r\nwd.pdf"), None)
    assert "/" not in version.original_name and "\\" not in version.original_name and "\n" not in version.original_name
    client, user = viewer()
    grant(user, room, download=True)
    response = client.get(reverse("document_download", args=[doc.pk]))
    assert "\n" not in response["Content-Disposition"] and "\r" not in response["Content-Disposition"]


# --- Доступ: форма выдачи ---------------------------------------------------


@pytest.mark.django_db
def test_admin_grants_access_with_expiry_and_audit_has_no_personal_data():
    room = make_room()
    folder = services.create_folder(room, None, "F")
    client, admin = verified_admin_client()
    user = User.objects.create_user("investor@example.com", STRONG, name="Inv")
    future = (timezone.now() + datetime.timedelta(days=3)).date().isoformat()
    response = client.post(reverse("access_list", args=[room.pk]),
                           {"user": user.pk, "folder": folder.pk, "can_download": "on", "expires_on": future})
    assert response.status_code == 302
    g = AccessGrant.objects.get()
    assert g.folder == folder and g.can_download and g.expires_at and g.created_by == admin
    event = AuditEvent.objects.get(code="grant.created")
    assert event.target == f"grant:{g.pk}" and "investor" not in event.target
    page = client.get(reverse("access_list", args=[room.pk])).content.decode()
    assert "investor@example.com" in page

    past = (timezone.now() - datetime.timedelta(days=1)).date().isoformat()
    bad = client.post(reverse("access_list", args=[room.pk]), {"user": user.pk, "expires_on": past})
    assert bad.status_code == 200 and AccessGrant.objects.count() == 1


@pytest.mark.django_db
def test_grant_form_offers_only_active_viewers_and_rejects_foreign_folders():
    room, other = make_room(), make_room("Other")
    foreign = services.create_folder(other, None, "X")
    admin_user = make_admin("a2@example.com")
    suspended = User.objects.create_user("s@example.com", STRONG, status=User.Status.SUSPENDED)
    ok = User.objects.create_user("ok@example.com", STRONG)
    client, _admin = verified_admin_client()
    for user in (admin_user, suspended):
        assert client.post(reverse("access_list", args=[room.pk]), {"user": user.pk}).status_code == 200
    assert client.post(reverse("access_list", args=[room.pk]), {"user": ok.pk, "folder": foreign.pk}).status_code == 200
    assert AccessGrant.objects.count() == 0


# --- NDA --------------------------------------------------------------------


@pytest.mark.django_db
def test_nda_is_required_before_any_content_and_closed_while_unpublished():
    room = make_room(nda_required=True)
    doc = make_doc(services.create_folder(room, None, "F"), "Hidden")
    client, user = viewer()
    grant(user, room, download=True)
    for url in (reverse("room_detail", args=[room.pk]), reverse("document_detail", args=[doc.pk]), reverse("document_download", args=[doc.pk])):
        response = client.get(url)
        assert response.status_code == 302 and response["Location"] == reverse("nda_accept", args=[room.pk]), url
    page = client.get(reverse("nda_accept", args=[room.pk])).content.decode()
    assert "not available yet" in page
    client.post(reverse("nda_accept", args=[room.pk]), {"accept": "on"})
    assert not NdaAcceptance.objects.exists()
    assert client.get(reverse("room_detail", args=[room.pk])).status_code == 302


@pytest.mark.django_db
def test_nda_publish_accept_and_reaccept_on_new_version():
    room = make_room(nda_required=True)
    folder = services.create_folder(room, None, "F")
    doc = make_doc(folder, "Visible")
    admin_client, admin = verified_admin_client()
    client, user = viewer()
    grant(user, room, download=True)

    assert admin_client.get(reverse("room_detail", args=[room.pk])).status_code == 200  # администратор освобождён
    missing = admin_client.post(reverse("nda_edit", args=[room.pk]), {"body_ru": "только русский"})
    assert missing.status_code == 200 and room.nda_versions.count() == 0  # нужен текст на языке экземпляра (en)
    assert admin_client.post(reverse("nda_edit", args=[room.pk]), {"body_en": "Keep it secret.", "body_de": "Geheim halten."}).status_code == 302
    assert AuditEvent.objects.filter(code="nda.published", target=f"room:{room.pk}:nda:1").exists()

    page = client.get(reverse("nda_accept", args=[room.pk])).content.decode()
    assert "Keep it secret." in page
    assert client.post(reverse("nda_accept", args=[room.pk]), {}).status_code == 200  # без отметки не принимается
    assert client.post(reverse("nda_accept", args=[room.pk]), {"accept": "on"}).status_code == 302
    acceptance = NdaAcceptance.objects.get()
    assert acceptance.user == user and acceptance.version.number == 1 and acceptance.language == "en"
    assert AuditEvent.objects.filter(code="nda.accepted", actor=user).exists()
    assert client.get(reverse("document_detail", args=[doc.pk])).status_code == 200

    admin_client.post(reverse("nda_edit", args=[room.pk]), {"body_en": "Version two."})
    assert client.get(reverse("room_detail", args=[room.pk])).status_code == 302  # нужно принять заново
    client.post(reverse("nda_accept", args=[room.pk]), {"accept": "on"})
    assert NdaAcceptance.objects.count() == 2 and client.get(reverse("room_detail", args=[room.pk])).status_code == 200


@pytest.mark.django_db
def test_nda_text_language_falls_back_to_instance_language_and_records_shown_language():
    room = make_room(nda_required=True)
    services.create_folder(room, None, "F")
    admin_client, _admin = verified_admin_client()
    admin_client.post(reverse("nda_edit", args=[room.pk]), {"body_en": "English text", "body_de": "Deutscher Text"})
    client, user = viewer(language="de")
    grant(user, room)
    client.post(reverse("set_language"), {"language": "de", "next": "/"})
    assert "Deutscher Text" in client.get(reverse("nda_accept", args=[room.pk])).content.decode()
    client.post(reverse("set_language"), {"language": "uk", "next": "/"})  # украинского текста нет -> английский
    assert "English text" in client.get(reverse("nda_accept", args=[room.pk])).content.decode()
    client.post(reverse("nda_accept", args=[room.pk]), {"accept": "on"})
    assert NdaAcceptance.objects.get().language == "en"


@pytest.mark.django_db
def test_room_without_nda_requirement_skips_the_page():
    room = make_room(nda_required=False)
    services.create_folder(room, None, "F")
    client, user = viewer()
    grant(user, room)
    assert client.get(reverse("room_detail", args=[room.pk])).status_code == 200
    assert client.get(reverse("nda_accept", args=[room.pk]))["Location"] == reverse("room_detail", args=[room.pk])


# --- Переводы ---------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("code,heading", [("ru", "Папки"), ("uk", "Папки"), ("de", "Ordner"), ("en", "Folders")])
def test_room_page_is_translated(code, heading):
    room = make_room()
    client, user = viewer()
    grant(user, room)
    client.post(reverse("set_language"), {"language": code, "next": "/"})
    assert f"<h2>{heading}</h2>" in client.get(reverse("room_detail", args=[room.pk])).content.decode()
