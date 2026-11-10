"""Сброс пароля по одноразовой ссылке.

- Токен подписан и привязан к текущему хэшу пароля и времени последнего входа: после смены пароля
  или входа ссылка перестаёт работать. Срок — PASSWORD_RESET_TIMEOUT.
- Ссылка несёт токен в параметре `t` (его вырезают журналы). Открытие ссылки ничего не расходует:
  токен переносится в сессию, адрес очищается. Пароль меняет только POST.
- Ответ на запрос одинаков для известного и неизвестного адреса; лимиты считают каждый запрос.
- Второй фактор администратора сброс не затрагивает.
"""
from django import forms
from django.contrib import messages
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_http_methods

from apps.audit import events
from apps.audit.service import record
from apps.core import ratelimit
from apps.invites import mail

from .models import User, normalize_email

SESSION_KEY = "password_reset_token"
HOUR = 3600


class RequestForm(forms.Form):
    email = forms.EmailField(label=gettext_lazy("Email"), widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}))


class NewPasswordForm(forms.Form):
    password1 = forms.CharField(label=gettext_lazy("New password"), strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label=gettext_lazy("Repeat the password"), strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", _("The passwords do not match."))
        elif p1:
            try:
                validate_password(p1, self.user)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return data


def make_token(user):
    return f"{urlsafe_base64_encode(str(user.pk).encode())}.{default_token_generator.make_token(user)}"


def user_for_token(value):
    """Возвращает пользователя, если токен верен, не просрочен и учётная запись активна."""
    try:
        uid_part, token = value.split(".", 1)
        user = User.objects.get(pk=int(urlsafe_base64_decode(uid_part).decode()))
    except (ValueError, TypeError, UnicodeDecodeError, User.DoesNotExist, OverflowError):
        return None
    if user.is_active and default_token_generator.check_token(user, token):
        return user
    return None


@require_http_methods(["GET", "POST"])
def password_reset(request):
    form = RequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = normalize_email(form.cleaned_data["email"])
        ip_ok = ratelimit.allow(ratelimit.make_key("reset_ip", ratelimit.client_ip(request)), 10, HOUR)
        email_ok = ratelimit.allow(ratelimit.make_key("reset_email", email), 3, HOUR)
        user = User.objects.filter(email=email).first()
        if ip_ok and email_ok and user is not None and user.is_active:
            record(events.AUTH_PASSWORD_RESET_REQUESTED, target=f"user:{user.pk}")
            link = mail.absolute(reverse("password_reset_confirm"), t=make_token(user))
            mail.queue_password_reset_email(user.email, link, user.language)
        return redirect("password_reset_done")  # тот же ответ в любом случае
    return render(request, "accounts/password_reset.html", {"form": form})


def password_reset_done(request):
    return render(request, "accounts/password_reset_done.html")


def _invalid(request):
    return render(request, "accounts/password_reset_invalid.html", status=404)


@require_http_methods(["GET", "POST"])
def password_reset_confirm(request):
    ip_key = ratelimit.make_key("reset_confirm_ip", ratelimit.client_ip(request))
    token = request.GET.get("t")
    if token is not None:
        if ratelimit.exceeded(ip_key, 20, 600):
            return render(request, "invites/too_many.html", status=429)
        if user_for_token(token) is None:
            ratelimit.hit(ip_key)
            return _invalid(request)
        request.session[SESSION_KEY] = token
        return redirect("password_reset_confirm")  # токен уходит из адресной строки

    stored = request.session.get(SESSION_KEY, "")
    user = user_for_token(stored)
    if user is None:
        request.session.pop(SESSION_KEY, None)
        return _invalid(request)
    form = NewPasswordForm(request.POST or None, user=user)
    if request.method == "POST":
        if ratelimit.exceeded(ip_key, 20, 600):
            return render(request, "invites/too_many.html", status=429)
        if form.is_valid():
            with transaction.atomic():
                locked = User.objects.select_for_update().get(pk=user.pk)
                # Повторная проверка под блокировкой: две вкладки не применят один токен дважды.
                if not default_token_generator.check_token(locked, stored.split(".", 1)[1]):
                    request.session.pop(SESSION_KEY, None)
                    return _invalid(request)
                locked.set_password(form.cleaned_data["password1"])
                locked.save(update_fields=["password"])
                record(events.AUTH_PASSWORD_RESET_COMPLETED, actor=locked, target=f"user:{locked.pk}")
            request.session.pop(SESSION_KEY, None)
            messages.success(request, _("Your password was changed. Sign in with the new password."))
            return redirect("login")
    return render(request, "accounts/password_reset_confirm.html", {"form": form})
