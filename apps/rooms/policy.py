"""Единая политика доступа. Все проверки (список, просмотр, скачивание) идут только через неё.

Закрыто по умолчанию: без действующего гранта пользователь не видит ничего, даже названий.
Грант на папку покрывает её и вложенные папки; несколько грантов объединяются.
Отозванный или истёкший грант перестаёт действовать на ближайшем же запросе.
"""
from django.db.models import Q
from django.utils import timezone

from .models import AccessGrant, Folder, NdaAcceptance, Room


def is_trusted_admin(user):
    """Администратор с подтверждённым вторым фактором в текущей сессии."""
    verified = getattr(user, "is_verified", None)
    return bool(
        user.is_authenticated and user.is_active and user.is_admin and callable(verified) and verified()
    )


def _active_grants(user):
    now = timezone.now()
    return AccessGrant.objects.filter(user=user, revoked_at__isnull=True).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))


class RoomAccess:
    """Права пользователя внутри одной комнаты."""

    def __init__(self, room, admin=False, grants=()):
        self.room = room
        self.admin = admin
        self.whole = admin or any(g.folder_id is None for g in grants)
        self.whole_download = admin or any(g.folder_id is None and g.can_download for g in grants)
        self.folder_grants = {g.folder_id for g in grants if g.folder_id is not None}
        self.download_folder_grants = {g.folder_id for g in grants if g.folder_id is not None and g.can_download}
        self._parents = None

    @property
    def any(self):
        return self.whole or bool(self.folder_grants)

    def _parent_map(self):
        if self._parents is None:
            self._parents = dict(Folder.objects.filter(room=self.room).values_list("id", "parent_id"))
        return self._parents

    def _chain(self, folder_id):
        parents, chain = self._parent_map(), []
        while folder_id is not None and folder_id not in chain:
            chain.append(folder_id)
            folder_id = parents.get(folder_id)
        return chain

    def can_view_folder(self, folder_id):
        return self.whole or any(f in self.folder_grants for f in self._chain(folder_id))

    def can_download_in(self, folder_id):
        return self.whole_download or any(f in self.download_folder_grants for f in self._chain(folder_id))

    def visible_folder_ids(self):
        """Идентификаторы папок, которые пользователь видит."""
        parents = self._parent_map()
        if self.whole:
            return set(parents)
        return {fid for fid in parents if self.can_view_folder(fid)}

    def root_folder_ids(self):
        """Папки верхнего уровня для пользователя: для гранта на подпапку именно она становится корнем."""
        parents = self._parent_map()
        visible = self.visible_folder_ids()
        return sorted(fid for fid in visible if parents[fid] is None or parents[fid] not in visible)


def access_for(user, room):
    """Права пользователя в комнате или None, если доступа нет совсем."""
    if not user.is_authenticated or not user.is_active:
        return None
    if is_trusted_admin(user):
        return RoomAccess(room, admin=True)
    if user.is_admin:
        return None  # администратор без второго фактора ничего не получает
    grants = list(_active_grants(user).filter(room=room))
    access = RoomAccess(room, grants=grants)
    return access if access.any else None


def visible_rooms(user):
    if not user.is_authenticated or not user.is_active:
        return Room.objects.none()
    if is_trusted_admin(user):
        return Room.objects.all()
    if user.is_admin:
        return Room.objects.none()
    return Room.objects.filter(id__in=_active_grants(user).values("room_id")).distinct()


def needs_nda(user, room):
    """Нужно ли пользователю принять NDA, прежде чем увидеть содержимое. Администратор освобождён."""
    if not room.nda_required or is_trusted_admin(user):
        return False
    current = room.current_nda
    if current is None:
        return True  # NDA требуется, но текст не опубликован: закрыто
    return not NdaAcceptance.objects.filter(user=user, version=current).exists()


def nda_missing(room):
    """True, если NDA требуется, но администратор ещё не опубликовал текст."""
    return room.nda_required and room.current_nda is None


def can_view_document(user, document):
    access = access_for(user, document.folder.room)
    return bool(access and access.can_view_folder(document.folder_id) and not needs_nda(user, document.folder.room))


def can_download_document(user, document):
    access = access_for(user, document.folder.room)
    return bool(
        access
        and access.can_view_folder(document.folder_id)
        and access.can_download_in(document.folder_id)
        and not needs_nda(user, document.folder.room)
    )
