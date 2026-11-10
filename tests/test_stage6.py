"""Этап 6: сброс пароля, заголовки чувствительных страниц, идентификатор запроса."""
import re

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.audit import events
from apps.audit.models import AuditEvent

from .helpers import STRONG, make_admin, verified_admin_client

NEW = "Another-strong-pass-77"


def link_from_outbox():
    body = mail.outbox[-1].body
    match = re.search(r"https?://\S+", body)
    return match.group(0)


def request_reset(client, email, capture):
    with capture() as callbacks:
        response = client.post(reverse("password_reset"), {"email": email})
    return response, callbacks


@pytest.mark.django_db
def test_reset_flow_changes_password_once(django_capture_on_commit_callbacks):
    User.objects.create_user("v@example.com", STRONG)
    client = Client()
    response, _ = request_reset(client, "v@example.com", lambda: django_capture_on_commit_callbacks(execute=True))
    assert response.status_code == 302 and response.url == reverse("password_reset_done")
    assert len(mail.outbox) == 1
    link = link_from_outbox()
    assert "t=" in link and link.startswith("http://testserver/password-reset/confirm/")

    # GET ничего не расходует и убирает токен из адреса
    path = link.replace("http://testserver", "")
    first = client.get(path)
    assert first.status_code == 302 and first.url == reverse("password_reset_confirm")
    assert client.get(first.url).status_code == 200
    assert client.get(path).status_code == 302  # повторное открытие тоже возможно

    bad = client.post(reverse("password_reset_confirm"), {"password1": "short", "password2": "short"})
    assert bad.status_code == 200 and User.objects.get().check_password(STRONG)

    ok = client.post(reverse("password_reset_confirm"), {"password1": NEW, "password2": NEW})
    assert ok.status_code == 302 and ok.url == reverse("login")
    user = User.objects.get()
    assert user.check_password(NEW) and not user.check_password(STRONG)
    assert AuditEvent.objects.filter(code=events.AUTH_PASSWORD_RESET_COMPLETED).exists()

    # Ссылка одноразовая
    other = Client()
    assert other.get(path).status_code == 404
    assert Client().post(reverse("login"), {"username": "v@example.com", "password": NEW}).status_code == 302


@pytest.mark.django_db
def test_same_answer_for_unknown_and_known_address(django_capture_on_commit_callbacks):
    User.objects.create_user("v@example.com", STRONG)
    known, _ = request_reset(Client(), "v@example.com", lambda: django_capture_on_commit_callbacks(execute=True))
    sent = len(mail.outbox)
    unknown, _ = request_reset(Client(), "nobody@example.com", lambda: django_capture_on_commit_callbacks(execute=True))
    assert (known.status_code, known.url) == (unknown.status_code, unknown.url)
    assert len(mail.outbox) == sent == 1


@pytest.mark.django_db
def test_suspended_user_gets_no_link(django_capture_on_commit_callbacks):
    User.objects.create_user("s@example.com", STRONG, status=User.Status.SUSPENDED)
    request_reset(Client(), "s@example.com", lambda: django_capture_on_commit_callbacks(execute=True))
    assert mail.outbox == []


@pytest.mark.django_db
def test_requests_are_rate_limited(django_capture_on_commit_callbacks):
    User.objects.create_user("v@example.com", STRONG)
    for _ in range(5):
        response, _cb = request_reset(Client(), "v@example.com", lambda: django_capture_on_commit_callbacks(execute=True))
        assert response.status_code == 302  # ответ не выдаёт лимит
    assert len(mail.outbox) == 3


@pytest.mark.django_db
def test_garbage_and_expired_tokens(settings, django_capture_on_commit_callbacks):
    User.objects.create_user("v@example.com", STRONG)
    client = Client()
    for bad in ["x", "abc.def", "MQ.zzz", "!!!.???", ""]:
        assert client.get(reverse("password_reset_confirm"), {"t": bad}).status_code == 404
    request_reset(client, "v@example.com", lambda: django_capture_on_commit_callbacks(execute=True))
    path = link_from_outbox().replace("http://testserver", "")
    settings.PASSWORD_RESET_TIMEOUT = -1
    assert Client().get(path).status_code == 404


@pytest.mark.django_db
def test_login_clears_old_link(django_capture_on_commit_callbacks):
    User.objects.create_user("v@example.com", STRONG)
    request_reset(Client(), "v@example.com", lambda: django_capture_on_commit_callbacks(execute=True))
    path = link_from_outbox().replace("http://testserver", "")
    assert Client().post(reverse("login"), {"username": "v@example.com", "password": STRONG}).status_code == 302
    assert Client().get(path).status_code == 404  # вход меняет last_login


@pytest.mark.django_db
def test_reset_does_not_bypass_admin_2fa(django_capture_on_commit_callbacks):
    client, admin = verified_admin_client()
    request_reset(Client(), admin.email, lambda: django_capture_on_commit_callbacks(execute=True))
    path = link_from_outbox().replace("http://testserver", "")
    fresh = Client()
    fresh.get(path)
    fresh.post(reverse("password_reset_confirm"), {"password1": NEW, "password2": NEW})
    signed = Client()
    assert signed.post(reverse("login"), {"username": admin.email, "password": NEW}).status_code == 302
    assert signed.get(reverse("audit_list")).status_code in (302, 403)  # без кода доступа к администрированию нет


@pytest.mark.django_db
def test_reset_emails_are_sent_in_user_language(django_capture_on_commit_callbacks):
    User.objects.create_user("v@example.com", STRONG, language="de")
    request_reset(Client(), "v@example.com", lambda: django_capture_on_commit_callbacks(execute=True))
    assert "Passwort" in mail.outbox[-1].subject or "Passwort" in mail.outbox[-1].body


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["invite_entry", "request_access", "request_confirm", "request_status", "password_reset", "password_reset_done", "password_reset_confirm"])
def test_sensitive_pages_headers(name, settings):
    settings.SELF_REGISTRATION = True
    response = Client().get(reverse(name))
    assert response["Referrer-Policy"] == "same-origin"  # не no-referrer: иначе браузер шлёт Origin: null и CSRF отклоняет формы
    assert "no-store" in response["Cache-Control"]


@pytest.mark.django_db
def test_other_pages_keep_default_referrer_policy():
    response = Client().get(reverse("login"))
    assert response["Referrer-Policy"] == "same-origin"


@pytest.mark.django_db
def test_audit_events_carry_request_id():
    client, admin = verified_admin_client()
    assert AuditEvent.objects.exists()
    ids = set(AuditEvent.objects.values_list("request_id", flat=True))
    assert "" not in ids and all(re.fullmatch(r"[0-9a-f]{32}", i) for i in ids)
    response = client.get(reverse("audit_export"))
    assert "request_id" in response.content.decode().splitlines()[0]


@pytest.mark.django_db
def test_client_cannot_choose_request_id():
    response = Client().get(reverse("login"), HTTP_X_REQUEST_ID="attacker")
    assert response["X-Request-ID"] != "attacker" and len(response["X-Request-ID"]) == 32
