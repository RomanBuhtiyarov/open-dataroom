"""Группы посетителей и уровни доступа. Права только добавляются: запрещающих правил нет."""
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.audit import events
from apps.audit.service import record

from .models import AccessGrant, Folder, Group, GroupMember

MAX_GROUP_NAME = 80
NONE, VIEW, DOWNLOAD = "none", "view", "download"
LEVELS = (NONE, VIEW, DOWNLOAD)


def _clean_group_name(value):
    name = " ".join((value or "").split())
    if not name:
        raise ValidationError(_("Enter a name."))
    if len(name) > MAX_GROUP_NAME:
        raise ValidationError(_("The name is too long."))
    return name


def _name_taken(room, name, exclude_id=None):
    return Group.objects.filter(room=room).annotate(n=Lower("name")).filter(n=name.lower()).exclude(pk=exclude_id).exists()


def create_group(room, name, actor):
    name = _clean_group_name(name)
    if _name_taken(room, name):
        raise ValidationError(_("A group with this name already exists in the room."))
    try:
        with transaction.atomic():
            group = Group.objects.create(room=room, name=name, created_by=actor)
            record(events.GROUP_CREATED, actor=actor, target=f"group:{group.pk}")
    except IntegrityError:
        raise ValidationError(_("A group with this name already exists in the room."))
    return group


def rename_group(group, name, actor):
    name = _clean_group_name(name)
    if _name_taken(group.room, name, exclude_id=group.pk):
        raise ValidationError(_("A group with this name already exists in the room."))
    try:
        with transaction.atomic():
            group.name = name
            group.save(update_fields=["name"])
            record(events.GROUP_RENAMED, actor=actor, target=f"group:{group.pk}")
    except IntegrityError:
        raise ValidationError(_("A group with this name already exists in the room."))
    return group


def delete_group(group, actor):
    """Удаление группы убирает и её права: участники сохраняют только личные права."""
    with transaction.atomic():
        pk = group.pk
        group.delete()
        record(events.GROUP_DELETED, actor=actor, target=f"group:{pk}")


def add_member(group, user, actor):
    """Добавляет посетителя. Возвращает True, если он добавлен сейчас (повтор ничего не делает)."""
    if user.role != User.Role.VIEWER or user.status != User.Status.ACTIVE:
        raise ValidationError(_("Only active visitors can be added to a group."))
    with transaction.atomic():
        _member, created = GroupMember.objects.get_or_create(group=group, user=user, defaults={"added_by": actor})
        if created:
            record(events.GROUP_MEMBER_ADDED, actor=actor, target=f"group:{group.pk}:user:{user.pk}")
    return created


def remove_member(group, user, actor):
    with transaction.atomic():
        removed, _details = GroupMember.objects.filter(group=group, user=user).delete()
        if removed:
            record(events.GROUP_MEMBER_REMOVED, actor=actor, target=f"group:{group.pk}:user:{user.pk}")
    return bool(removed)


def _active(qs, now):
    return [g for g in qs if g.is_active(now)]


def set_level(room, *, group=None, user=None, folder=None, level, actor, expires_at=None):
    """Задаёт уровень доступа субъекта к комнате (folder=None) или папке: нет, просмотр, скачивание.

    Старое право отзывается и создаётся новое, поэтому в журнале видна каждая перемена. Срок прежнего права сохраняется.
    """
    if level not in LEVELS or (group is None) == (user is None):
        raise ValidationError(_("Invalid request."))
    if group is not None and group.room_id != room.id:
        raise ValidationError(_("The group does not belong to this room."))
    if folder is not None and folder.room_id != room.id:
        raise ValidationError(_("The folder does not belong to this room."))
    if user is not None and (user.role != User.Role.VIEWER or user.status != User.Status.ACTIVE):
        raise ValidationError(_("Only active visitors can get access."))
    now = timezone.now()
    with transaction.atomic():
        current = _active(
            AccessGrant.objects.select_for_update().filter(room=room, folder=folder, group=group, user=user, revoked_at__isnull=True), now,
        )
        wanted_download = level == DOWNLOAD
        if level != NONE and any(g.can_download == wanted_download for g in current) and len(current) == 1:
            return False  # уже так и есть
        kept_expiry = expires_at if expires_at is not None else next((g.expires_at for g in current if g.expires_at), None)
        for grant in current:
            grant.revoked_at = now
            grant.save(update_fields=["revoked_at"])
            record(events.GRANT_REVOKED, actor=actor, target=f"grant:{grant.pk}")
        if level != NONE:
            grant = AccessGrant.objects.create(
                room=room, folder=folder, group=group, user=user, can_download=wanted_download, expires_at=kept_expiry, created_by=actor,
            )
            record(events.GRANT_CREATED, actor=actor, target=f"grant:{grant.pk}")
        return True


def folders_in_order(room):
    """Папки комнаты в порядке дерева: [(папка, глубина, путь)]."""
    folders = list(Folder.objects.filter(room=room).order_by(Lower("name"), "id"))
    children = {}
    for f in folders:
        children.setdefault(f.parent_id, []).append(f)
    rows = []

    def walk(parent_id, depth, trail):
        for f in children.get(parent_id, []):
            path = trail + [f.name]
            rows.append((f, depth, " / ".join(path)))
            walk(f.pk, depth + 1, path)

    walk(None, 0, [])
    return rows
