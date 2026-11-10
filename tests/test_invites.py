"""Этап 3: приглашения по ссылке и QR, коды подтверждения, самостоятельные заявки, ограничение частоты."""
import datetime
import hashlib
import logging
import re
from unittest import mock

import pytest
from django.core import mail as django_mail
from django.db import transaction
from django.forms.models import model_to_dict
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.core import ratelimit
from apps.core.log_filters import RedactTokenFilter, redact
from apps.core.models import InstanceSettings
from apps.invites import codes, mail, services
from apps.invites.models import AccessRequest, EmailCode, Invitation
from apps.rooms import services as room_services
from apps.rooms.models import AccessGrant, Room

from .helpers import STRONG, password_login, verified_admin_client


@pytest.fixture
def commit(django_capture_on_commit_callbacks):
    """Выполняет вызов и затем коллбэки on_commit, как это произошло бы при реальной фиксации транзакции."""

    def run(fn, *args, **kwargs):
        with django_capture_on_commit_callbacks(execute=True):
            return fn(*args, **kwargs)

    return run


def make_room(name="Alpha"):
    room = Room.objects.create(name=name)
    room_services.create_folder(room, None, "Finance")
    return room


def invite(room=None, folder=None, email="guest@example.com", language="en", send_email=False, **extra):
    room = room or make_room()
    params = dict(
        email=email, name="", language=language, room=room, folder=folder, can_download=False,
        access_expires_at=None, valid_days=7, created_by=None, send_email=send_email,
    )
    params.update(extra)
    return services.create_invitation(**params)


def open_link(client, token):
    return client.get(f"{reverse('invite_entry')}?t={token}")


def last_code():
    match = re.search(r"\b(\d{6})\b", django_mail.outbox[-1].body)
    assert match, django_mail.outbox[-1].body
    return match.group(1)


def request_code(client, commit):
    commit(client.post, reverse("invite_entry"), {"action": "send_code"})
    return last_code()


def accept(client, code, password=STRONG, name="Guest", **extra):
    data = {"action": "accept", "name": name, "password1": password, "password2": password, "code": code}
    data.update(extra)
    return client.post(reverse("invite_entry"), data)


# --- Токены: только хеши, GET ничего не расходует ---------------------------


@pytest.mark.django_db
def test_token_is_stored_only_as_hash_and_nowhere_else(commit):
    inv, token = commit(invite, send_email=True)
    assert inv.token_hash == hashlib.sha256(token.encode()).hexdigest()
    haystack = " ".join(str(v) for v in model_to_dict(inv).values()) + " ".join(
        f"{e.code} {e.target}" for e in AuditEvent.objects.all()
    )
    assert token not in haystack
    assert token in django_mail.outbox[0].body  # открытый токен существует только в письме


@pytest.mark.django_db
def test_opening_the_link_never_consumes_and_hides_the_token_from_the_address_bar():
    inv, token = invite()
    client = Client()
    for _ in range(3):
        response = open_link(client, token)
        assert response.status_code == 302 and response["Location"] == reverse("invite_entry")
        page = client.get(reverse("invite_entry"))
        assert page.status_code == 200 and "g***@example.com" in page.content.decode()
        assert token not in page.content.decode()
    inv.refresh_from_db()
    assert inv.consumed_at is None and not User.objects.filter(email=inv.email).exists()
    assert not AccessGrant.objects.exists()


@pytest.mark.django_db
def test_unknown_expired_revoked_and_used_links_look_identical():
    pending, ok_token = invite(email="a@example.com")
    expired, t_exp = invite(email="b@example.com")
    Invitation.objects.filter(pk=expired.pk).update(expires_at=timezone.now() - datetime.timedelta(seconds=1))
    revoked, t_rev = invite(email="c@example.com")
    services.revoke_invitation(revoked.pk, None)
    used, t_used = invite(email="d@example.com")
    Invitation.objects.filter(pk=used.pk).update(consumed_at=timezone.now())

    bodies = set()
    for token in ("no-such-token", t_exp, t_rev, t_used):
        response = open_link(Client(), token)
        assert response.status_code == 404
        bodies.add(re.sub(r'value="[^"]+"', "", response.content.decode()))
    assert len(bodies) == 1
    assert open_link(Client(), ok_token).status_code == 302


