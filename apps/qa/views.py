"""Экраны вопросов и ответов. Каждое представление проверяет доступ через apps.rooms.policy и apps.qa.services."""
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.audit import events
from apps.audit.service import record
from apps.core import xlsx
from apps.core.decorators import admin_required
from apps.core.util import parse_int
from apps.rooms import files, policy, serving
from apps.rooms.models import Document, Folder, Room

from . import services
from .forms import AnswerForm, AskForm, AssignForm, InboxFilterForm
from .models import Question


def _errors(request, exc):
    for message in exc.messages:
        messages.error(request, message)


def _inbox(request):
    """Вопросы для администратора с фильтрами."""
    form = InboxFilterForm(request.GET or None)
    qs = Question.objects.select_related("room", "author", "assignee", "document")
    if form.is_valid():
        status, assignee = form.cleaned_data["status"], form.cleaned_data["assignee"]
        if status == "overdue":
            qs = qs.filter(status__in=[Question.Status.NEW, Question.Status.IN_PROGRESS], due_date__lt=timezone.localdate())
        elif status:
            qs = qs.filter(status=status)
        if assignee == "none":
            qs = qs.filter(assignee__isnull=True)
        elif assignee:
            qs = qs.filter(assignee_id=int(assignee))
    return form, qs


@login_required
@require_GET
@never_cache
def question_list(request):
    if policy.is_trusted_admin(request.user):
        form, qs = _inbox(request)
        today = timezone.localdate()
        rows = [{"q": q, "overdue": q.is_overdue(today)} for q in qs[:500]]
        return render(request, "qa/inbox.html", {"form": form, "rows": rows, "query": request.GET.urlencode()})
    if request.user.is_admin:
        raise PermissionDenied
    mine, others = [], []
    for question in services.visible_to(request.user):
        (mine if question.author_id == request.user.pk else others).append(question)
    return render(request, "qa/my_questions.html", {"mine": mine, "others": others})


def _scope_from(request, user):
    """Комната, папка и документ из параметров запроса. Всё, что пользователь не видит, считается отсутствующим."""
    source = request.POST if request.method == "POST" else request.GET
    document = folder = None
    document_id, folder_id, room_id = (parse_int(source.get(k, "")) for k in ("document", "folder", "room"))
    if document_id is not None:
        document = get_object_or_404(Document.objects.select_related("folder__room"), pk=document_id)
        room = document.folder.room
    elif folder_id is not None:
        folder = get_object_or_404(Folder.objects.select_related("room"), pk=folder_id)
        room = folder.room
    elif room_id is not None:
        room = get_object_or_404(Room, pk=room_id)
    else:
        raise Http404
    access = policy.access_for(user, room)
    if access is None or policy.needs_nda(user, room):
        raise Http404
    target_folder = document.folder_id if document else folder.pk if folder else None
    if target_folder is not None and not access.can_view_folder(target_folder):
        raise Http404
    return room, folder, document


@login_required
@never_cache
def question_ask(request):
    if request.user.is_admin:
        raise PermissionDenied  # администратор отвечает на вопросы, а не задаёт их
    room, folder, document = _scope_from(request, request.user)
    form = AskForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            question = services.ask(request.user, room, form.cleaned_data["text"], folder=folder, document=document)
        except ValidationError as exc:
            _errors(request, exc)
        else:
            messages.success(request, _("Your question is sent."))
            return redirect("question_detail", question_id=question.pk)
    subject = document.title if document else folder.name if folder else room.name
    return render(request, "qa/ask.html", {
        "form": form, "room": room, "folder": folder, "document": document, "subject": subject})


