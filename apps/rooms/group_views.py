"""Экраны групп, матрицы прав и просмотра «как посетитель». Только для администратора."""
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _, gettext_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.core.decorators import admin_required
from apps.accounts.models import User
from apps.audit import events
from apps.audit.service import record

from . import access_services as svc
from . import policy
from .models import AccessGrant, Document, Group, GroupMember, Room


def _errors(request, exc):
    for message in exc.messages:
        messages.error(request, message)


def _person_label(user):
    return f"{user.name} <{user.email}>" if user.name else user.email


class NameForm(forms.Form):
    name = forms.CharField(label=gettext_lazy("Group name"), max_length=svc.MAX_GROUP_NAME)


class MemberForm(forms.Form):
    user = forms.ModelChoiceField(label=gettext_lazy("Person"), queryset=User.objects.none())

    def __init__(self, group, *args, **kwargs):
        super().__init__(*args, **kwargs)
        taken = GroupMember.objects.filter(group=group).values("user_id")
        self.fields["user"].queryset = (
            User.objects.filter(role=User.Role.VIEWER, status=User.Status.ACTIVE).exclude(pk__in=taken).order_by("email")
        )
        self.fields["user"].label_from_instance = _person_label


# --- Группы -----------------------------------------------------------------


@admin_required
@never_cache
def group_list(request, room_id):
    room = get_object_or_404(Room, pk=room_id)
    form = NameForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            group = svc.create_group(room, form.cleaned_data["name"], request.user)
        except ValidationError as exc:
            _errors(request, exc)
        else:
            messages.success(request, _("The group is created."))
            return redirect("group_detail", group_id=group.pk)
    groups = Group.objects.filter(room=room).annotate(member_count=Count("members", distinct=True))
    return render(request, "rooms/groups.html", {"room": room, "groups": groups, "form": form})


@admin_required
@never_cache
def group_detail(request, group_id):
    group = get_object_or_404(Group.objects.select_related("room"), pk=group_id)
    members = GroupMember.objects.filter(group=group).select_related("user").order_by("user__email")
    now = timezone.now()
    grants = [{"grant": g, "active": g.is_active(now)} for g in group.grants.select_related("folder")]
    return render(request, "rooms/group_detail.html", {
        "group": group, "room": group.room, "members": members, "grants": grants,
        "rename_form": NameForm(initial={"name": group.name}), "member_form": MemberForm(group),
    })


@admin_required
@require_POST
def group_rename(request, group_id):
    group = get_object_or_404(Group, pk=group_id)
    form = NameForm(request.POST)
    if form.is_valid():
        try:
            svc.rename_group(group, form.cleaned_data["name"], request.user)
        except ValidationError as exc:
            _errors(request, exc)
        else:
            messages.success(request, _("The group is renamed."))
    else:
        messages.error(request, _("Enter a name."))
    return redirect("group_detail", group_id=group.pk)


@admin_required
@require_POST
def group_delete(request, group_id):
    group = get_object_or_404(Group, pk=group_id)
    room_id = group.room_id
    svc.delete_group(group, request.user)
    messages.success(request, _("The group is deleted. Its members keep only their personal access."))
    return redirect("group_list", room_id=room_id)


@admin_required
@require_POST
def group_member_add(request, group_id):
    group = get_object_or_404(Group, pk=group_id)
    form = MemberForm(group, request.POST)
    if form.is_valid():
        try:
            svc.add_member(group, form.cleaned_data["user"], request.user)
        except ValidationError as exc:
            _errors(request, exc)
        else:
            messages.success(request, _("The person is added to the group."))
    else:
        messages.error(request, _("Choose a person."))
    return redirect("group_detail", group_id=group.pk)


@admin_required
@require_POST
def group_member_remove(request, group_id, user_id):
    group = get_object_or_404(Group, pk=group_id)
    user = get_object_or_404(User, pk=user_id)
    if svc.remove_member(group, user, request.user):
        messages.success(request, _("The person is removed from the group."))
    return redirect("group_detail", group_id=group.pk)


# --- Матрица прав -----------------------------------------------------------


def _direct_level(grants, folder_id):
    direct = [g for g in grants if g.folder_id == folder_id]
    if not direct:
        return svc.NONE
    return svc.DOWNLOAD if any(g.can_download for g in direct) else svc.VIEW