@pytest.mark.django_db
def test_guessing_tokens_is_rate_limited():
    for _ in range(20):
        assert open_link(Client(), "guess").status_code == 404
    assert open_link(Client(), "guess").status_code == 429
    good, token = invite()
    assert open_link(Client(), token).status_code == 429  # лимит по адресу клиента, а не по токену


@pytest.mark.django_db
def test_invite_page_without_a_session_is_unavailable_and_post_creates_nothing():
    response = Client().post(reverse("invite_entry"), {"action": "accept", "name": "X", "password1": STRONG, "password2": STRONG, "code": "123456"})
    assert response.status_code == 404 and not User.objects.exists()


# --- Принятие приглашения ---------------------------------------------------


@pytest.mark.django_db
def test_full_flow_confirms_mailbox_creates_account_and_grants_access_once(commit):
    room = make_room()
    folder = room.folders.get()
    expires = timezone.now() + datetime.timedelta(days=5)
    inv, token = invite(room=room, folder=folder, can_download=True, access_expires_at=expires, language="de")
    client = Client()
    open_link(client, token)
    code = request_code(client, commit)
    assert django_mail.outbox[-1].to == ["guest@example.com"] and "Bestätigungscode" in django_mail.outbox[-1].body

    response = accept(client, code, name="Gina Guest")
    assert response.status_code == 302 and response["Location"] == reverse("home")
    user = User.objects.get(email="guest@example.com")
    assert user.role == User.Role.VIEWER and user.name == "Gina Guest" and user.language == "de"
    assert user.email_confirmed_at and user.check_password(STRONG)
    grant = AccessGrant.objects.get()
    assert (grant.user, grant.room, grant.folder, grant.can_download, grant.expires_at) == (user, room, folder, True, expires)
    inv.refresh_from_db()
    assert inv.consumed_at and inv.consumed_by == user
    assert client.get(reverse("home")).status_code == 200  # уже вошёл
    assert AuditEvent.objects.filter(code="invitation.accepted", actor=user, target=f"invitation:{inv.pk}").exists()

    # Повтор той же ссылки и повторное принятие ничего не выдают.
    assert open_link(Client(), token).status_code == 404
    with pytest.raises(services.Unavailable):
        services.accept_new_user(inv.pk, name="Again", password=STRONG, language="de")
    assert AccessGrant.objects.count() == 1 and User.objects.count() == 1


@pytest.mark.django_db
def test_accept_needs_a_valid_code_and_wrong_codes_burn_the_code(commit, settings):
    inv, token = invite()
    client = Client()
    open_link(client, token)
    # Без отправленного кода принять нельзя.
    assert accept(client, "123456").status_code == 200 and not User.objects.exists()
    code = request_code(client, commit)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(settings.EMAIL_CODE_MAX_ATTEMPTS):
        response = accept(client, wrong)
        assert response.status_code == 200 and "not valid or has expired" in response.content.decode()
    assert accept(client, code).status_code == 200  # лимит попыток исчерпан, даже верный код не принимается
    assert not User.objects.exists()
    inv.refresh_from_db()
    assert inv.consumed_at is None
    assert AuditEvent.objects.filter(code="invitation.code_failed", outcome="failed").exists()


@pytest.mark.django_db
def test_expired_code_and_replaced_code_do_not_work(commit):
    inv, token = invite()
    client = Client()
    open_link(client, token)
    old = request_code(client, commit)
    EmailCode.objects.update(created_at=timezone.now() - datetime.timedelta(minutes=2))
    ratelimit_keys = ratelimit.make_key("code_inv_min", inv.pk)
    from apps.core.models import RateLimitHit

    RateLimitHit.objects.filter(key=ratelimit_keys).update(created_at=timezone.now() - datetime.timedelta(minutes=2))
    new = request_code(client, commit)
    if new != old:
        assert accept(client, old).status_code == 200  # прежний код аннулирован
    EmailCode.objects.filter(consumed_at__isnull=True).update(expires_at=timezone.now() - datetime.timedelta(seconds=1))
    assert accept(client, new).status_code == 200 and not User.objects.exists()


