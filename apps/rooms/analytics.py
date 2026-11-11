"""Аналитика просмотров: сколько посетителей открыло документ, сколько времени, что осталось неоткрытым.

Учитываются только посетители (не администраторы). Время приблизительное: секунды, пока страница видна на экране.
Сведения берутся из журнала аудита (кто открыл) и из таблицы PageTime (сколько времени на странице).
"""
import csv
import datetime

from django.db import transaction
from django.db.models import Count, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import User
from apps.audit import events
from apps.audit.models import AuditEvent
from apps.audit.service import record
from apps.core.decorators import admin_required

from . import policy
from .models import Document, PageTime, Room

PULSE_MIN = 5  # секунд между учитываемыми сигналами одной страницы
PULSE_MAX_GAP = 12  # больший перерыв — страницу покидали; время перерыва не засчитывается
PAGE_SECONDS_CAP = 3600
MAX_PAGES_PER_PULSE = 20  # страниц в одном сигнале: на экране их помещается немного
DAYS = 30


def _document_id(target):
    """document:<id>:v<n>[:view:<code>] -> id; иначе None."""
    parts = target.split(":")
    return int(parts[1]) if len(parts) >= 2 and parts[0] == "document" and parts[1].isdigit() else None


def _visitor_views():
    """(посетитель, документ, время) по событиям просмотра. Администраторы не учитываются."""
    qs = (AuditEvent.objects.filter(code=events.DOCUMENT_VIEWED, actor__isnull=False)
          .exclude(actor__role=User.Role.ADMIN).values_list("actor_id", "target", "created_at").iterator())
    for actor_id, target, created in qs:
        document_id = _document_id(target)
        if document_id is not None:
            yield actor_id, document_id, created


def summary(now=None):
    now = now or timezone.now()
    views = list(_visitor_views())
    docs = {d.pk: d for d in Document.objects.select_related("folder__room")}
    seconds = dict(PageTime.objects.values("version__document_id").annotate(s=Sum("seconds")).values_list("version__document_id", "s"))

    per_doc = {}
    for actor_id, document_id, created in views:
        row = per_doc.setdefault(document_id, {"users": set(), "views": 0, "last": None})
        row["users"].add(actor_id)
        row["views"] += 1
        row["last"] = max(row["last"], created) if row["last"] else created
    documents = []
    for pk, doc in docs.items():
        row = per_doc.get(pk)
        documents.append({
            "document": doc, "room": doc.folder.room, "viewers": len(row["users"]) if row else 0,
            "views": row["views"] if row else 0, "seconds": seconds.get(pk, 0) or 0, "last": row["last"] if row else None,
        })
    documents.sort(key=lambda r: (-r["viewers"], -r["seconds"], r["document"].title.lower()))

    opened = {(a, d) for a, d, _ in views}
    visitors = []
    rooms = list(Room.objects.all())
    docs_by_room = {}
    for doc in docs.values():
        docs_by_room.setdefault(doc.folder.room_id, []).append(doc)
    for user in User.objects.filter(role=User.Role.VIEWER, status=User.Status.ACTIVE).order_by("email"):
        visible = []
        for room in rooms:
            access = policy.access_for(user, room)
            if access is None:
                continue
            visible += [d for d in docs_by_room.get(room.pk, []) if access.can_view_folder(d.folder_id)]
        if not visible:
            continue
        unopened = [d for d in visible if (user.pk, d.pk) not in opened]
        visitors.append({"user": user, "visible": len(visible), "opened": len(visible) - len(unopened), "unopened": unopened})

    start = (now - datetime.timedelta(days=DAYS - 1)).date()
    by_day = {}
    for actor_id, _doc, created in views:
        day = created.astimezone(datetime.timezone.utc).date()
        if day >= start:
            slot = by_day.setdefault(day, {"views": 0, "users": set()})
            slot["views"] += 1
            slot["users"].add(actor_id)
    days = []
    for i in range(DAYS):
        day = start + datetime.timedelta(days=i)
        slot = by_day.get(day)
        days.append({"day": day, "views": slot["views"] if slot else 0, "viewers": len(slot["users"]) if slot else 0})
    return {"documents": documents, "visitors": visitors, "days": days, "peak": max([d["views"] for d in days] + [1])}


