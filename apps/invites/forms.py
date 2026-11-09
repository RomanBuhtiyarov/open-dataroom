import datetime

from django import forms
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User, normalize_email
from apps.rooms.models import Folder, Room


def _folder_label(folder):
    names = []
    node = folder
    while node is not None:
        names.append(node.name)
        node = node.parent
    return f"{folder.room.name} / " + " / ".join(reversed(names))


def _end_of_day(day):
    if day is None:
        return None
    end = datetime.datetime.combine(day, datetime.time(23, 59, 59), tzinfo=datetime.timezone.utc)
    if end <= timezone.now():
        raise forms.ValidationError(_("The end date must be in the future."))
    return end


class AccessScopeMixin:
    """Поля «комната / папка / скачивание / срок», общие для приглашения и одобрения заявки."""

    def clean(self):
        data = super().clean()
        room, folder = data.get("room"), data.get("folder")
        if folder is not None and room is not None and folder.room_id != room.id:
            self.add_error("folder", _("The folder does not belong to this room."))
        if folder is not None and room is None:
            self.add_error("room", _("Choose the room of this folder."))
        return data

    def clean_expires_on(self):
        return _end_of_day(self.cleaned_data.get("expires_on"))


class InvitationForm(AccessScopeMixin, forms.Form):
    email = forms.EmailField(label=_("Email"))
    name = forms.CharField(label=_("Name"), max_length=150, required=False)
    language = forms.ChoiceField(label=_("Language of the invitation"), choices=settings.LANGUAGES)
    room = forms.ModelChoiceField(label=_("Room"), queryset=Room.objects.all())
    folder = forms.ModelChoiceField(label=_("Access to"), queryset=Folder.objects.none(), required=False, empty_label=_("The whole room"))
    can_download = forms.BooleanField(label=_("Can download originals"), required=False)
    expires_on = forms.DateField(
        label=_("Access ends on"), required=False, widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_("Optional. Access ends at the end of this day (UTC)."),
    )
    valid_days = forms.IntegerField(
        label=_("The invitation is valid for (days)"), min_value=1, max_value=settings.INVITATION_MAX_DAYS,
        initial=settings.INVITATION_DEFAULT_DAYS,
    )
    send_email = forms.BooleanField(label=_("Send the invitation by email"), required=False, initial=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        folders = Folder.objects.select_related("room", "parent")
        self.fields["folder"].queryset = folders
        self.fields["folder"].label_from_instance = _folder_label

    def clean_email(self):
        return normalize_email(self.cleaned_data["email"])


class PasswordPairMixin:
    """Пароль и подтверждение; пароль проверяется общими валидаторами проекта."""

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2:
            if p1 != p2:
                self.add_error("password2", _("The passwords do not match."))
            else:
                try:
                    validate_password(p1, user=User(email=getattr(self, "email_for_validation", ""), name=data.get("name", "")))
                except ValidationError as exc:
                    self.add_error("password1", exc)
        return data


def _password_fields():
    return (
        forms.CharField(label=_("Password"), strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"})),
        forms.CharField(label=_("Repeat the password"), strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"})),
    )


class AcceptInviteForm(PasswordPairMixin, forms.Form):
    name = forms.CharField(label=_("Name"), max_length=150, widget=forms.TextInput(attrs={"autocomplete": "name"}))
    password1, password2 = _password_fields()
    code = forms.CharField(
        label=_("Confirmation code from the email"), max_length=12,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code"}),
    )

    def __init__(self, *args, email="", **kwargs):
        super().__init__(*args, **kwargs)
        self.email_for_validation = email


class RequestAccessForm(PasswordPairMixin, forms.Form):
    email = forms.EmailField(label=_("Email"), widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    name = forms.CharField(label=_("Name"), max_length=150, widget=forms.TextInput(attrs={"autocomplete": "name"}))
    language = forms.ChoiceField(label=_("Language of emails"), choices=settings.LANGUAGES)
    message = forms.CharField(
        label=_("Why do you need access?"), required=False, max_length=500, widget=forms.Textarea(attrs={"rows": 3}),
    )
    password1, password2 = _password_fields()

    def clean_email(self):
        return normalize_email(self.cleaned_data["email"])

    def clean(self):
        self.email_for_validation = self.cleaned_data.get("email", "")
        return super().clean()


class CodeForm(forms.Form):
    code = forms.CharField(
        label=_("Confirmation code from the email"), max_length=12,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "autofocus": True}),
    )


class ApproveForm(AccessScopeMixin, forms.Form):
    room = forms.ModelChoiceField(label=_("Room"), queryset=Room.objects.all(), required=False, empty_label=_("No access yet"))
    folder = forms.ModelChoiceField(label=_("Access to"), queryset=Folder.objects.none(), required=False, empty_label=_("The whole room"))
    can_download = forms.BooleanField(label=_("Can download originals"), required=False)
    expires_on = forms.DateField(label=_("Access ends on"), required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["folder"].queryset = Folder.objects.select_related("room", "parent")
        self.fields["folder"].label_from_instance = _folder_label
