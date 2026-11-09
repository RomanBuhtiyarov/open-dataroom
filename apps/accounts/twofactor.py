"""Второй фактор (TOTP) для администраторов: подключение устройства и подтверждение входа."""
import base64
from urllib.parse import quote

import segno
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.audit import events
from apps.audit.models import AuditEvent
from apps.audit.service import record
from apps.core.instance import get_instance


def _safe_next(request):
    target = request.POST.get("next") or request.GET.get("next") or "/"
    ok = url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    return target if ok else "/"


def _otpauth_url(device, issuer, account):
    issuer = issuer.replace(":", "")
    secret = base64.b32encode(device.bin_key).decode()
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"


def _qr_svg(url):
    svg = segno.make(url, error="m").svg_inline(scale=5, border=2, dark="#000000", light="#ffffff")
    return mark_safe(svg)  # noqa: S308 - SVG собран библиотекой из наших данных


@login_required
@never_cache
def two_factor(request):
    user = request.user
    next_url = _safe_next(request)
    if not user.is_admin or user.is_verified():
        return redirect(next_url)

    confirmed = TOTPDevice.objects.filter(user=user, confirmed=True).first()
    enrolling = confirmed is None
    device = confirmed or TOTPDevice.objects.filter(user=user, confirmed=False).first()
    if device is None:
        device = TOTPDevice.objects.create(user=user, name="default", confirmed=False)

    error = ""
    if request.method == "POST":
        code = "".join(request.POST.get("code", "").split())
        allowed, _data = device.verify_is_allowed()
        if not allowed:
            error = _("Too many wrong codes. Wait a little and try again.")
        elif code.isdigit() and len(code) == 6 and device.verify_token(code):
            if enrolling:
                device.confirmed = True
                device.save(update_fields=["confirmed"])
            otp_login(request, device)
            record(events.AUTH_2FA_ENROLLED if enrolling else events.AUTH_2FA_VERIFIED, actor=user, target=f"user:{user.pk}")
            return redirect(next_url)
        else:
            error = error or _("The code is not valid. Check the time on your phone and try again.")
            record(events.AUTH_2FA_FAILED, actor=user, target=f"user:{user.pk}", outcome=AuditEvent.Outcome.FAILED)

    context = {"enrolling": enrolling, "error": error, "next": next_url}
    if enrolling:
        issuer = get_instance(request).effective_site_name
        context["qr"] = _qr_svg(_otpauth_url(device, issuer, user.email))
        secret = base64.b32encode(device.bin_key).decode()
        context["secret"] = " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))
    return render(request, "accounts/two_factor.html", context)
