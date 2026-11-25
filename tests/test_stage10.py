"""Этап 10: вопросы и ответы."""
import io
import zipfile

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.audit import events
from apps.audit.models import AuditEvent
from apps.core import backup
from apps.qa import services
from apps.qa.models import Question
from apps.rooms import services as room_services
from apps.rooms.models import AccessGrant, Room

from .helpers import office_upload, pdf_upload, verified_admin_client, viewer


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = tmp_path / "media"
    settings.PRIVATE_MEDIA_ROOT.mkdir()
    return settings.PRIVATE_MEDIA_ROOT


def setup_world():
    client, admin = verified_admin_client()
    room = Room.objects.create(name="Alpha")
    finance = room_services.create_folder(room, None, "Finance")
    legal = room_services.create_folder(room, None, "Legal")
    doc, _v = room_services.create_document(finance, "Plan", pdf_upload(), admin)
    secret, _v = room_services.create_document(legal, "Secret", pdf_upload(), admin)
    return client, admin, room, finance, legal, doc, secret


def grant(user, room, folder=None):
    return AccessGrant.objects.create(user=user, room=room, folder=folder)


def ask(client, **scope):
    return client.post(reverse("question_ask"), {"text": "How is revenue recognised?", **scope})


# --- Вопрос ----------------------------------------------------------------


@pytest.mark.django_db
def test_visitor_asks_about_a_document_and_admin_is_notified(django_capture_on_commit_callbacks):
    _, admin, room, finance, _legal, doc, _s = setup_world()
    vclient, user = viewer()
    grant(user, room, finance)
    with django_capture_on_commit_callbacks(execute=True):
        response = ask(vclient, document=doc.pk, room=room.pk)
    question = Question.objects.get()
    assert response.status_code == 302 and response["Location"] == reverse("question_detail", args=[question.pk])
    assert (question.author, question.document, question.subject, question.status) == (user, doc, "Plan", "new")
    assert [m.to for m in mail.outbox] == [[admin.email]]
    assert "revenue" not in mail.outbox[0].body and "revenue" not in mail.outbox[0].subject  # текст в письмо не попадает
    assert AuditEvent.objects.filter(code=events.QUESTION_ASKED, target=f"question:{question.pk}").exists()
    assert "revenue" not in "".join(AuditEvent.objects.values_list("target", flat=True))


@pytest.mark.django_db
def test_cannot_ask_outside_own_access():
    _, _, room, finance, legal, doc, secret = setup_world()
    vclient, user = viewer()
    grant(user, room, finance)
    assert ask(vclient, document=secret.pk).status_code == 404
    assert ask(vclient, folder=legal.pk).status_code == 404
    assert ask(vclient, room=room.pk).status_code == 302  # комната целиком: у него есть доступ к её части
    stranger, _u = viewer("s@example.com")
    assert ask(stranger, room=room.pk).status_code == 404
    assert Question.objects.count() == 1


@pytest.mark.django_db
def test_nda_blocks_asking_until_accepted():
    _, _, room, finance, _l, doc, _s = setup_world()
    room.nda_required = True
    room.save()
    vclient, user = viewer()
    grant(user, room, finance)
    assert ask(vclient, document=doc.pk).status_code == 404


@pytest.mark.django_db
def test_admin_cannot_ask_and_empty_text_is_rejected():
    client, _, room, _f, _l, _d, _s = setup_world()
    assert client.post(reverse("question_ask"), {"text": "x", "room": room.pk}).status_code == 403
    vclient, user = viewer()
    grant(user, room)
    response = vclient.post(reverse("question_ask"), {"text": "   ", "room": room.pk})
    assert response.status_code == 200 and Question.objects.count() == 0


@pytest.mark.django_db
def test_nul_characters_are_rejected_not_a_server_error():
    from django.core.exceptions import ValidationError

    _, admin, room, *_ = setup_world()
    vclient, user = viewer()
    grant(user, room)
    assert vclient.post(reverse("question_ask"), {"text": "bad\x00text", "room": room.pk}).status_code == 200
    with pytest.raises(ValidationError):
        services.ask(user, room, "bad\x00text")
    question = make_question(user, room)
    with pytest.raises(ValidationError):
        services.answer(question, admin, "bad\x00answer")
    assert Question.objects.filter(answered_at__isnull=False).count() == 0


@pytest.mark.django_db
def test_asking_is_rate_limited():
    _, _, room, *_ = setup_world()
    vclient, user = viewer()
    grant(user, room)
    for _i in range(services.ASK_LIMIT):
        assert ask(vclient, room=room.pk).status_code == 302
    assert ask(vclient, room=room.pk).status_code == 200
    assert Question.objects.count() == services.ASK_LIMIT


# --- Кто что видит ----------------------------------------------------------


