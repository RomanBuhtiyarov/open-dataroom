"""Письма о вопросах. Текст вопроса и ответа в письмо не входит: только ссылка. Отправка после фиксации транзакции."""
from django.db import transaction
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.core.models import InstanceSettings
from apps.invites.mail import _render, absolute, deliver


def _language(user):
    return user.language or InstanceSettings.current().effective_locale


def queue_new_question(question):
    from apps.accounts.models import User

    link = absolute(reverse("question_detail", args=[question.pk]))
    messages = []
    for admin in User.objects.filter(role=User.Role.ADMIN, status=User.Status.ACTIVE):
        subject, body = _render("question_new", _language(admin), _("New question in %(site)s"), {"link": link})
        messages.append((admin.email, subject, body))

    def send():
        for to, subject, body in messages:
            deliver("question_new", to, subject, body)

    transaction.on_commit(send)


def queue_answer(question):
    author = question.author
    if author is None:
        return
    link = absolute(reverse("question_detail", args=[question.pk]))
    subject, body = _render("question_answered", _language(author), _("Your question was answered"), {"link": link})
    to = author.email
    transaction.on_commit(lambda: deliver("question_answered", to, subject, body))
