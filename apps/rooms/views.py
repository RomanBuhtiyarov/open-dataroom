from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.audit import events
from apps.audit.models import AuditEvent
from apps.audit.service import record
from apps.core.decorators import admin_required
from apps.core.instance import get_instance
from apps.core.models import InstanceSettings

from . import files, policy, services
from .forms import FolderNameForm, GrantForm, NdaAcceptForm, NdaForm, RoomCreateForm, RoomForm, UploadForm, VersionForm
from .models import AccessGrant, Document, Folder, NdaAcceptance, NdaText, NdaVersion, Room


def _room_access_or_404(request, room_id):
    """Комната и права пользователя. Нет доступа — 404: существование комнаты не раскрывается."""
    room = get_object_or_404(Room, pk=room_id)
    access = policy.access_for(request.user, room)
    if access is None:
        raise Http404
    return room, access


def _room_url(room_id, folder_id=None):
    url = reverse("room_detail", args=[room_id])
    return f"{url}?folder={folder_id}" if folder_id else url


def _errors(request, exc):
    for message in exc.messages:
        messages.error(request, message)


@login_required
@never_cache
def room_list(request):
    # Пока экземпляр не настроен, администратор сначала проходит первичную настройку.
    if request.user.is_admin and not get_instance(request).is_setup_complete:
        return redirect("setup")
    rooms = policy.visible_rooms(request.user)
    return render(request, "rooms/room_list.html", {"rooms": rooms})


@admin_required
@never_cache
def room_create(request):
    initial = {"template": get_instance(request).room_template}
    form = RoomCreateForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            room = form.save(commit=False)
            room.created_by = request.user
            room.save()
            created = services.apply_template(room, form.cleaned_data["template"], get_instance(request).effective_locale)
        record(events.ROOM_CREATED, actor=request.user, target=f"room:{room.pk}")
        if created:
            record(events.ROOM_TEMPLATE_APPLIED, actor=request.user, target=f"room:{room.pk}")
        messages.success(request, _("The room is created."))
        return redirect("room_detail", room_id=room.pk)
    return render(request, "rooms/room_form.html", {"form": form, "room": None})


@admin_required
@never_cache
def room_edit(request, room_id):
    room = get_object_or_404(Room, pk=room_id)
    form = RoomForm(request.POST or None, instance=room)
    if request.method == "POST" and form.is_valid():
        form.save()
        record(events.ROOM_UPDATED, actor=request.user, target=f"room:{room.pk}")
        messages.success(request, _("The room is saved."))
        return redirect("room_detail", room_id=room.pk)
    return render(request, "rooms/room_form.html", {"form": form, "room": room})


@admin_required
@require_POST
def room_apply_template(request, room_id):
    room = get_object_or_404(Room, pk=room_id)
    key = request.POST.get("template", "")
    if key not in services.ROOM_TEMPLATES:
        raise Http404
    created = services.apply_template(room, key, get_instance(request).effective_locale)
    record(events.ROOM_TEMPLATE_APPLIED, actor=request.user, target=f"room:{room.pk}")
    messages.success(request, _("Template applied. New folders: %(count)d.") % {"count": created})
    return redirect("room_detail", room_id=room.pk)


def _breadcrumbs(access, folder):
    crumbs, node = [], folder
    while node is not None and access.can_view_folder(node.id):
        crumbs.append(node)
        node = node.parent
    return list(reversed(crumbs))


@login_required
@never_cache
def room_detail(request, room_id):
    room, access = _room_access_or_404(request, room_id)
    if policy.needs_nda(request.user, room):
        return redirect("nda_accept", room_id=room.pk)

    folder = None
    folder_id = request.GET.get("folder")
    if folder_id:
        folder = get_object_or_404(Folder.objects.select_related("parent"), pk=folder_id, room=room)
        if not access.can_view_folder(folder.id):
            raise Http404

    visible = access.visible_folder_ids()
    if folder is None:
        roots = set(access.root_folder_ids())
        subfolders = [f for f in Folder.objects.filter(room=room, parent=None) if f.id in roots] if access.whole else [
            f for f in Folder.objects.filter(id__in=roots)
        ]
    else:
        subfolders = [f for f in Folder.objects.filter(parent=folder) if f.id in visible]

    documents = []
    if folder is not None:
        for doc in Document.objects.filter(folder=folder).prefetch_related("versions"):
            versions = list(doc.versions.all())
            documents.append({"doc": doc, "version": versions[0] if versions else None,
                              "can_download": policy.can_download_document(request.user, doc)})

    context = {
        "room": room, "access": access, "folder": folder, "subfolders": subfolders, "documents": documents,
        "crumbs": _breadcrumbs(access, folder) if folder else [],
        "templates": [(key, label) for key, label in InstanceSettings.RoomTemplate.choices if key != "empty"],
        "name_form": FolderNameForm(),
    }
    return render(request, "rooms/room_detail.html", context)