def make_question(user, room, **extra):
    return Question.objects.create(room=room, author=user, text="Q?", subject=room.name, **extra)


@pytest.mark.django_db
def test_questions_are_private_to_the_author_by_default():
    client, admin, room, *_ = setup_world()
    a_client, a = viewer("a@example.com")
    b_client, b = viewer("b@example.com")
    grant(a, room)
    grant(b, room)
    question = make_question(a, room)
    url = reverse("question_detail", args=[question.pk])
    assert a_client.get(url).status_code == 200
    assert b_client.get(url).status_code == 404
    assert client.get(url).status_code == 200
    assert b_client.get(reverse("question_list")).content.decode().count(url) == 0


@pytest.mark.django_db
def test_shared_answer_is_visible_in_scope_without_the_author():
    client, admin, room, finance, legal, doc, secret = setup_world()
    a_client, a = viewer("a@example.com")
    b_client, b = viewer("b@example.com")
    c_client, c = viewer("c@example.com")
    grant(a, room)
    grant(b, room, finance)
    grant(c, room, legal)
    question = Question.objects.create(room=room, folder=finance, document=doc, author=a, subject="Plan", text="Q?")
    services.answer(question, admin, "Answer text", share=True)
    url = reverse("question_detail", args=[question.pk])
    page = b_client.get(url)
    assert page.status_code == 200
    body = page.content.decode()
    assert "Answer text" in body and "a@example.com" not in body
    assert c_client.get(url).status_code == 404  # у него нет доступа к папке документа
    services.set_shared(question, admin, False)
    assert b_client.get(url).status_code == 404


@pytest.mark.django_db
def test_revoked_access_closes_the_question_for_its_author():
    _, admin, room, *_ = setup_world()
    vclient, user = viewer()
    g = grant(user, room)
    question = make_question(user, room)
    url = reverse("question_detail", args=[question.pk])
    assert vclient.get(url).status_code == 200
    g.revoked_at = timezone.now()
    g.save()
    assert vclient.get(url).status_code == 404


@pytest.mark.django_db
def test_moving_the_document_changes_who_sees_a_shared_answer():
    client, admin, room, finance, legal, doc, secret = setup_world()
    a_client, a = viewer("a@example.com")
    b_client, b = viewer("b@example.com")
    grant(a, room)
    grant(b, room, finance)
    question = Question.objects.create(room=room, folder=finance, document=doc, author=a, subject="Plan", text="Q?")
    services.answer(question, admin, "Done", share=True)
    url = reverse("question_detail", args=[question.pk])
    assert b_client.get(url).status_code == 200
    room_services.move_document(doc, legal)
    assert b_client.get(url).status_code == 404


# --- Ответ, вложение, ведение ----------------------------------------------


@pytest.mark.django_db
def test_admin_answers_with_attachment_and_author_is_notified(django_capture_on_commit_callbacks, media):
    client, admin, room, *_ = setup_world()
    vclient, user = viewer()
    grant(user, room)
    question = make_question(user, room)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("question_answer", args=[question.pk]), {
            "text": "See the attached file.", "file": office_upload("xlsx", "model.xlsx"), "share": ""})
    assert response.status_code == 302
    question.refresh_from_db()
    assert question.status == "answered" and question.answered_by == admin and question.has_attachment
    assert question.attachment_name == "model.xlsx" and question.attachment_ext == "xlsx"
    assert (media / "qa" / str(room.pk) / question.attachment_stored).exists()
    assert [m.to for m in mail.outbox][-1] == [user.email]
    assert "attached" not in mail.outbox[-1].body
    download = vclient.get(reverse("question_attachment", args=[question.pk]))
    assert download.status_code == 200 and b"".join(download.streaming_content)[:2] == b"PK"
    assert AuditEvent.objects.filter(code=events.QUESTION_ATTACHMENT_DOWNLOADED).count() == 1


@pytest.mark.django_db
def test_attachment_follows_visibility_and_revocation():
    client, admin, room, finance, *_ = setup_world()
    a_client, a = viewer("a@example.com")
    b_client, b = viewer("b@example.com")
    ga, gb = grant(a, room), grant(b, room)
    question = make_question(a, room)
    services.answer(question, admin, "Here", upload=pdf_upload("answer.pdf"))
    url = reverse("question_attachment", args=[question.pk])
    assert a_client.get(url).status_code == 200
    assert b_client.get(url).status_code == 404  # не опубликовано
    services.set_shared(question, admin, True)
    assert b_client.get(url).status_code == 200
    gb.revoked_at = timezone.now()
    gb.save()
    assert b_client.get(url).status_code == 404
    assert Client().get(url).status_code == 302  # без входа — на страницу входа