def per_visitor_rows():
    """Для выгрузки: документ × посетитель."""
    users = {u.pk: u.email for u in User.objects.all()}
    docs = {d.pk: d for d in Document.objects.select_related("folder__room")}
    rows = {}
    for actor_id, document_id, created in _visitor_views():
        row = rows.setdefault((document_id, actor_id), {"views": 0, "last": created})
        row["views"] += 1
        row["last"] = max(row["last"], created)
    times = {}
    for user_id, doc_id, total, pages in (PageTime.objects.values("user_id", "version__document_id")
                                           .annotate(s=Sum("seconds"), p=Count("page", distinct=True))
                                           .values_list("user_id", "version__document_id", "s", "p")):
        times[(doc_id, user_id)] = (total or 0, pages)
    out = []
    for (document_id, actor_id), row in sorted(rows.items(), key=lambda kv: (docs[kv[0][0]].title.lower() if kv[0][0] in docs else "", users.get(kv[0][1], ""))):
        doc = docs.get(document_id)
        if doc is None:
            continue
        seconds, pages = times.get((document_id, actor_id), (0, 0))
        out.append([doc.folder.room.name, doc.title, users.get(actor_id, ""), row["views"], pages, seconds,
                    row["last"].astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")])
    return out


@admin_required
@require_GET
@never_cache
def analytics(request):
    return render(request, "rooms/analytics.html", summary())


@admin_required
@require_GET
@never_cache
def analytics_document(request, document_id):
    document = get_object_or_404(Document.objects.select_related("folder__room"), pk=document_id)
    version = document.versions.first()
    pages = []
    if version is not None and version.extension == "pdf":
        agg = {p["page"]: p for p in PageTime.objects.filter(version=version).values("page").annotate(
            viewers=Count("user", distinct=True), seconds=Sum("seconds"))}
        total = version.page_count or 0
        top = max([a["seconds"] or 0 for a in agg.values()] + [1])
        for number in range(1, total + 1):
            a = agg.get(number)
            pages.append({"number": number, "viewers": a["viewers"] if a else 0, "seconds": (a["seconds"] or 0) if a else 0})
    else:
        top = 1
    return render(request, "rooms/analytics_document.html", {
        "document": document, "room": document.folder.room, "version": version, "pages": pages, "top": top})


def _csv_safe(value):
    value = str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


@admin_required
@require_GET
@never_cache
def analytics_export(request):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="view-analytics.csv"'
    writer = csv.writer(response)
    writer.writerow(["room", "document", "visitor", "views", "pages_opened", "seconds", "last_viewed_utc"])
    rows = per_visitor_rows()
    for row in rows:
        writer.writerow([_csv_safe(c) if isinstance(c, str) else c for c in row])
    record(events.ANALYTICS_EXPORTED, actor=request.user, target=f"rows:{len(rows)}")
    return response


@require_POST
def document_pulse(request, document_id):
    """Сигнал «страница открыта»: прибавляет секунды просмотра. Доступ проверяется так же, как при показе страницы."""
    if not request.user.is_authenticated:
        raise Http404
    document = get_object_or_404(Document.objects.select_related("folder__room"), pk=document_id)
    room = document.folder.room
    access = policy.access_for(request.user, room)
    if access is None or not access.can_view_folder(document.folder_id) or policy.needs_nda(request.user, room):
        raise Http404
    response = HttpResponse(status=204)
    response["Cache-Control"] = "no-store"
    if access.admin:
        return response  # просмотры администратора в статистику не входят
    version = document.versions.first()
    pages = request.POST.getlist("page")[:MAX_PAGES_PER_PULSE]
    total = (version.page_count or 0) if version is not None and version.extension == "pdf" else 0
    numbers = sorted({int(p) for p in pages if p.isdigit()})
    if not numbers or numbers[0] < 1 or numbers[-1] > total:
        raise Http404
    for number in numbers:
        add_time(request.user, version, number)
    return response


def add_time(user, version, page, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        row, created = PageTime.objects.select_for_update().get_or_create(
            user=user, version=version, page=page, defaults={"first_seen": now, "last_seen": now})
        if created:
            return row
        elapsed = (now - row.last_seen).total_seconds()
        if elapsed < PULSE_MIN:
            return row
        if elapsed <= PULSE_MAX_GAP:
            row.seconds = min(row.seconds + int(elapsed), PAGE_SECONDS_CAP)
        row.last_seen = now
        row.save(update_fields=["seconds", "last_seen"])
    return row