@pytest.mark.django_db
def test_weak_or_mismatched_password_is_rejected_without_burning_the_invitation(commit):
    inv, token = invite()
    client = Client()
    open_link(client, token)
    code = request_code(client, commit)
    assert accept(client, code, password="short").status_code == 200
    mismatch = client.post(reverse("invite_entry"), {
        "action": "accept", "name": "G", "password1": STRONG, "password2": "Different-pass-123456", "code": code})
    assert mismatch.status_code == 200 and not User.objects.exists()
    assert accept(client, code).status_code == 302  # тот же код ещё действует: он не расходовался на ошибки формы
    assert User.objects.count() == 1


@pytest.mark.django_db
def test_second_account_with_same_email_created_meanwhile_gets_no_grant(commit):
    inv, token = invite()
    User.objects.create_user("guest@example.com", STRONG)
    with pytest.raises(services.Unavailable):
        services.accept_new_user(inv.pk, name="X", password=STRONG, language="en")
    assert not AccessGrant.objects.exists()
    inv.refresh_from_db()
    assert inv.consumed_at is None


@pytest.mark.django_db
def test_revoked_or_expired_invitation_cannot_be_accepted():
    inv, _t = invite()
    services.revoke_invitation(inv.pk, None)
    with pytest.raises(services.Unavailable):
        services.accept_new_user(inv.pk, name="X", password=STRONG, language="en")
    inv2, _t2 = invite(email="x@example.com")
    Invitation.objects.filter(pk=inv2.pk).update(expires_at=timezone.now() - datetime.timedelta(seconds=1))
    with pytest.raises(services.Unavailable):
        services.accept_new_user(inv2.pk, name="X", password=STRONG, language="en")
    assert not User.objects.exists() and not AccessGrant.objects.exists()


@pytest.mark.django_db
def test_existing_account_signs_in_then_accepts_exactly_once():
    existing = User.objects.create_user("guest@example.com", STRONG)
    room = make_room()
    inv, token = invite(room=room, can_download=True)
    anonymous = Client()
    open_link(anonymous, token)
    page = anonymous.get(reverse("invite_entry")).content.decode()
    assert "Sign in to accept" in page and "name=\"password1\"" not in page
    assert anonymous.post(reverse("invite_entry"), {}).status_code == 200 and not AccessGrant.objects.exists()

    client = password_login("guest@example.com")
    open_link(client, token)
    assert "Add this access to my account" in client.get(reverse("invite_entry")).content.decode()
    assert client.post(reverse("invite_entry"), {}).status_code == 302
    grant = AccessGrant.objects.get()
    assert grant.user == existing and grant.room == room and grant.can_download
    assert client.post(reverse("invite_entry"), {}).status_code == 404  # повтор не выдаёт права снова
    assert AccessGrant.objects.count() == 1


@pytest.mark.django_db
def test_existing_account_is_not_accepted_from_another_signed_in_user_or_for_admins():
    User.objects.create_user("guest@example.com", STRONG)
    other = User.objects.create_user("other@example.com", STRONG)
    inv, token = invite()
    client = password_login("other@example.com")
    open_link(client, token)
    page = client.get(reverse("invite_entry")).content.decode()
    assert "signed in with another account" in page
    assert client.post(reverse("invite_entry"), {}).status_code == 200
    assert not AccessGrant.objects.filter(user=other).exists()

    User.objects.create_user("boss@example.com", STRONG, role=User.Role.ADMIN)
    inv2, token2 = invite(email="boss@example.com")
    c2 = Client()
    open_link(c2, token2)
    assert c2.get(reverse("invite_entry")).status_code == 404


@pytest.mark.django_db
def test_resend_rotates_the_token_and_old_link_stops_working(commit):
    inv, old = invite(send_email=False)
    new_inv, new = commit(services.rotate_token, inv.pk, None)
    assert new != old and open_link(Client(), old).status_code == 404
    assert open_link(Client(), new).status_code == 302
    assert new in django_mail.outbox[-1].body
    services.revoke_invitation(inv.pk, None)
    with pytest.raises(services.Unavailable):
        services.rotate_token(inv.pk, None)


# --- Письма: после коммита, на языке получателя, без утечек в журналы -------