@pytest.mark.django_db
def test_bad_attachments_are_refused_and_leave_no_file(media):
    client, admin, room, *_ = setup_world()
    vclient, user = viewer()
    grant(user, room)
    question = make_question(user, room)
    macro = office_upload("docx", "evil.docx", extra=("word/vbaProject.bin",))
    client.post(reverse("question_answer", args=[question.pk]), {"text": "Hi", "file": macro})
    question.refresh_from_db()
    assert question.answered_at is None and not question.has_attachment
    assert not any((media / "qa").rglob("*")) if (media / "qa").exists() else True


@pytest.mark.django_db
def test_replacing_an_attachment_removes_the_old_file(django_capture_on_commit_callbacks, media):
    _, admin, room, *_ = setup_world()
    _c, user = viewer()
    question = make_question(user, room)
    services.answer(question, admin, "One", upload=pdf_upload("one.pdf"))
    first = question.attachment_stored
    with django_capture_on_commit_callbacks(execute=True):
        services.answer(question, admin, "Two", upload=pdf_upload("two.pdf"))
    assert question.attachment_stored != first
    assert not (media / "qa" / str(room.pk) / first).exists()
    assert (media / "qa" / str(room.pk) / question.attachment_stored).exists()


@pytest.mark.django_db
def test_admin_handling_assign_close_share_and_overdue():
    client, admin, room, *_ = setup_world()
    _c, user = viewer()
    question = make_question(user, room)
    other_client, other = viewer("o@example.com")
    yesterday = (timezone.localdate() - __import__("datetime").timedelta(days=1)).isoformat()
    client.post(reverse("question_assign", args=[question.pk]), {"assignee": admin.pk, "due_date": yesterday})
    question.refresh_from_db()
    assert question.status == "in_progress" and question.assignee == admin and question.is_overdue()
    page = client.get(reverse("question_list"), {"status": "overdue"}).content.decode()
    assert reverse("question_detail", args=[question.pk]) in page
    # поделиться нельзя, пока нет ответа
    client.post(reverse("question_share", args=[question.pk]), {"shared": "1"})
    question.refresh_from_db()
    assert not question.shared
    services.answer(question, admin, "Done")
    client.post(reverse("question_close", args=[question.pk]), {"closed": "1"})
    question.refresh_from_db()
    assert question.status == "closed" and question.closed_at
    client.post(reverse("question_close", args=[question.pk]), {"closed": "0"})
    question.refresh_from_db()
    assert question.status == "answered"
    codes = set(AuditEvent.objects.values_list("code", flat=True))
    assert {events.QUESTION_ASSIGNED, events.QUESTION_ANSWERED, events.QUESTION_CLOSED, events.QUESTION_REOPENED} <= codes


@pytest.mark.django_db
def test_only_administrators_manage_questions():
    _, admin, room, *_ = setup_world()
    vclient, user = viewer()
    grant(user, room)
    question = make_question(user, room)
    for name in ("question_answer", "question_assign", "question_close", "question_share"):
        assert vclient.post(reverse(name, args=[question.pk]), {"text": "x"}).status_code == 403
    assert vclient.get(reverse("question_export")).status_code == 403
    question.refresh_from_db()
    assert question.status == "new" and not question.answer_text


@pytest.mark.django_db
def test_assignee_must_be_an_administrator():
    from django.core.exceptions import ValidationError

    _, admin, room, *_ = setup_world()
    _c, user = viewer()
    question = make_question(user, room)
    with pytest.raises(ValidationError):
        services.assign(question, admin, user, None)


# --- Выгрузка и резервная копия ---------------------------------------------


@pytest.mark.django_db
def test_xlsx_export_is_valid_and_audited():
    client, admin, room, *_ = setup_world()
    _c, user = viewer()
    q = Question.objects.create(room=room, author=user, subject="=HYPERLINK(1)", text="=1+1\x0b", status="new")
    response = client.get(reverse("question_export"))
    assert response.status_code == 200 and "spreadsheetml" in response["Content-Type"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
    assert 't="inlineStr"' in sheet and "=1+1" in sheet and "\x0b" not in sheet
    assert "<f>" not in sheet  # формулы не создаются
    assert AuditEvent.objects.filter(code=events.QUESTIONS_EXPORTED).exists()
    assert q.pk


@pytest.mark.django_db(transaction=True)
def test_backup_includes_answer_attachments(tmp_path, media):
    _, admin, room, *_ = setup_world()
    _c, user = viewer()
    question = make_question(user, room)
    services.answer(question, admin, "File", upload=pdf_upload("a.pdf"))
    archive = tmp_path / "b.tar.gz"
    manifest = backup.create_backup(archive)
    rel = f"qa/{room.pk}/{question.attachment_stored}"
    assert rel in manifest["files"]
    path = media / rel
    path.unlink()
    backup.restore_backup(archive, replace=True)
    assert path.exists()
