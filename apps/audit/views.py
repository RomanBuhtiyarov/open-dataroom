"""Журнал аудита для администратора: просмотр с фильтрами и выгрузка в CSV."""
import csv
import datetime

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.core.decorators import admin_required

from . import chain, events, labels
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
        "request_id": event.request_id,
        "outcome_label": labels.outcome_label(event.outcome),
        "seq": event.seq,
        "mac": event.chain_mac,
        "legacy": event.legacy,
    }


BROKEN_REASONS = {
    "mismatch": gettext_lazy("an entry was changed"),
    "gap": gettext_lazy("an entry is missing"),
    "unsealed": gettext_lazy("an entry was added without a signature"),
    "head": gettext_lazy("the end of the log does not match the recorded head"),
    "anchor_mismatch": gettext_lazy("an entry differs from the anchor"),
    "anchor_missing": gettext_lazy("the entry named in the anchor is gone"),
}


def _last_check():
    """Последняя проверка берётся из самого журнала: событие audit.verified."""
    event = AuditEvent.objects.filter(code=events.AUDIT_VERIFIED).order_by("-seq", "-id").first()
    if event is None:
        return None
    parts = event.target.split(":")
    if event.outcome == AuditEvent.Outcome.OK:
        return {"ok": True, "time": event.created_at.astimezone(UTC), "seq": parts[-1]}
    return {"ok": False, "time": event.created_at.astimezone(UTC), "seq": parts[1] if len(parts) > 1 else "",
            "reason": BROKEN_REASONS.get(parts[2] if len(parts) > 2 else "", "")}


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
        "check": _last_check(), "head_seq": chain.current_head()[0],
    })


@admin_required
@require_POST
def audit_verify(request):
    anchor_text = (request.POST.get("anchor") or "").strip()
    anchor = chain.parse_anchor(anchor_text) if anchor_text else None
    if anchor_text and anchor is None:
        messages.error(request, _("The anchor line is not valid."))
        return redirect("audit_list")
    result = chain.verify(anchor)
    if result.ok:
        record(events.AUDIT_VERIFIED, actor=request.user, target=f"seq:{result.last_seq}")
        messages.success(request, _("The log is intact: %(count)s entries checked.") % {"count": result.checked})
    else:
        record(events.AUDIT_VERIFIED, actor=request.user, target=f"broken:{result.broken_at}:{result.reason}",
               outcome=AuditEvent.Outcome.FAILED)
        messages.error(request, _("The chain is broken at entry %(seq)s.") % {"seq": result.broken_at})
    return redirect("audit_list")


@admin_required
@require_GET
@never_cache
def audit_anchor(request):
    line = chain.anchor_line()
    if not line:
        messages.error(request, _("The audit log is empty."))
        return redirect("audit_list")
    record(events.AUDIT_ANCHOR_ISSUED, actor=request.user, target=f"seq:{line.split()[2]}")
    response = HttpResponse(line + "\n", content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="audit-anchor.txt"'
    return response


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
    writer.writerow(["time_utc", "actor", "event_code", "event", "target", "outcome", "request_id", "seq", "chain_mac"])
    count = 0
    for event in _filtered(form)[:EXPORT_LIMIT]:
        row = _row(event)
        writer.writerow([row["time"].strftime("%Y-%m-%d %H:%M:%S"), _csv_safe(row["actor"]), row["code"],
                         _csv_safe(row["label"]), _csv_safe(row["target"]), row["outcome"],
                         _csv_safe(row["request_id"]), row["seq"] if row["seq"] is not None else "", row["mac"]])
        count += 1
    record(events.AUDIT_EXPORTED, actor=request.user, target=f"rows:{count}")
    return response
