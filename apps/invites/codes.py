"""Одноразовые коды подтверждения адреса."""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import security
from .models import EmailCode


def _subject(invitation, access_request):
    if invitation is not None:
        return "invitation", invitation.pk, {"invitation": invitation}
    return "request", access_request.pk, {"access_request": access_request}


def issue_code(invitation=None, access_request=None):
    """Создаёт новый код и аннулирует прежние. Возвращает открытый код (его нужно отправить сразу)."""
    kind, subject_id, lookup = _subject(invitation, access_request)
    now = timezone.now()
    code = security.new_code()
    with transaction.atomic():
        EmailCode.objects.filter(consumed_at__isnull=True, **lookup).update(consumed_at=now)
        EmailCode.objects.create(
            code_hash=security.hash_code(kind, subject_id, code),
            expires_at=now + timedelta(seconds=settings.EMAIL_CODE_TTL_SECONDS),
            **lookup,
        )
    return code


def verify_code(code, invitation=None, access_request=None):
    """Проверяет код. Число попыток ограничено; успешный код гасится. Попытка учитывается и при ошибке."""
    kind, subject_id, lookup = _subject(invitation, access_request)
    code = "".join((code or "").split())
    now = timezone.now()
    with transaction.atomic():
        row = (
            EmailCode.objects.select_for_update()
            .filter(consumed_at__isnull=True, expires_at__gt=now, **lookup)
            .order_by("-created_at", "-id")
            .first()
        )
        if row is None or row.attempts >= settings.EMAIL_CODE_MAX_ATTEMPTS:
            return False
        row.attempts += 1
        ok = code.isdigit() and len(code) == 6 and security.codes_equal(row.code_hash, security.hash_code(kind, subject_id, code))
        if ok:
            row.consumed_at = now
        row.save(update_fields=["attempts", "consumed_at"])
        return ok
