"""Этап 11: исправления по разбору безопасности."""
import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse

from apps.core.util import parse_int
from apps.qa import services
from apps.qa.models import Question
from apps.rooms import services as room_services
from apps.rooms import views as room_views
from apps.rooms.models import AccessGrant, Room

from .helpers import make_pdf, pdf_upload, verified_admin_client, viewer


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = tmp_path / "media"
    settings.PRIVATE_MEDIA_ROOT.mkdir()
    return settings.PRIVATE_MEDIA_ROOT


def grant(user, room, folder=None):
    return AccessGrant.objects.create(user=user, room=room, folder=folder)


def test_parse_int_accepts_only_plain_ascii_digits():
    assert parse_int("12") == 12 and parse_int("0") == 0
    for bad in ["", "²", "١٢", "-1", "1.5", " 1", "1 ", "x", "9" * 10, None, 5]:
        assert parse_int(bad) is None


@pytest.mark.django_db
def test_unicode_digits_in_parameters_never_cause_a_server_error():
    client, admin = verified_admin_client()
    room = Room.objects.create(name="Alpha")
    folder = room_services.create_folder(room, None, "Docs")
    doc, _v = room_services.create_document(folder, "Plan", pdf_upload(), admin)
    vclient, user = viewer()
    grant(user, room)
    assert vclient.post(reverse("document_pulse", args=[doc.pk]), {"page": ["²"]}).status_code == 404
    assert vclient.post(reverse("document_pulse", args=[doc.pk]), {"page": ["9" * 5000]}).status_code == 404
    for name in ("document", "folder", "room"):
        assert vclient.get(reverse("question_ask"), {name: "²"}).status_code == 404
    assert client.get(reverse("document_view", args=[doc.pk]), {"v": "²"}).status_code == 200  # игнорируется
    assert client.get(reverse("document_download", args=[doc.pk]), {"version": "²"}).status_code == 404


# --- Удаление папки не расширяет область общего ответа ----------------------


@pytest.mark.django_db
def test_deleting_a_folder_does_not_widen_a_shared_answer():
    client, admin = verified_admin_client()
    room = Room.objects.create(name="Alpha")
    restricted = room_services.create_folder(room, None, "Restricted")
    other = room_services.create_folder(room, None, "Other")
    author_client, author = viewer("a@example.com")
    reader_client, reader = viewer("b@example.com")
    outsider_client, outsider = viewer("c@example.com")
    grant(author, room)
    grant(reader, room, restricted)
    grant(outsider, room, other)
    question = Question.objects.create(room=room, folder=restricted, author=author, subject="Restricted", text="Q?")
    services.answer(question, admin, "Answer", share=True)
    url = reverse("question_detail", args=[question.pk])
    assert reader_client.get(url).status_code == 200 and outsider_client.get(url).status_code == 404

    assert client.post(reverse("folder_delete", args=[restricted.pk])).status_code == 302
    question.refresh_from_db()
    assert question.folder_id is None and question.shared is False
    assert outsider_client.get(url).status_code == 404  # раньше стал бы виден всей комнате
    assert reader_client.get(url).status_code == 404
    assert author_client.get(url).status_code == 200  # автор по-прежнему видит свой вопрос


@pytest.mark.django_db
def test_deleting_a_nested_folder_moves_questions_to_the_parent():
    client, admin = verified_admin_client()
    room = Room.objects.create(name="Alpha")
    parent = room_services.create_folder(room, None, "Parent")
    child = room_services.create_folder(room, parent, "Child")
    _c, author = viewer()
    grant(author, room)
    question = Question.objects.create(room=room, folder=child, author=author, subject="Child", text="Q?", shared=True)
    client.post(reverse("folder_delete", args=[child.pk]))
    question.refresh_from_db()
    assert question.folder_id == parent.pk and question.shared is False


# --- Письмо о первом ответе уходит один раз --------------------------------


@pytest.mark.django_db
def test_two_answers_send_one_notification(django_capture_on_commit_callbacks):
    _, admin = verified_admin_client()
    room = Room.objects.create(name="Alpha")
    _c, author = viewer()
    grant(author, room)
    question = Question.objects.create(room=room, author=author, subject="Alpha", text="Q?")
    stale = Question.objects.get(pk=question.pk)  # второй администратор держит устаревшую копию
    with django_capture_on_commit_callbacks(execute=True):
        services.answer(question, admin, "First")
        services.answer(stale, admin, "Second")
    assert len([m for m in mail.outbox if m.to == [author.email]]) == 1
    question.refresh_from_db()
    assert question.answer_text == "Second"


# --- Предел отрисовки страниц ----------------------------------------------


@pytest.mark.django_db
def test_uncached_page_renders_are_limited_per_user(monkeypatch):
    _, admin = verified_admin_client()
    room = Room.objects.create(name="Alpha", watermark_enabled=False)
    folder = room_services.create_folder(room, None, "Docs")
    doc, _v = room_services.create_document(folder, "Big", pdf_upload("big.pdf", make_pdf(pages=3)), admin)
    vclient, user = viewer()
    grant(user, room)
    monkeypatch.setattr(room_views, "RENDER_LIMIT", 1)
    assert vclient.get(reverse("document_page", args=[doc.pk, 1])).status_code == 200
    limited = vclient.get(reverse("document_page", args=[doc.pk, 2]))
    assert limited.status_code == 429 and limited["Retry-After"] == "10"
    assert vclient.get(reverse("document_page", args=[doc.pk, 1])).status_code == 200  # из кэша не считается


# --- CSP и сессия ----------------------------------------------------------


@pytest.mark.django_db
def test_content_security_policy_is_sent_everywhere():
    for response in (Client().get(reverse("login")), Client().get(reverse("healthz"))):
        policy = response["Content-Security-Policy"]
        assert "script-src 'self'" in policy and "frame-ancestors 'none'" in policy and "object-src 'none'" in policy
        assert "script-src 'self' 'unsafe" not in policy


@pytest.mark.django_db
def test_session_cookie_has_a_lifetime(settings):
    settings.SESSION_COOKIE_AGE = 3600
    viewer_client, _user = viewer()
    cookie = viewer_client.cookies[settings.SESSION_COOKIE_NAME]
    assert cookie["max-age"] in (3600, "3600") or int(cookie["max-age"]) <= 3600


@pytest.mark.django_db
@pytest.mark.parametrize("language", ["ru", "uk", "de", "en"])
def test_saved_due_date_is_shown_in_the_format_a_date_input_accepts(language):
    import datetime

    from apps.accounts.models import User

    client, admin = verified_admin_client()
    User.objects.filter(pk=admin.pk).update(language=language)
    room = Room.objects.create(name="Alpha")
    _c, author = viewer()
    grant(author, room)
    question = Question.objects.create(room=room, author=author, subject="Alpha", text="Q?",
                                       due_date=datetime.date(2026, 11, 24))
    page = client.get(reverse("question_detail", args=[question.pk])).content.decode()
    assert 'value="2026-11-24"' in page
