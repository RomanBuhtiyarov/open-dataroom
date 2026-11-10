from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.accounts.models import User
from apps.audit import events
from apps.audit.models import AuditEvent
from apps.audit.service import record
from apps.core import ratelimit
from apps.core.instance import get_instance
from apps.core.views import set_language_cookie

from . import codes, mail, security, services
from .forms import AcceptInviteForm, CodeForm, RequestAccessForm
from .models import AccessRequest, Invitation

INVITE_SESSION_KEY = "invite_id"
REQUEST_SESSION_KEY = "access_request_id"
STATUS_SESSION_KEY = "access_request_status_id"

HOUR = 3600


def _too_many(request, status=429):
    return render(request, "invites/too_many.html", status=status)


def _unavailable(request):
    return render(request, "invites/unavailable.html", status=404)


def _send_code(request, scope, subject_id, to, language, **subject):
    """Отправка кода с ограничениями: не чаще раза в минуту на адресата и не более пяти в час; плюс лимит по IP."""
    if not ratelimit.allow(ratelimit.make_key(f"code_ip", ratelimit.client_ip(request)), 20, HOUR):
        return False
    if ratelimit.exceeded(ratelimit.make_key(f"code_{scope}_min", subject_id), 1, 60):
        return False
    if not ratelimit.allow(ratelimit.make_key(f"code_{scope}_hour", subject_id), 5, HOUR):
        return False
    ratelimit.hit(ratelimit.make_key(f"code_{scope}_min", subject_id))
    code = codes.issue_code(**subject)
    mail.queue_code_email(to, code, language)
    return True


# --- Приглашение по ссылке или QR ------------------------------------------


@never_cache
@require_http_methods(["GET", "POST"])
def invite_entry(request):
    """Страница приглашения. Открытие ссылки ничего не расходует: приглашение гасит только POST «принять»."""
    token = request.GET.get("t")
    if token is not None:
        fail_key = ratelimit.make_key("invite_lookup_fail", ratelimit.client_ip(request))
        if ratelimit.exceeded(fail_key, 20, 600):
            return _too_many(request)
        invitation = services.find_by_token(token)
        if invitation is None or not invitation.is_pending():
            ratelimit.hit(fail_key)
            return _unavailable(request)
        # Токен убираем из адресной строки: дальше работаем по сессии, на «чистом» адресе.
        request.session[INVITE_SESSION_KEY] = invitation.pk
        response = redirect("invite_entry")
        if settings.LANGUAGE_COOKIE_NAME not in request.COOKIES:
            set_language_cookie(response, invitation.language)
        return response

    invitation = Invitation.objects.filter(pk=request.session.get(INVITE_SESSION_KEY)).first()
    if invitation is None or not invitation.is_pending():
        return _unavailable(request)

    existing = User.objects.filter(email=invitation.email).first()
    context = {"invitation": invitation, "masked_email": security.mask_email(invitation.email)}
    if existing is not None:
        return _existing_user_flow(request, invitation, existing, context)
    return _new_user_flow(request, invitation, context)


def _new_user_flow(request, invitation, context):
    form = AcceptInviteForm(request.POST or None, email=invitation.email, initial={"name": invitation.name})
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "send_code":
            if _send_code(request, "inv", invitation.pk, invitation.email, invitation.language, invitation=invitation):
                messages.success(request, _("We sent a confirmation code to the invited address."))
            else:
                messages.error(request, _("Please wait a little before asking for another code."))
            return redirect("invite_entry")
        if action == "accept":
            return _accept_new_user(request, invitation, form, context)
    context["form"] = form if request.method == "POST" else AcceptInviteForm(email=invitation.email, initial={"name": invitation.name})
    return render(request, "invites/invite.html", context)


def _accept_new_user(request, invitation, form, context):
    check_key = ratelimit.make_key("code_check_ip", ratelimit.client_ip(request))
    if ratelimit.exceeded(check_key, 30, HOUR):
        return _too_many(request)
    if form.is_valid():
        if codes.verify_code(form.cleaned_data["code"], invitation=invitation):
            try:
                user = services.accept_new_user(
                    invitation.pk, name=form.cleaned_data["name"], password=form.cleaned_data["password1"],
                    language=invitation.language,
                )
            except services.Unavailable:
                return _unavailable(request)
            request.session.pop(INVITE_SESSION_KEY, None)
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            messages.success(request, _("Your account is ready."))
            return redirect("home")
        ratelimit.hit(check_key)
        record(events.INVITATION_CODE_FAILED, target=f"invitation:{invitation.pk}", outcome=AuditEvent.Outcome.FAILED)
        form.add_error("code", _("The code is not valid or has expired. Ask for a new one."))
    context["form"] = form
    return render(request, "invites/invite.html", context)


