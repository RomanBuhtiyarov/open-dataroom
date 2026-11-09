"""Письма. Отправка выполняется только после фиксации транзакции (on_commit): откат ничего не рассылает.

В журналы пишется лишь вид письма и класс ошибки: ни адреса, ни текста, ни токенов, ни кодов.
"""
import logging
from urllib.parse import urlencode

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy as _

from apps.core.models import InstanceSettings

logger = logging.getLogger(__name__)


def _site_name():
    return InstanceSettings.current().effective_site_name


def _absolute(path, **query):
    url = settings.PUBLIC_BASE_URL + path
    return f"{url}?{urlencode(query)}" if query else url


def deliver(kind, to, subject, body):
    """Отправляет письмо. Возвращает True при успехе. Ошибка не раскрывает содержимое."""
    try:
        send_mail(subject, body, None, [to], fail_silently=False)
    except Exception as exc:  # noqa: BLE001
        logger.error("email delivery failed kind=%s error=%s", kind, type(exc).__name__)
        return False
    return True


def _render(template, language, subject_template, context):
    with translation.override(language):
        context = {"site": _site_name(), **context}
        subject = str(subject_template)  # ленивая строка вычисляется на языке получателя
        if "%(site)s" in subject:
            subject = subject % {"site": context["site"]}
        body = render_to_string(f"emails/{template}.txt", context)
    return subject, body


def queue_invitation_email(invitation, token):
    """Письмо со ссылкой-приглашением. Токен живёт только в этой функции и в письме."""
    from .models import Invitation

    link = _absolute(reverse("invite_entry"), t=token)
    subject, body = _render(
        "invitation", invitation.language, _("Invitation to %(site)s"),
        {"link": link, "until": f"{invitation.expires_at:%Y-%m-%d %H:%M}"},
    )
    pk, to = invitation.pk, invitation.email

    def send():
        ok = deliver("invitation", to, subject, body)
        now = timezone.now()
        Invitation.objects.filter(pk=pk).update(
            email_sent_at=now if ok else None, email_failed_at=None if ok else now
        )

    transaction.on_commit(send)


def queue_code_email(to, code, language):
    subject, body = _render(
        "code", language, _("Your confirmation code"),
        {"code": code, "minutes": settings.EMAIL_CODE_TTL_SECONDS // 60},
    )
    transaction.on_commit(lambda: deliver("code", to, subject, body))


def queue_existing_account_email(to, language):
    subject, body = _render("existing_account", language, _("You already have an account"), {"link": _absolute(reverse("login"))})
    transaction.on_commit(lambda: deliver("existing_account", to, subject, body))


def queue_decision_email(access_request, approved):
    """Уведомление о решении. Не содержит секретов, поэтому его можно безопасно повторить."""
    from .models import AccessRequest

    template = "request_approved" if approved else "request_rejected"
    subject_template = _("Your access request was approved") if approved else _("Your access request was not approved")
    subject, body = _render(template, access_request.language, subject_template, {"link": _absolute(reverse("login"))})
    pk, to = access_request.pk, access_request.email

    def send():
        ok = deliver(template, to, subject, body)
        now = timezone.now()
        AccessRequest.objects.filter(pk=pk).update(
            notified_at=now if ok else None, notify_failed_at=None if ok else now
        )

    transaction.on_commit(send)
