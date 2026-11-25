"""Вопросы и ответы. Доступ решает единая политика (apps.rooms.policy); здесь только правила самих вопросов."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.audit import events
from apps.audit.service import record
from apps.core import ratelimit
from apps.rooms import files, policy
from apps.rooms.models import Room

from . import mail
from .models import MAX_TEXT, Question

ASK_LIMIT = 10  # вопросов на посетителя
ASK_WINDOW = 3600  # в час


def attachments_dir(room_id):
    from django.conf import settings

    return settings.PRIVATE_MEDIA_ROOT / "qa" / str(room_id)


def attachment_path(question):
    return attachments_dir(question.room_id) / question.attachment_stored


# --- Кто что видит ----------------------------------------------------------


def _room_access(user, room):
    """Права в комнате или None; без принятого NDA доступа нет."""
    access = policy.access_for(user, room)
    if access is None or policy.needs_nda(user, room):
        return None
    return access


def _in_scope(access, question):
    folder_id = question.scope_folder_id
    return access.any if folder_id is None else access.can_view_folder(folder_id)


def can_view(user, question):
    """Администратор видит всё. Автор видит свой вопрос, пока у него есть доступ к комнате.
    Остальные видят только опубликованный ответ в пределах своей области доступа."""
    if not user.is_authenticated or not user.is_active:
        return False
    if policy.is_trusted_admin(user):
        return True
    access = _room_access(user, question.room)
    if access is None:
        return False
    if question.author_id == user.pk:
        return True
    return question.shared and question.answered_at is not None and _in_scope(access, question)


def can_view_attachment(user, question):
    return question.has_attachment and can_view(user, question) and (
        policy.is_trusted_admin(user) or question.answered_at is not None)


def visible_to(user):
    """Список вопросов, доступных пользователю (для посетителя: свои и опубликованные)."""
    if policy.is_trusted_admin(user):
        return Question.objects.select_related("room", "author", "assignee", "document")
    own, shared = [], []
    access_by_room = {}
    candidates = (Question.objects.select_related("room", "author", "document")
                  .filter(room__in=policy.visible_rooms(user)))
    for question in candidates.filter(author=user):
        own.append(question)
    for question in candidates.filter(shared=True, answered_at__isnull=False).exclude(author=user):
        if question.room_id not in access_by_room:
            access_by_room[question.room_id] = _room_access(user, question.room)
        access = access_by_room[question.room_id]
        if access is not None and _in_scope(access, question):
            shared.append(question)
    return sorted(own + shared, key=lambda q: (q.created_at, q.pk), reverse=True)


# --- Вопрос -----------------------------------------------------------------


def _clean_text(value, limit=MAX_TEXT):
    text = (value or "").replace("\r\n", "\n").strip()
    if "\x00" in text:
        raise ValidationError(_("The text contains characters that are not allowed."))  # PostgreSQL не хранит NUL
    if not text:
        raise ValidationError(_("Enter a text."))
    if len(text) > limit:
        raise ValidationError(_("The text is too long."))
    return text


def ask(user, room, text, folder=None, document=None):
    """Новый вопрос. Задать можно только то, что пользователь уже видит по политике доступа."""
    access = _room_access(user, room)
    if access is None or policy.is_trusted_admin(user):
        raise ValidationError(_("You cannot ask a question here."))
    if document is not None:
        if document.folder.room_id != room.pk or not access.can_view_folder(document.folder_id):
            raise ValidationError(_("You cannot ask a question here."))
        folder = document.folder
        subject = document.title
    elif folder is not None:
        if folder.room_id != room.pk or not access.can_view_folder(folder.pk):
            raise ValidationError(_("You cannot ask a question here."))
        subject = folder.name
    else:
        subject = room.name
    text = _clean_text(text)
    if not ratelimit.allow(ratelimit.make_key("qa_ask", user.pk), ASK_LIMIT, ASK_WINDOW):
        raise ValidationError(_("Too many questions. Please try again later."))
    with transaction.atomic():
        question = Question.objects.create(room=room, folder=folder, document=document, subject=subject[:200],
                                           author=user, text=text)
        record(events.QUESTION_ASKED, actor=user, target=f"question:{question.pk}")
        mail.queue_new_question(question)
    return question


# --- Ответ и ведение --------------------------------------------------------


def answer(question, admin, text, upload=None, share=False):
    """Записывает ответ (повторный вызов обновляет его). Вложение проверяется и сохраняется до записи в базу."""
    text = _clean_text(text)
    new_file = None
    if upload is not None:
        ext, _pages = files.check_upload(upload)
        stored, size, sha = files.store_upload(question.room_id, upload, base=attachments_dir)
        new_file = (stored, upload.name[:255], ext, size, sha)
    old_stored = question.attachment_stored
    first = question.answered_at is None
    try:
        with transaction.atomic():
            locked = Question.objects.select_for_update().get(pk=question.pk)
            locked.answer_text = text
            locked.answered_by = admin
            locked.answered_at = locked.answered_at or timezone.now()
            locked.status = Question.Status.ANSWERED
            locked.closed_at = None
            if new_file:
                (locked.attachment_stored, locked.attachment_name, locked.attachment_ext,
                 locked.attachment_size, locked.attachment_sha256) = new_file
            was_shared, locked.shared = locked.shared, bool(share)
            locked.save()
            record(events.QUESTION_ANSWERED, actor=admin, target=f"question:{locked.pk}")
            if locked.shared != was_shared:
                record(events.QUESTION_SHARED if locked.shared else events.QUESTION_UNSHARED,
                       actor=admin, target=f"question:{locked.pk}")
            if first:
                mail.queue_answer(locked)
            if new_file and old_stored:
                transaction.on_commit(lambda: files.remove_file(question.room_id, old_stored, base=attachments_dir))
    except BaseException:
        if new_file:
            files.remove_file(question.room_id, new_file[0], base=attachments_dir)
        raise
    question.refresh_from_db()
    return question


def assign(question, admin, assignee, due_date):
    """Ответственный (только администратор) и срок. Новый вопрос переходит «в работу»."""
    if assignee is not None and not assignee.is_admin:
        raise ValidationError(_("Choose an administrator."))
    with transaction.atomic():
        locked = Question.objects.select_for_update().get(pk=question.pk)
        locked.assignee, locked.due_date = assignee, due_date
        if locked.status == Question.Status.NEW and assignee is not None:
            locked.status = Question.Status.IN_PROGRESS
        locked.save(update_fields=["assignee", "due_date", "status"])
        record(events.QUESTION_ASSIGNED, actor=admin, target=f"question:{locked.pk}")
    question.refresh_from_db()
    return question


def set_closed(question, admin, closed):
    with transaction.atomic():
        locked = Question.objects.select_for_update().get(pk=question.pk)
        if closed:
            locked.status, locked.closed_at = Question.Status.CLOSED, timezone.now()
        else:
            locked.closed_at = None
            locked.status = (Question.Status.ANSWERED if locked.answered_at
                             else Question.Status.IN_PROGRESS if locked.assignee_id else Question.Status.NEW)
        locked.save(update_fields=["status", "closed_at"])
        record(events.QUESTION_CLOSED if closed else events.QUESTION_REOPENED, actor=admin, target=f"question:{locked.pk}")
    question.refresh_from_db()
    return question


def set_shared(question, admin, shared):
    """Публикация ответа без смены текста. Публиковать можно только отвеченный вопрос."""
    if shared and question.answered_at is None:
        raise ValidationError(_("Answer the question first."))
    if question.shared == shared:
        return question
    with transaction.atomic():
        Question.objects.filter(pk=question.pk).update(shared=shared)
        record(events.QUESTION_SHARED if shared else events.QUESTION_UNSHARED, actor=admin, target=f"question:{question.pk}")
    question.refresh_from_db()
    return question


def administrators():
    return User.objects.filter(role=User.Role.ADMIN, status=User.Status.ACTIVE).order_by("email")


def rooms_for_filter():
    return Room.objects.all()