# --- Папки ------------------------------------------------------------------


@admin_required
@require_POST
def folder_create(request, room_id):
    room = get_object_or_404(Room, pk=room_id)
    parent = None
    if request.POST.get("parent"):
        parent = get_object_or_404(Folder, pk=request.POST["parent"], room=room)
    form = FolderNameForm(request.POST)
    target = redirect(_room_url(room.pk, parent.pk if parent else None))
    if not form.is_valid():
        messages.error(request, _("Enter a name."))
        return target
    try:
        folder = services.create_folder(room, parent, form.cleaned_data["name"])
    except ValidationError as exc:
        _errors(request, exc)
        return target
    record(events.FOLDER_CREATED, actor=request.user, target=f"folder:{folder.pk}")
    messages.success(request, _("The folder is created."))
    return target


@admin_required
@require_POST
def folder_rename(request, folder_id):
    folder = get_object_or_404(Folder.objects.select_related("room", "parent"), pk=folder_id)
    target = redirect(_room_url(folder.room_id, folder.pk))
    try:
        services.rename_folder(folder, request.POST.get("name", ""))
    except ValidationError as exc:
        _errors(request, exc)
        return target
    record(events.FOLDER_RENAMED, actor=request.user, target=f"folder:{folder.pk}")
    messages.success(request, _("The folder is renamed."))
    return target


@admin_required
@require_POST
def folder_delete(request, folder_id):
    folder = get_object_or_404(Folder.objects.select_related("room", "parent"), pk=folder_id)
    back = redirect(_room_url(folder.room_id, folder.parent_id))
    if folder.children.exists() or folder.documents.exists():
        messages.error(request, _("Only an empty folder can be deleted."))
        return back
    folder_pk = folder.pk
    folder.delete()
    record(events.FOLDER_DELETED, actor=request.user, target=f"folder:{folder_pk}")
    messages.success(request, _("The folder is deleted."))
    return back


# --- Документы --------------------------------------------------------------