@pytest.mark.django_db
def test_emails_are_sent_only_after_commit_and_never_on_rollback(django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        invite(send_email=True)
    assert len(callbacks) == 1 and django_mail.outbox == []  # до фиксации ничего не ушло
    callbacks[0]()
    assert len(django_mail.outbox) == 1

    django_mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True) as rolled_back:
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                invite(send_email=True, email="r@example.com")
                raise RuntimeError("rollback")
    assert rolled_back == [] and django_mail.outbox == []
    assert not Invitation.objects.filter(email="r@example.com").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("language,subject_part,body_part", [
    ("ru", "Приглашение в", "Вас пригласили"),
    ("uk", "Запрошення до", "Вас запросили"),
    ("de", "Einladung zu", "Sie wurden"),
    ("en", "Invitation to", "You have been invited"),
])
def test_invitation_email_is_in_the_recipient_language(commit, language, subject_part, body_part):
    InstanceSettings.objects.create(site_name="Acme Fund", default_locale="en")
    commit(invite, send_email=True, language=language)
    message = django_mail.outbox[0]
    assert subject_part in message.subject and "Acme Fund" in message.subject
    assert body_part in message.body and "/invite/?t=" in message.body


@pytest.mark.django_db
def test_failed_delivery_is_recorded_and_logged_without_secrets(commit, caplog):
    caplog.set_level(logging.DEBUG)
    with mock.patch("apps.invites.mail.send_mail", side_effect=OSError("smtp down")):
        inv, token = commit(invite, send_email=True, email="secret.person@example.com")
    inv.refresh_from_db()
    assert inv.email_failed_at and not inv.email_sent_at
    assert "kind=invitation" in caplog.text and "OSError" in caplog.text
    for secret in (token, "secret.person@example.com", "invited to"):
        assert secret not in caplog.text


@pytest.mark.django_db
def test_no_token_or_code_reaches_the_logs_during_the_whole_flow(commit, caplog):
    caplog.set_level(logging.DEBUG)
    inv, token = commit(invite, send_email=True)
    client = Client()
    open_link(client, token)
    code = request_code(client, commit)
    accept(client, code, password=STRONG)
    assert token not in caplog.text and code not in caplog.text and STRONG not in caplog.text


def test_log_filter_redacts_invitation_tokens():
    assert redact('"GET /invite/?t=abc_DEF-123 HTTP/1.1" 302') == '"GET /invite/?t=[redacted] HTTP/1.1" 302'
    record = logging.LogRecord("django.server", logging.INFO, "x", 1, '"GET %s HTTP/1.1" %s', ("/invite/?t=SECRET&a=1", 302), None)
    RedactTokenFilter().filter(record)
    assert "SECRET" not in record.getMessage() and "a=1" in record.getMessage()
    configured = logging.getLogger("django.server").handlers
    assert any(isinstance(f, RedactTokenFilter) for h in configured for f in h.filters)


# --- Ограничения частоты ----------------------------------------------------


@pytest.mark.django_db
def test_codes_cannot_be_requested_twice_in_a_minute(commit):
    inv, token = invite()
    client = Client()
    open_link(client, token)
    commit(client.post, reverse("invite_entry"), {"action": "send_code"})
    commit(client.post, reverse("invite_entry"), {"action": "send_code"})
    assert len(django_mail.outbox) == 1


@pytest.mark.django_db
def test_code_checks_are_limited_per_client_address(commit):
    inv, token = invite()
    client = Client()
    open_link(client, token)
    for _ in range(30):
        ratelimit.hit(ratelimit.make_key("code_check_ip", "127.0.0.1"))
    assert accept(client, "123456").status_code == 429


@pytest.mark.django_db
def test_login_is_blocked_after_repeated_failures_per_email_and_per_ip():
    User.objects.create_user("v@example.com", STRONG)
    client = Client()
    for _ in range(5):
        assert client.post(reverse("login"), {"username": "v@example.com", "password": "wrong-password"}).status_code == 200
    blocked = client.post(reverse("login"), {"username": "v@example.com", "password": STRONG})
    assert blocked.status_code == 429 and "Too many attempts" in blocked.content.decode()
    # Другой адрес с того же IP ещё может войти, пока общий лимит не исчерпан.
    User.objects.create_user("w@example.com", STRONG)
    assert client.post(reverse("login"), {"username": "w@example.com", "password": STRONG}).status_code == 302
    # Общий лимит по IP.
    c2 = Client()
    for i in range(20):
        c2.post(reverse("login"), {"username": f"u{i}@example.com", "password": "wrong-password"})
    assert c2.post(reverse("login"), {"username": "w@example.com", "password": STRONG}).status_code == 429


