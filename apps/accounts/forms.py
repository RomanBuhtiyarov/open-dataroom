from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.utils.translation import gettext_lazy as _

from .models import User, normalize_email


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(
        label=_("Email"),
        widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}),
    )
    password = forms.CharField(
        label=_("Password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    def clean_username(self):
        return normalize_email(self.cleaned_data["username"])


class ProfileForm(forms.ModelForm):
    """Профиль пользователя: имя и предпочитаемый язык. Роль и статус здесь изменить нельзя."""

    language = forms.ChoiceField(
        label=_("Interface language"),
        required=False,
        choices=[("", _("Use the instance language"))] + list(settings.LANGUAGES),
    )

    class Meta:
        model = User
        fields = ["name", "language"]
        labels = {"name": _("Name")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.update({"autocomplete": "name"})