def _existing_user_flow(request, invitation, existing, context):
    if not existing.is_active or existing.is_admin:
        return _unavailable(request)
    signed_in = request.user.is_authenticated and request.user.pk == existing.pk
    context.update({"existing": True, "signed_in": signed_in, "wrong_account": request.user.is_authenticated and not signed_in,
                    "login_url": reverse("login") + "?next=" + reverse("invite_entry")})
    if signed_in and request.method == "POST":
        try:
            services.accept_existing_user(invitation.pk, request.user)
        except services.Unavailable:
            return _unavailable(request)
        request.session.pop(INVITE_SESSION_KEY, None)
        messages.success(request, _("Access added to your account."))
        return redirect("home")
    return render(request, "invites/invite.html", context)


# --- Самостоятельная заявка -------------------------------------------------


def _self_registration_or_404(request):
    if not get_instance(request).self_registration_enabled:
        raise Http404


@never_cache
@require_http_methods(["GET", "POST"])
def request_access(request):
    _self_registration_or_404(request)
    if request.user.is_authenticated:
        return redirect("home")
    form = RequestAccessForm(request.POST or None, initial={"language": request.LANGUAGE_CODE})
    if request.method == "POST" and form.is_valid():
        ip_key = ratelimit.make_key("request_ip", ratelimit.client_ip(request))
        email_key = ratelimit.make_key("request_email", form.cleaned_data["email"])
        if not (ratelimit.allow(ip_key, 10, HOUR) and ratelimit.allow(email_key, 3, HOUR)):
            return _too_many(request)
        data = form.cleaned_data
        access_request, send = services.submit_access_request(
            email=data["email"], name=data["name"], password=data["password1"], language=data["language"],
            message=data["message"],
        )
        if access_request is not None and send:
            _send_code(request, "req", access_request.pk, access_request.email, access_request.language, access_request=access_request)
        # Ответ одинаков при любом исходе: не раскрываем, есть ли адрес в системе.
        request.session[REQUEST_SESSION_KEY] = access_request.pk if access_request is not None else 0
        return redirect("request_confirm")
    return render(request, "invites/request_access.html", {"form": form})


@never_cache
@require_http_methods(["GET", "POST"])
def request_confirm(request):
    _self_registration_or_404(request)
    if REQUEST_SESSION_KEY not in request.session:
        return redirect("request_access")
    request_id = request.session[REQUEST_SESSION_KEY]
    access_request = AccessRequest.objects.filter(pk=request_id, status=AccessRequest.Status.UNCONFIRMED).first()
    form = CodeForm(request.POST or None)
    if request.method == "POST":
        if request.POST.get("action") == "send_code":
            if access_request is not None:
                _send_code(request, "req", access_request.pk, access_request.email, access_request.language, access_request=access_request)
            messages.success(request, _("If the address can be confirmed, we sent a new code."))
            return redirect("request_confirm")
        check_key = ratelimit.make_key("code_check_ip", ratelimit.client_ip(request))
        if ratelimit.exceeded(check_key, 30, HOUR):
            return _too_many(request)
        if form.is_valid():
            if access_request is not None and codes.verify_code(form.cleaned_data["code"], access_request=access_request):
                services.confirm_request(access_request.pk)
                request.session.pop(REQUEST_SESSION_KEY, None)
                request.session[STATUS_SESSION_KEY] = access_request.pk
                return redirect("request_status")
            else:
                ratelimit.hit(check_key)
                if access_request is not None:
                    record(events.REQUEST_CODE_FAILED, target=f"request:{access_request.pk}", outcome=AuditEvent.Outcome.FAILED)
                form.add_error("code", _("The code is not valid or has expired. Ask for a new one."))
    return render(request, "invites/request_confirm.html", {"form": form})


@never_cache
@require_http_methods(["GET"])
def request_status(request):
    """Состояние своей заявки. Доступно только в той сессии, где адрес был подтверждён кодом."""
    _self_registration_or_404(request)
    access_request = AccessRequest.objects.filter(pk=request.session.get(STATUS_SESSION_KEY)).first()
    if access_request is None:
        return redirect("request_access")
    return render(request, "invites/request_status.html", {"status": access_request.status})
