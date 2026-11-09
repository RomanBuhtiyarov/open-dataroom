"""Приглашения и заявки. Права выдаются только внутри одной транзакции, один раз."""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, transaction
from django.db.models.functions import Lower
from django.utils import timezone

from apps.accounts.models import User, normalize_email
from apps.audit import events
from apps.audit.service import record
from apps.rooms.models import AccessGrant

from . import mail, security
from .models import AccessRequest, Invitation


class Unavailable(Exception):
    """Приглашение или заявка уже использованы, отозваны, просрочены или не существуют."""


# --- Приглашения ------------------------------------------------------------


def create_invitation(*, email, name, language, room, folder, can_download, access_expires_at, valid_days, created_by, send_email):
    """Создаёт приглашение. Возвращает (приглашение, открытый токен). Токен больше нигде не хранится."""
    token = security.new_token()
    days = max(1, min(int(valid_days), settings.INVITATION_MAX_DAYS))
    with transaction.atomic():
        invitation = Invitation.objects.create(
            token_hash=security.hash_token(token), email=normalize_email(email), name=name, language=language,
            room=room, folder=folder, can_download=can_download, access_expires_at=access_expires_at,
            expires_at=timezone.now() + timedelta(days=days), created_by=created_by,
        )
        record(events.INVITATION_CREATED, actor=created_by, target=f"invitation:{invitation.pk}")
        if send_email:
            mail.queue_invitation_email(invitation, token)
    return invitation, token


def rotate_token(invitation_id, actor, valid_days=None, send_email=True):
    """Повторная отправка: выдаёт новый токен (старый перестаёт работать) и продлевает срок. Только для ожидающих."""
    with transaction.atomic():
        invitation = Invitation.objects.select_for_update().get(pk=invitation_id)
        if invitation.consumed_at or invitation.revoked_at:
            raise Unavailable
        token = security.new_token()
        days = valid_days or settings.INVITATION_DEFAULT_DAYS
        invitation.token_hash = security.hash_token(token)
        invitation.expires_at = timezone.now() + timedelta(days=max(1, min(int(days), settings.INVITATION_MAX_DAYS)))
        invitation.email_sent_at = invitation.email_failed_at = None
        invitation.save(update_fields=["token_hash", "expires_at", "email_sent_at", "email_failed_at"])
        record(events.INVITATION_RESENT, actor=actor, target=f"invitation:{invitation.pk}")
        if send_email:
            mail.queue_invitation_email(invitation, token)
    return invitation, token


def revoke_invitation(invitation_id, actor):
    with transaction.atomic():
        invitation = Invitation.objects.select_for_update().get(pk=invitation_id)
        if invitation.consumed_at or invitation.revoked_at:
            return False
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["revoked_at"])
        record(events.INVITATION_REVOKED, actor=actor, target=f"invitation:{invitation.pk}")
    return True


def find_by_token(token):
    return Invitation.objects.filter(token_hash=security.hash_token(token or "")).first()


def _grant_for(invitation, user):
    return AccessGrant.objects.create(
        user=user, room=invitation.room, folder=invitation.folder, can_download=invitation.can_download,
        expires_at=invitation.access_expires_at, created_by=invitation.created_by,
    )


def accept_new_user(invitation_id, *, name, password, language):
    """Создаёт учётную запись и выдаёт доступ. Приглашение «гасится» в этой же транзакции."""
    try:
        with transaction.atomic():
            invitation = Invitation.objects.select_for_update().get(pk=invitation_id)
            if not invitation.is_pending():
                raise Unavailable
            if User.objects.filter(email=invitation.email).exists():
                raise Unavailable
            user = User.objects.create_user(
                invitation.email, password, name=name, language=language, email_confirmed_at=timezone.now()
            )
            _grant_for(invitation, user)
            invitation.consumed_at = timezone.now()
            invitation.consumed_by = user
            invitation.save(update_fields=["consumed_at", "consumed_by"])
            record(events.INVITATION_ACCEPTED, actor=user, target=f"invitation:{invitation.pk}")
    except IntegrityError:
        raise Unavailable
    return user


def accept_existing_user(invitation_id, user):
    """Уже зарегистрированный адрес: после входа добавляем доступ. Повтор не выдаёт права второй раз."""
    with transaction.atomic():
        invitation = Invitation.objects.select_for_update().get(pk=invitation_id)
        if not invitation.is_pending() or normalize_email(user.email) != invitation.email:
            raise Unavailable
        if not user.is_active or user.is_admin:
            raise Unavailable
        _grant_for(invitation, user)
        invitation.consumed_at = timezone.now()
        invitation.consumed_by = user
        invitation.save(update_fields=["consumed_at", "consumed_by"])
        record(events.INVITATION_ACCEPTED, actor=user, target=f"invitation:{invitation.pk}")
    return invitation