@login_required
@never_cache
def question_detail(request, question_id):
    question = get_object_or_404(Question.objects.select_related("room", "author", "assignee", "answered_by", "document"), pk=question_id)
    if not services.can_view(request.user, question):
        raise Http404
    is_admin = policy.is_trusted_admin(request.user)
    context = {
        "q": question, "is_admin": is_admin, "is_author": question.author_id == request.user.pk,
        "overdue": question.is_overdue(), "can_attachment": services.can_view_attachment(request.user, question),
    }
    if is_admin:
        context["answer_form"] = AnswerForm(initial={"text": question.answer_text, "share": question.shared})
        context["assign_form"] = AssignForm(initial={"assignee": question.assignee_id, "due_date": question.due_date})
    return render(request, "qa/detail.html", context)


def _admin_question(question_id):
    return get_object_or_404(Question.objects.select_related("room", "author", "document"), pk=question_id)


@admin_required
@require_POST
def question_answer(request, question_id):
    question = _admin_question(question_id)
    form = AnswerForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            services.answer(question, request.user, form.cleaned_data["text"], form.cleaned_data["file"], form.cleaned_data["share"])
        except ValidationError as exc:
            _errors(request, exc)
        else:
            messages.success(request, _("The answer is saved."))
    else:
        messages.error(request, _("Enter a text."))
    return redirect("question_detail", question_id=question.pk)


@admin_required
@require_POST
def question_assign(request, question_id):
    question = _admin_question(question_id)
    form = AssignForm(request.POST)
    if form.is_valid():
        try:
            services.assign(question, request.user, form.cleaned_data["assignee"], form.cleaned_data["due_date"])
        except ValidationError as exc:
            _errors(request, exc)
        else:
            messages.success(request, _("Saved."))
    else:
        messages.error(request, _("Check the form."))
    return redirect("question_detail", question_id=question.pk)


@admin_required
@require_POST
def question_close(request, question_id):
    question = _admin_question(question_id)
    services.set_closed(question, request.user, request.POST.get("closed") == "1")
    messages.success(request, _("Saved."))
    return redirect("question_detail", question_id=question.pk)


@admin_required
@require_POST
def question_share(request, question_id):
    question = _admin_question(question_id)
    try:
        services.set_shared(question, request.user, request.POST.get("shared") == "1")
    except ValidationError as exc:
        _errors(request, exc)
    else:
        messages.success(request, _("Saved."))
    return redirect("question_detail", question_id=question.pk)


@login_required
def question_attachment(request, question_id):
    """Вложение к ответу. Единственная точка выдачи: доступ решают политика и правила видимости вопроса."""
    question = get_object_or_404(Question.objects.select_related("room", "document"), pk=question_id)
    if not services.can_view_attachment(request.user, question):
        raise Http404
    path = services.attachment_path(question)
    if not path.exists():
        raise Http404
    if serving.is_first_chunk(request):
        record(events.QUESTION_ATTACHMENT_DOWNLOADED, actor=request.user, target=f"question:{question.pk}")
    return serving.file_response(request, path, filename=question.attachment_name,
                                 content_type=files.ALLOWED[question.attachment_ext])


@admin_required
@require_GET
@never_cache
def question_export(request):
    _form, qs = _inbox(request)
    header = ["id", "room", "subject", "status", "asked_utc", "asker", "question", "assigned_to", "due_date",
              "answered_utc", "answered_by", "answer", "shared", "attachment"]
    rows = [header]
    count = 0
    for q in qs[:20000]:
        rows.append([
            q.pk, q.room.name, q.subject, q.status, _utc(q.created_at), q.author.email if q.author else "", q.text,
            (q.assignee.name or q.assignee.email) if q.assignee else "", q.due_date.isoformat() if q.due_date else "",
            _utc(q.answered_at), q.answered_by.email if q.answered_by_id else "", q.answer_text,
            "yes" if q.shared else "no", q.attachment_name,
        ])
        count += 1
    response = HttpResponse(xlsx.make_xlsx(rows, "Questions"),
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="questions.xlsx"'
    record(events.QUESTIONS_EXPORTED, actor=request.user, target=f"rows:{count}")
    return response


def _utc(value):
    return value.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M") if value else ""