@pytest.mark.django_db
def test_successful_logins_do_not_count_against_the_limit():
    User.objects.create_user("v@example.com", STRONG)
    for _ in range(8):
        assert Client().post(reverse("login"), {"username": "v@example.com", "password": STRONG}).status_code == 302


def test_client_ip_trusts_forwarded_header_only_when_enabled(settings):
    request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="9.9.9.9, 10.0.0.7", REMOTE_ADDR="127.0.0.1")
    settings.TRUST_PROXY_HEADERS = False
    assert ratelimit.client_ip(request) == "127.0.0.1"
    settings.TRUST_PROXY_HEADERS = True
    assert ratelimit.client_ip(request) == "10.0.0.7"  # последний элемент добавлен нашим proxy


@pytest.mark.django_db
def test_rate_limit_keys_do_not_contain_addresses():
    ratelimit.hit(ratelimit.make_key("login_email", "person@example.com"))
    ratelimit.hit(ratelimit.make_key("login_ip", "203.0.113.9"))
    from apps.core.models import RateLimitHit

    stored = " ".join(RateLimitHit.objects.values_list("key", flat=True))
    assert "person" not in stored and "example.com" not in stored and "203.0.113.9" not in stored


# --- Администратор: приглашения --------------------------------------------


@pytest.mark.django_db
def test_admin_creates_invitation_sees_link_and_qr_once(commit):
    room = make_room()
    client, admin = verified_admin_client()
    response = commit(client.post, reverse("invitation_create"), {
        "email": "Investor@Example.com", "name": "", "language": "uk", "room": room.pk, "folder": "",
        "can_download": "on", "expires_on": "", "valid_days": 10, "send_email": "on",
    })
    assert response.status_code == 200
    html = response.content.decode()
    token = re.search(r"t=([\w-]+)", html).group(1)
    assert "<svg" in html and "no-store" in response["Cache-Control"]
    inv = Invitation.objects.get()
    assert inv.email == "investor@example.com" and inv.language == "uk" and inv.created_by == admin and inv.can_download
    assert inv.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert (inv.expires_at - timezone.now()).days in (9, 10)
    assert django_mail.outbox[0].to == ["investor@example.com"]
    listing = client.get(reverse("invitation_list")).content.decode()
    assert "investor@example.com" in listing and token not in listing  # токен нигде не показывается повторно
    assert AuditEvent.objects.filter(code="invitation.created", actor=admin).exists()


@pytest.mark.django_db
def test_admin_can_skip_email_revoke_and_resend(commit):
    room = make_room()
    client, _admin = verified_admin_client()
    commit(client.post, reverse("invitation_create"), {
        "email": "a@example.com", "language": "en", "room": room.pk, "valid_days": 7})
    assert django_mail.outbox == []
    inv = Invitation.objects.get()
    resend = commit(client.post, reverse("invitation_resend", args=[inv.pk]))
    assert resend.status_code == 200 and len(django_mail.outbox) == 1
    assert client.post(reverse("invitation_revoke", args=[inv.pk])).status_code == 302
    inv.refresh_from_db()
    assert inv.status() == Invitation.REVOKED
    again = client.post(reverse("invitation_resend", args=[inv.pk]))
    assert again.status_code == 302 and len(django_mail.outbox) == 1
    assert AuditEvent.objects.filter(code="invitation.revoked").exists()


@pytest.mark.django_db
def test_invitation_form_validation():
    room, other = make_room("A"), make_room("B")
    foreign = other.folders.get()
    client, _admin = verified_admin_client()
    base = {"email": "a@example.com", "language": "en", "room": room.pk, "valid_days": 7}
    assert client.post(reverse("invitation_create"), {**base, "folder": foreign.pk}).status_code == 200
    past = (timezone.now() - datetime.timedelta(days=1)).date().isoformat()
    assert client.post(reverse("invitation_create"), {**base, "expires_on": past}).status_code == 200
    assert client.post(reverse("invitation_create"), {**base, "valid_days": 400}).status_code == 200
    assert client.post(reverse("invitation_create"), {**base, "language": "fr"}).status_code == 200
    assert client.post(reverse("invitation_create"), {**base, "email": "not-an-email"}).status_code == 200
    assert not Invitation.objects.exists()