# --- Заявки -----------------------------------------------------------------


def submit_access_request(*, email, name, password, language, message):
    """Принимает заявку. Возвращает (заявка или None, нужно ли слать код).

    Ответ для посетителя одинаков во всех случаях, чтобы не раскрывать, есть ли такой адрес в системе.
    Каждая заявка самостоятельна: повторная отправка не подменяет данные чужой неподтверждённой заявки,
    а подтвердить заявку может только тот, у кого есть код из почты и сессия, создавшая эту заявку.
    """
    email = normalize_email(email)
    if User.objects.filter(email=email).exists():
        mail.queue_existing_account_email(email, language)
        return None, False
    if AccessRequest.objects.filter(status=AccessRequest.Status.PENDING).annotate(e=Lower("email")).filter(e=email).exists():
        return None, False  # уже на рассмотрении
    stale = timezone.now() - timedelta(days=1)
    AccessRequest.objects.filter(status=AccessRequest.Status.UNCONFIRMED, created_at__lt=stale).delete()
    with transaction.atomic():
        request = AccessRequest.objects.create(
            email=email, name=name, password_hash=make_password(password), language=language, message=message
        )
        record(events.REQUEST_SUBMITTED, target=f"request:{request.pk}")
    return request, True


def confirm_request(request_id):
    """Адрес подтверждён кодом: заявка попадает в очередь. Прочие неподтверждённые заявки на этот адрес удаляются."""
    with transaction.atomic():
        request = AccessRequest.objects.select_for_update().get(pk=request_id)
        if request.status != AccessRequest.Status.UNCONFIRMED:
            return request
        others = AccessRequest.objects.exclude(pk=request.pk).annotate(e=Lower("email")).filter(e=request.email.lower())
        if others.filter(status=AccessRequest.Status.PENDING).exists():
            request.delete()  # пока шло подтверждение, заявка с этого адреса уже встала в очередь
            return None
        others.filter(status=AccessRequest.Status.UNCONFIRMED).delete()
        request.status = AccessRequest.Status.PENDING
        request.email_confirmed_at = timezone.now()
        request.save(update_fields=["status", "email_confirmed_at"])
        record(events.REQUEST_CONFIRMED, target=f"request:{request.pk}")
    return request


def approve_request(request_id, decider, *, room=None, folder=None, can_download=False, access_expires_at=None):
    """Одобряет заявку один раз: создаёт учётную запись с уже заданным паролем и, при необходимости, доступ."""
    try:
        with transaction.atomic():
            request = AccessRequest.objects.select_for_update().get(pk=request_id)
            if request.status != AccessRequest.Status.PENDING:
                raise Unavailable
            if User.objects.filter(email=request.email).exists():
                raise Unavailable
            user = User(email=request.email, name=request.name, language=request.language, email_confirmed_at=timezone.now())
            user.password = request.password_hash
            user.save()
            if room is not None:
                AccessGrant.objects.create(
                    user=user, room=room, folder=folder, can_download=can_download,
                    expires_at=access_expires_at, created_by=decider,
                )
            request.status = AccessRequest.Status.APPROVED
            request.decided_by, request.decided_at, request.user = decider, timezone.now(), user
            request.save(update_fields=["status", "decided_by", "decided_at", "user"])
            record(events.REQUEST_APPROVED, actor=decider, target=f"request:{request.pk}")
            mail.queue_decision_email(request, approved=True)
    except IntegrityError:
        raise Unavailable
    return request


def reject_request(request_id, decider):
    with transaction.atomic():
        request = AccessRequest.objects.select_for_update().get(pk=request_id)
        if request.status != AccessRequest.Status.PENDING:
            raise Unavailable
        request.status = AccessRequest.Status.REJECTED
        request.decided_by, request.decided_at = decider, timezone.now()
        request.save(update_fields=["status", "decided_by", "decided_at"])
        record(events.REQUEST_REJECTED, actor=decider, target=f"request:{request.pk}")
        mail.queue_decision_email(request, approved=False)
    return request


def retry_decision_email(request_id):
    """Повторяет только письмо о решении; права не затрагиваются."""
    request = AccessRequest.objects.get(pk=request_id)
    if request.status not in (AccessRequest.Status.APPROVED, AccessRequest.Status.REJECTED) or request.notified_at:
        raise Unavailable
    mail.queue_decision_email(request, approved=request.status == AccessRequest.Status.APPROVED)
    return request