def _columns(room):
    """Столбцы матрицы: все группы комнаты и люди с личными действующими правами."""
    now = timezone.now()
    active = [g for g in AccessGrant.objects.filter(room=room, revoked_at__isnull=True).select_related("user", "group") if g.is_active(now)]
    columns = []
    for group in Group.objects.filter(room=room):
        columns.append({"key": f"g{group.pk}", "label": group.name, "group": group, "user": None,
                        "grants": [g for g in active if g.group_id == group.pk]})
    people = {}
    for g in active:
        if g.user_id:
            people.setdefault(g.user_id, {"user": g.user, "grants": []})["grants"].append(g)
    for item in sorted(people.values(), key=lambda i: i["user"].email):
        columns.append({"key": f"u{item['user'].pk}", "label": item["user"].email, "group": None, "user": item["user"],
                        "grants": item["grants"]})
    for column in columns:
        column["access"] = policy.RoomAccess(room, grants=column["grants"])
    return columns


def _matrix_rows(room, columns):
    rows = [{"folder": None, "depth": 0, "path": None}]
    rows += [{"folder": f, "depth": d, "path": p} for f, d, p in svc.folders_in_order(room)]
    for row in rows:
        fid = row["folder"].pk if row["folder"] else None
        cells = []
        for column in columns:
            direct = _direct_level(column["grants"], fid)
            inherited = svc.NONE
            access = column["access"]
            if direct == svc.NONE and access.any:
                if fid is None:
                    inherited = svc.NONE
                elif access.can_view_folder(fid):
                    inherited = svc.DOWNLOAD if access.can_download_in(fid) else svc.VIEW
            cells.append({"name": f"cell_{column['key']}_{fid or 0}", "direct": direct, "inherited": inherited})
        row["cells"] = cells
    return rows


@admin_required
@never_cache
def access_matrix(request, room_id):
    room = get_object_or_404(Room, pk=room_id)
    columns = _columns(room)
    if request.method == "POST":
        changes = 0
        with transaction.atomic():
            for column in columns:
                for row in _matrix_rows(room, [column]):
                    cell = row["cells"][0]
                    value = request.POST.get(cell["name"])
                    if value not in svc.LEVELS or value == cell["direct"]:
                        continue
                    try:
                        if svc.set_level(room, group=column["group"], user=column["user"], folder=row["folder"],
                                         level=value, actor=request.user):
                            changes += 1
                    except ValidationError as exc:
                        _errors(request, exc)
        messages.success(request, _("The access matrix is saved. Changes: %(count)d.") % {"count": changes})
        return redirect("access_matrix", room_id=room.pk)
    return render(request, "rooms/access_matrix.html", {
        "room": room, "columns": columns, "rows": _matrix_rows(room, columns),
        "levels": [(svc.NONE, _("no access")), (svc.VIEW, _("view only")), (svc.DOWNLOAD, _("can download"))],
    })


# --- «Посмотреть как» ---------------------------------------------------------


@admin_required
@never_cache
def view_as(request, room_id, user_id):
    """Только чтение: что видит посетитель, по расчёту единой политики. Документы не открываются от его имени."""
    room = get_object_or_404(Room, pk=room_id)
    visitor = get_object_or_404(User, pk=user_id, role=User.Role.VIEWER)
    record(events.VIEW_AS_OPENED, actor=request.user, target=f"room:{room.pk}:user:{visitor.pk}")
    access = policy.visitor_access(visitor, room)
    context = {"room": room, "visitor": visitor, "label": _person_label(visitor), "access": access, "nda_pending": False}
    if access is not None:
        visible = access.visible_folder_ids()
        folders = [(f, d, p) for f, d, p in svc.folders_in_order(room) if f.pk in visible]
        documents = {}
        for doc in Document.objects.filter(folder_id__in=visible).prefetch_related("versions"):
            documents.setdefault(doc.folder_id, []).append(doc)
        context.update({
            "nda_pending": policy.needs_nda(visitor, room),
            "tree": [
                {"folder": f, "depth": d, "download": access.can_download_in(f.pk), "documents": documents.get(f.pk, [])}
                for f, d, _p in folders
            ],
        })
    return render(request, "rooms/view_as.html", context)