@admin_required
@never_cache
def document_upload(request, room_id):
    room = get_object_or_404(Room, pk=room_id)
    if not Folder.objects.filter(room=room).exists():
        messages.error(request, _("Create a folder first."))
        return redirect("room_detail", room_id=room.pk)
    initial = {"folder": request.GET.get("folder")}
    form = UploadForm(room, request.POST or None, request.FILES or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            document, _version = services.create_document(
                form.cleaned_data["folder"], form.cleaned_data["title"], form.cleaned_data["file"], request.user
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            record(events.DOCUMENT_UPLOADED, actor=request.user, target=f"document:{document.pk}:v1")
            messages.success(request, _("The document is uploaded."))
            return redirect("document_detail", document_id=document.pk)
    return render(request, "rooms/document_upload.html", {"form": form, "room": room})


@login_required
@never_cache
def document_detail(request, document_id):
    document = get_object_or_404(Document.objects.select_related("folder__room", "folder__parent"), pk=document_id)
    room = document.folder.room
    access = policy.access_for(request.user, room)
    if access is None or not access.can_view_folder(document.folder_id):
        raise Http404
    if policy.needs_nda(request.user, room):
        return redirect("nda_accept", room_id=room.pk)
    versions = list(document.versions.all())
    if not access.admin:
        versions = versions[:1]  # посетители видят только актуальную версию
    return render(request, "rooms/document_detail.html", {
        "document": document, "room": room, "versions": versions, "access": access,
        "can_download": policy.can_download_document(request.user, document),
        "crumbs": _breadcrumbs(access, document.folder),
        "form": VersionForm() if access.admin else None,
    })


@admin_required
@require_POST
def version_upload(request, document_id):
    document = get_object_or_404(Document.objects.select_related("folder__room"), pk=document_id)
    form = VersionForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            version = services.add_version(document, form.cleaned_data["file"], request.user)
        except ValidationError as exc:
            _errors(request, exc)
        else:
            record(events.DOCUMENT_VERSION_ADDED, actor=request.user, target=f"document:{document.pk}:v{version.number}")
            messages.success(request, _("A new version is published."))
    else:
        messages.error(request, _("Choose a file."))
    return redirect("document_detail", document_id=document.pk)


@login_required
def document_download(request, document_id):
    """Скачивание оригинала. Единственная точка выдачи файла; доступ решает политика."""
    document = get_object_or_404(Document.objects.select_related("folder__room"), pk=document_id)
    room = document.folder.room
    access = policy.access_for(request.user, room)
    if access is None or not access.can_view_folder(document.folder_id):
        raise Http404
    if policy.needs_nda(request.user, room):
        return redirect("nda_accept", room_id=room.pk)
    if not policy.can_download_document(request.user, document):
        record(events.DOCUMENT_DOWNLOAD_DENIED, actor=request.user, target=f"document:{document.pk}",
               outcome=AuditEvent.Outcome.DENIED)
        raise PermissionDenied

    versions = document.versions.all()
    number = request.GET.get("version")
    if number and access.admin:
        version = get_object_or_404(versions, number=number if number.isdigit() else 0)
    else:
        version = versions.first()
    if version is None:
        raise Http404
    path = files.file_path(room.pk, version.stored_name)
    if not path.exists():
        raise Http404
    record(events.DOCUMENT_DOWNLOADED, actor=request.user, target=f"document:{document.pk}:v{version.number}")
    response = FileResponse(open(path, "rb"), as_attachment=True, filename=version.original_name,
                            content_type=files.ALLOWED[version.extension])
    response["Cache-Control"] = "private, no-store"
    response["Accept-Ranges"] = "none"
    return response


# --- Права доступа ----------------------------------------------------------


@admin_required
@never_cache
def access_list(request, room_id):
    room = get_object_or_404(Room, pk=room_id)
    form = GrantForm(room, request.POST or None)
    if request.method == "POST" and form.is_valid():
        grant = AccessGrant.objects.create(
            user=form.cleaned_data["user"], room=room, folder=form.cleaned_data["folder"],
            can_download=form.cleaned_data["can_download"], expires_at=form.cleaned_data["expires_on"],
            created_by=request.user,
        )
        record(events.GRANT_CREATED, actor=request.user, target=f"grant:{grant.pk}")
        messages.success(request, _("Access is granted."))
        return redirect("access_list", room_id=room.pk)
    grants = room.grants.select_related("user", "folder")
    now = timezone.now()
    rows = [{"grant": g, "active": g.is_active(now)} for g in grants]
    return render(request, "rooms/access_list.html", {"room": room, "form": form, "rows": rows})


@admin_required
@require_POST
def grant_revoke(request, grant_id):
    grant = get_object_or_404(AccessGrant, pk=grant_id)
    if grant.revoked_at is None:
        grant.revoked_at = timezone.now()
        grant.save(update_fields=["revoked_at"])
        record(events.GRANT_REVOKED, actor=request.user, target=f"grant:{grant.pk}")
        messages.success(request, _("Access is revoked."))
    return redirect("access_list", room_id=grant.room_id)


# --- NDA --------------------------------------------------------------------


@login_required
@never_cache
def nda_accept(request, room_id):
    room, access = _room_access_or_404(request, room_id)
    if access.admin or not room.nda_required:
        return redirect("room_detail", room_id=room.pk)
    current = room.current_nda
    if current is not None and NdaAcceptance.objects.filter(user=request.user, version=current).exists():
        return redirect("room_detail", room_id=room.pk)
    language, body = ("", "")
    if current is not None:
        language, body = current.text_for(translation.get_language(), get_instance(request).effective_locale)
    form = NdaAcceptForm(request.POST or None)
    if request.method == "POST" and current is not None and form.is_valid():
        NdaAcceptance.objects.get_or_create(user=request.user, version=current, defaults={"language": language})
        record(events.NDA_ACCEPTED, actor=request.user, target=f"room:{room.pk}:nda:{current.number}")
        return redirect("room_detail", room_id=room.pk)
    return render(request, "rooms/nda_accept.html", {"room": room, "body": body, "form": form, "published": current is not None})


@admin_required
@never_cache
def nda_edit(request, room_id):
    room = get_object_or_404(Room, pk=room_id)
    default = get_instance(request).effective_locale
    current = room.current_nda
    initial = {}
    if current is not None:
        initial = {f"body_{t.language}": t.body for t in current.texts.all()}
    form = NdaForm(request.POST or None, initial=initial, default_language=default)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            number = (room.nda_versions.aggregate(m=Max("number"))["m"] or 0) + 1
            version = NdaVersion.objects.create(room=room, number=number, created_by=request.user)
            for code, body in form.texts().items():
                if body:
                    NdaText.objects.create(version=version, language=code, body=body)
        record(events.NDA_PUBLISHED, actor=request.user, target=f"room:{room.pk}:nda:{version.number}")
        messages.success(request, _("The new NDA version is published. Everyone must accept it again."))
        return redirect("nda_edit", room_id=room.pk)
    accepted = current.acceptances.count() if current is not None else 0
    return render(request, "rooms/nda_edit.html", {"room": room, "form": form, "current": current, "accepted": accepted})
