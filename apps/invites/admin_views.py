"""Экраны администратора: приглашения и очередь заявок."""
from django.conf import settings
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.core import ratelimit
from apps.core.decorators import admin_required
from apps.core.qr import qr_svg

from . import services
from .forms import ApproveForm, InvitationForm
from .models import AccessRequest, Invitation

HOUR = 3600


def _link(token):
    return f"{settings.PUBLIC_BASE_URL}{reverse('invite_entry')}?t={token}"


def _created_page(request, invitation, token, send_email):
    """Показывает ссылку и QR один раз: открытый токен больше нигде не хранится."""
    link = _link(token)
    return render(request, "invites/invitation_created.html", {
        "invitation": invitation, "link": link, "qr": qr_svg(link), "send_email": send_email,
    })


@admin_required
@never_cache
def invitation_list(request):
    now = None
    rows = [{"inv": inv, "status": inv.status(now)} for inv in Invitation.objects.select_related("room", "folder")[:200]]
    return render(request, "invites/invitation_list.html", {"rows": rows})


@admin_required
@never_cache
def invitation_create(request):
    form = InvitationForm(request.POST or None, initial={"language": request.LANGUAGE_CODE})
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        if data["send_email"] and not ratelimit.allow(ratelimit.make_key("invite_mail_admin", request.user.pk), 60, HOUR):
            form.add_error(None, _("Too many emails were sent recently. Try again later or share the link yourself."))
        else:
            invitation, token = services.create_invitation(
                email=data["email"], name=data["name"], language=data["language"], room=data["room"],
                folder=data["folder"], can_download=data["can_download"], access_expires_at=data["expires_on"],
                valid_days=data["valid_days"], created_by=request.user, send_email=data["send_email"],
            )
            return _created_page(request, invitation, token, data["send_email"])
    return render(request, "invites/invitation_form.html", {"form": form})


@admin_required
@require_POST
def invitation_resend(request, invitation_id):
    invitation = get_object_or_404(Invitation, pk=invitation_id)
    if not ratelimit.allow(ratelimit.make_key("invite_mail_admin", request.user.pk), 60, HOUR):
        messages.error(request, _("Too many emails were sent recently. Try again later or share the link yourself."))
        return redirect("invitation_list")
    try:
        invitation, token = services.rotate_token(invitation.pk, request.user)
    except services.Unavailable:
        messages.error(request, _("This invitation is already used or revoked."))
        return redirect("invitation_list")
    return _created_page(request, invitation, token, True)


@admin_required
@require_POST
def invitation_revoke(request, invitation_id):
    get_object_or_404(Invitation, pk=invitation_id)
    if services.revoke_invitation(invitation_id, request.user):
        messages.success(request, _("The invitation is revoked."))
    return redirect("invitation_list")


# --- Заявки -----------------------------------------------------------------


@admin_required
@never_cache
def request_list(request):
    pending = AccessRequest.objects.filter(status=AccessRequest.Status.PENDING)
    decided = AccessRequest.objects.filter(status__in=[AccessRequest.Status.APPROVED, AccessRequest.Status.REJECTED])[:50]
    return render(request, "invites/request_list.html", {"pending": pending, "decided": decided})


@admin_required
@never_cache
def request_detail(request, request_id):
    access_request = get_object_or_404(AccessRequest, pk=request_id)
    if access_request.status in (AccessRequest.Status.UNCONFIRMED,):
        return redirect("request_list")  # адрес ещё не подтверждён: решать рано
    pending = access_request.status == AccessRequest.Status.PENDING
    form = ApproveForm(request.POST or None) if pending else None
    if pending and request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "reject":
                services.reject_request(access_request.pk, request.user)
                messages.success(request, _("The request is rejected."))
                return redirect("request_list")
            if action == "approve" and form.is_valid():
                data = form.cleaned_data
                services.approve_request(
                    access_request.pk, request.user, room=data["room"], folder=data["folder"],
                    can_download=data["can_download"], access_expires_at=data["expires_on"],
                )
                messages.success(request, _("The request is approved."))
                return redirect("request_list")
        except services.Unavailable:
            messages.error(request, _("This request has already been decided."))
            return redirect("request_list")
    return render(request, "invites/request_detail.html", {"req": access_request, "form": form, "pending": pending})


@admin_required
@require_POST
def request_retry_email(request, request_id):
    try:
        services.retry_decision_email(request_id)
        messages.success(request, _("The notification is queued again."))
    except (services.Unavailable, AccessRequest.DoesNotExist):
        messages.error(request, _("Nothing to resend."))
    return redirect("request_list")