@pytest.mark.django_db
def test_invitation_emails_per_admin_are_limited(commit):
    room = make_room()
    client, admin = verified_admin_client()
    for _ in range(60):
        ratelimit.hit(ratelimit.make_key("invite_mail_admin", admin.pk))
    response = commit(client.post, reverse("invitation_create"), {
        "email": "a@example.com", "language": "en", "room": room.pk, "valid_days": 7, "send_email": "on"})
    assert response.status_code == 200 and not Invitation.objects.exists() and django_mail.outbox == []


@pytest.mark.django_db
def test_viewers_and_anonymous_cannot_use_invitation_or_request_admin_pages():
    User.objects.create_user("v@example.com", STRONG)
    viewer = password_login("v@example.com")
    for name in ("invitation_list", "invitation_create", "request_list"):
        assert viewer.get(reverse(name)).status_code == 403, name
        anonymous = Client().get(reverse(name))
        assert anonymous.status_code == 302 and anonymous["Location"].startswith("/login/")
    for name, args in (("invitation_revoke", [1]), ("invitation_resend", [1]), ("request_retry_email", [1])):
        assert viewer.post(reverse(name, args=args)).status_code == 403


# --- Самостоятельная заявка -------------------------------------------------


def enable_self_registration():
    InstanceSettings.objects.update_or_create(pk=1, defaults={"self_registration_enabled": True})


def submit_request(client, commit, email="new@example.com", language="de", **extra):
    data = {"email": email, "name": "Nina New", "language": language, "message": "Hello", "password1": STRONG, "password2": STRONG}
    data.update(extra)
    return commit(client.post, reverse("request_access"), data)


@pytest.mark.django_db
def test_self_registration_is_off_by_default_and_link_is_hidden():
    assert Client().get(reverse("request_access")).status_code == 404
    assert Client().get(reverse("request_confirm")).status_code == 404
    assert "request-access" not in Client().get(reverse("login")).content.decode()
    enable_self_registration()
    assert Client().get(reverse("request_access")).status_code == 200
    assert "request-access" in Client().get(reverse("login")).content.decode()


@pytest.mark.django_db
def test_request_is_confirmed_by_code_then_waits_for_approval(commit):
    enable_self_registration()
    client = Client()
    response = submit_request(client, commit)
    assert response.status_code == 302 and response["Location"] == reverse("request_confirm")
    req = AccessRequest.objects.get()
    assert req.status == AccessRequest.Status.UNCONFIRMED and req.password_hash != STRONG
    assert not User.objects.exists()
    assert django_mail.outbox[-1].to == ["new@example.com"] and "Bestätigungscode" in django_mail.outbox[-1].body

    admin_client, _admin = verified_admin_client()
    assert "Nina New" not in admin_client.get(reverse("request_list")).content.decode()  # адрес не подтверждён: в очереди нет
    assert admin_client.get(reverse("request_detail", args=[req.pk]))["Location"] == reverse("request_list")

    code = last_code()
    wrong = "000000" if code != "000000" else "111111"
    assert "not valid" in client.post(reverse("request_confirm"), {"code": wrong}).content.decode()
    done = client.post(reverse("request_confirm"), {"code": code})
    assert done.status_code == 302 and done["Location"] == reverse("request_status")
    status = client.get(reverse("request_status")).content.decode()
    assert "An administrator will review your request" in status
    req.refresh_from_db()
    assert req.status == AccessRequest.Status.PENDING and req.email_confirmed_at
    assert "Nina New" in admin_client.get(reverse("request_list")).content.decode()


