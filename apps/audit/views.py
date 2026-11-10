"""Журнал аудита для администратора: просмотр с фильтрами и выгрузка в CSV."""
import csv
import datetime

from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.core.decorators import admin_required

from . import events, labels
from .forms import AuditFilterForm
from .models import AuditEvent
from .service import record

PAGE_SIZE = 50
EXPORT_LIMIT = 50000
UTC = datetime.timezone.utc


def _filtered(form):
    qs = AuditEvent.objects.select_related("actor")
    if not form.is_valid():
        return qs
    data = form.cleaned_data
    if data["code"]:
        qs = qs.filter(code=data["code"])
    if data["outcome"]:
        qs = qs.filter(outcome=data["outcome"])
    if data["actor"]:
        qs = qs.filter(actor__email__icontains=data["actor"].strip().lower())
    if data["date_from"]:
        qs = qs.filter(created_at__gte=datetime.datetime.combine(data["date_from"], datetime.time.min, tzinfo=UTC))
    if data["date_to"]:
        end = datetime.datetime.combine(data["date_to"] + datetime.timedelta(days=1), datetime.time.min, tzinfo=UTC)
        qs = qs.filter(created_at__lt=end)
    return qs


def _row(event):
    return {
        "time": event.created_at.astimezone(UTC),
        "actor": event.actor.email if event.actor else "",
        "code": event.code,
        "label": labels.label(event.code),
        "target": event.target,
        "outcome": event.outcome,
        "outcome_label": labels.outcome_label(event.outcome),
    }


@admin_required
@require_GET
@never_cache
def audit_list(request):
    form = AuditFilterForm(request.GET or None)
    page = Paginator(_filtered(form), PAGE_SIZE).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "audit/audit_list.html", {
        "form": form, "page": page, "rows": [_row(e) for e in page], "query": query.urlencode(),
    })


def _csv_safe(value):
    """Защита от формул в таблицах: ячейка, начинающаяся с = + - @, получает префикс."""
    value = str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


@admin_required
@require_GET
@never_cache
def audit_export(request):
    form = AuditFilterForm(request.GET or None)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="audit-log.csv"'
    writer = csv.writer(response)
    writer.writerow(["time_utc", "actor", "event_code", "event", "target", "outcome"])
    count = 0
    for event in _filtered(form)[:EXPORT_LIMIT]:
        row = _row(event)
        writer.writerow([row["time"].strftime("%Y-%m-%d %H:%M:%S"), _csv_safe(row["actor"]), row["code"],
                         _csv_safe(row["label"]), _csv_safe(row["target"]), row["outcome"]])
        count += 1
    record(events.AUDIT_EXPORTED, actor=request.user, target=f"rows:{count}")
    return response