@pytest.mark.django_db
def test_approval_creates_the_account_with_the_submitted_password_and_grants_access(commit):
    enable_self_registration()
    room = make_room()
    client = Client()
    submit_request(client, commit)
    client.post(reverse("request_confirm"), {"code": last_code()})
    req = AccessRequest.objects.get()
    django_mail.outbox.clear()

    admin_client, admin = verified_admin_client()
    response = commit(admin_client.post, reverse("request_detail", args=[req.pk]),
                      {"action": "approve", "room": room.pk, "folder": "", "can_download": "on"})
    assert response.status_code == 302
    user = User.objects.get(email="new@example.com")
    assert user.role == User.Role.VIEWER and user.name == "Nina New" and user.language == "de"
    assert user.check_password(STRONG) and Client().post(reverse("login"), {"username": "new@example.com", "password": STRONG}).status_code == 302
    assert AccessGrant.objects.get().user == user
    req.refresh_from_db()
    assert req.status == AccessRequest.Status.APPROVED and req.decided_by == admin and req.user == user and req.notified_at
    assert len(django_mail.outbox) == 1 and "genehmigt" in django_mail.outbox[0].body
    assert AuditEvent.objects.filter(code="request.approved", actor=admin).exists()


@pytest.mark.django_db
def test_each_request_is_decided_exactly_once(commit):
    enable_self_registration()
    client = Client()
    submit_request(client, commit)
    client.post(reverse("request_confirm"), {"code": last_code()})
    req = AccessRequest.objects.get()
    admin_client, admin = verified_admin_client()

    commit(admin_client.post, reverse("request_detail", args=[req.pk]), {"action": "approve"})
    for action in ("approve", "reject"):
        # Решённая заявка только показывается: повторное решение ничего не меняет.
        response = admin_client.post(reverse("request_detail", args=[req.pk]), {"action": action})
        assert response.status_code == 200
    assert User.objects.filter(email="new@example.com").count() == 1
    req.refresh_from_db()
    assert req.status == AccessRequest.Status.APPROVED
    with pytest.raises(services.Unavailable):
        services.reject_request(req.pk, admin)
    with pytest.raises(services.Unavailable):
        services.approve_request(req.pk, admin)


@pytest.mark.django_db
def test_rejection_creates_no_account_and_sends_a_notice(commit):
    enable_self_registration()
    client = Client()
    submit_request(client, commit, language="ru")
    client.post(reverse("request_confirm"), {"code": last_code()})
    req = AccessRequest.objects.get()
    django_mail.outbox.clear()
    admin_client, _admin = verified_admin_client()
    commit(admin_client.post, reverse("request_detail", args=[req.pk]), {"action": "reject"})
    assert not User.objects.filter(email="new@example.com").exists()
    assert len(django_mail.outbox) == 1 and "не одобрена" in django_mail.outbox[0].body
    assert AccessRequest.objects.get().status == AccessRequest.Status.REJECTED
    # Отклонённый адрес может подать новую заявку.
    assert submit_request(Client(), commit).status_code == 302
    assert AccessRequest.objects.count() == 2


@pytest.mark.django_db
def test_request_for_existing_address_looks_the_same_and_creates_nothing(commit):
    enable_self_registration()
    User.objects.create_user("new@example.com", STRONG)
    response = submit_request(Client(), commit)
    assert response.status_code == 302 and response["Location"] == reverse("request_confirm")
    assert not AccessRequest.objects.exists()
    assert len(django_mail.outbox) == 1 and "Konto" in django_mail.outbox[0].body  # письмо владельцу адреса, язык заявки — de
    client = Client()
    submit_request(client, commit)
    assert "not valid" in client.post(reverse("request_confirm"), {"code": "123456"}).content.decode()


@pytest.mark.django_db
def test_a_rival_request_for_the_same_address_cannot_replace_the_owners_password(commit):
    """Злоумышленник подаёт заявку на чужой адрес первым; настоящий владелец подтверждает свою и получает свой пароль."""
    enable_self_registration()
    attacker, owner = Client(), Client()
    submit_request(attacker, commit, name="Mallory", password1="Attacker-pass-123456", password2="Attacker-pass-123456")
    attacker_code = last_code()
    submit_request(owner, commit, name="Nina New")
    owner_code = last_code()
    assert AccessRequest.objects.count() == 2

    owner.post(reverse("request_confirm"), {"code": owner_code})
    req = AccessRequest.objects.get()  # конкурирующая неподтверждённая заявка удалена
    assert req.status == AccessRequest.Status.PENDING and req.name == "Nina New"
    from django.contrib.auth.hashers import check_password

    assert check_password(STRONG, req.password_hash) and not check_password("Attacker-pass-123456", req.password_hash)
    # Код, отправленный владельцу, в сессии злоумышленника ничего не подтверждает.
    assert "not valid" in attacker.post(reverse("request_confirm"), {"code": attacker_code}).content.decode()
    assert AccessRequest.objects.get().status == AccessRequest.Status.PENDING


@pytest.mark.django_db
def test_pending_request_is_untouched_by_new_submissions_and_stale_drafts_are_dropped(commit):
    enable_self_registration()
    client = Client()
    submit_request(client, commit)
    client.post(reverse("request_confirm"), {"code": last_code()})
    sent = len(django_mail.outbox)
    submit_request(Client(), commit, name="Other", password1="Another-pass-123456", password2="Another-pass-123456")
    assert len(django_mail.outbox) == sent and AccessRequest.objects.count() == 1
    assert AccessRequest.objects.get().name == "Nina New"

    AccessRequest.objects.all().delete()
    submit_request(Client(), commit, email="old@example.com")
    AccessRequest.objects.update(created_at=timezone.now() - datetime.timedelta(days=2))
    submit_request(Client(), commit, email="fresh@example.com")
    assert list(AccessRequest.objects.values_list("email", flat=True)) == ["fresh@example.com"]


@pytest.mark.django_db
def test_request_form_validation_and_limits(commit):
    enable_self_registration()
    client = Client()
    bad = submit_request(client, commit, password1="short", password2="short")
    assert bad.status_code == 200 and not AccessRequest.objects.exists()
    assert submit_request(client, commit, language="fr").status_code == 200
    for _ in range(3):
        ratelimit.hit(ratelimit.make_key("request_email", "new@example.com"))
    limited = submit_request(Client(), commit)
    assert limited.status_code == 429 and not AccessRequest.objects.exists()


@pytest.mark.django_db
def test_failed_decision_email_can_be_retried_without_granting_anything_again(commit):
    enable_self_registration()
    room = make_room()
    client = Client()
    submit_request(client, commit)
    client.post(reverse("request_confirm"), {"code": last_code()})
    req = AccessRequest.objects.get()
    admin_client, admin = verified_admin_client()
    django_mail.outbox.clear()
    with mock.patch("apps.invites.mail.send_mail", side_effect=OSError("down")):
        commit(admin_client.post, reverse("request_detail", args=[req.pk]), {"action": "approve", "room": room.pk})
    req.refresh_from_db()
    assert req.notify_failed_at and not req.notified_at and django_mail.outbox == []
    assert "Send the email again" in admin_client.get(reverse("request_list")).content.decode()

    commit(admin_client.post, reverse("request_retry_email", args=[req.pk]))
    req.refresh_from_db()
    assert req.notified_at and len(django_mail.outbox) == 1
    assert User.objects.filter(email="new@example.com").count() == 1 and AccessGrant.objects.count() == 1
    commit(admin_client.post, reverse("request_retry_email", args=[req.pk]))  # уже доставлено: повтор ничего не шлёт
    assert len(django_mail.outbox) == 1


@pytest.mark.django_db
def test_approve_form_rejects_a_folder_without_its_room_and_a_foreign_folder(commit):
    enable_self_registration()
    room, other = make_room("A"), make_room("B")
    client = Client()
    submit_request(client, commit)
    client.post(reverse("request_confirm"), {"code": last_code()})
    req = AccessRequest.objects.get()
    admin_client, _admin = verified_admin_client()
    foreign = other.folders.get()
    assert admin_client.post(reverse("request_detail", args=[req.pk]), {"action": "approve", "room": room.pk, "folder": foreign.pk}).status_code == 200
    assert admin_client.post(reverse("request_detail", args=[req.pk]), {"action": "approve", "folder": foreign.pk}).status_code == 200
    assert not User.objects.filter(email="new@example.com").exists()


# --- Переводы ---------------------------------------------------------------

INVITE_HEADINGS = {"ru": "Вас пригласили", "uk": "Вас запросили", "de": "Sie sind eingeladen", "en": "You are invited"}


@pytest.mark.django_db
@pytest.mark.parametrize("code", ["ru", "uk", "de", "en"])
def test_invitation_page_is_translated_and_follows_the_invitation_language(code):
    inv, token = invite(language=code)
    client = Client()
    open_link(client, token)  # без cookie язык берётся из приглашения
    assert INVITE_HEADINGS[code] in client.get(reverse("invite_entry")).content.decode()
