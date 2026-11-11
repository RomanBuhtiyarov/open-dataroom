import datetime

from django import forms
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.core.models import InstanceSettings

from .models import Folder, Room


class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = ["name", "description", "nda_required", "watermark_enabled"]
        labels = {
            "name": _("Room name"),
            "description": _("Description"),
            "nda_required": _("Require visitors to accept an NDA first"),
            "watermark_enabled": _("Watermark on document pages"),
        }
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}
        help_texts = {
            "nda_required": _("Publish the NDA text on the NDA page of the room."),
            "watermark_enabled": _("Shows the visitor's email, time and address on every page. It deters leaks but does not stop photos of the screen."),
        }


class RoomCreateForm(RoomForm):
    template = forms.ChoiceField(label=_("Room template"), choices=InstanceSettings.RoomTemplate.choices)

    class Meta(RoomForm.Meta):
        fields = ["name", "description", "nda_required", "watermark_enabled"]


class FolderNameForm(forms.Form):
    name = forms.CharField(label=_("Folder name"), max_length=120)


class UploadForm(forms.Form):
    folder = forms.ModelChoiceField(label=_("Folder"), queryset=Folder.objects.none())
    title = forms.CharField(label=_("Title"), max_length=200, required=False, help_text=_("Leave empty to use the file name."))
    file = forms.FileField(label=_("File"), help_text=_("PDF, DOCX or XLSX."))

    def __init__(self, room, *args, **kwargs):
        super().__init__(*args, **kwargs)
        folders = Folder.objects.filter(room=room).select_related("parent")
        self.fields["folder"].queryset = folders
        self.fields["folder"].label_from_instance = lambda f: " / ".join(reversed(_path_names(f)))


def _path_names(folder):
    names = []
    while folder is not None:
        names.append(folder.name)
        folder = folder.parent
    return names


class VersionForm(forms.Form):
    file = forms.FileField(label=_("New version"), help_text=_("PDF, DOCX or XLSX."))


class GrantForm(forms.Form):
    user = forms.ModelChoiceField(label=_("Person"), queryset=User.objects.none())
    folder = forms.ModelChoiceField(label=_("Access to"), queryset=Folder.objects.none(), required=False, empty_label=_("The whole room"))
    can_download = forms.BooleanField(label=_("Can download originals"), required=False)
    expires_on = forms.DateField(
        label=_("Access ends on"), required=False, widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_("Optional. Access ends at the end of this day (UTC)."),
    )

    def __init__(self, room, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = User.objects.filter(role=User.Role.VIEWER, status=User.Status.ACTIVE).order_by("email")
        self.fields["user"].label_from_instance = lambda u: f"{u.name} <{u.email}>" if u.name else u.email
        self.fields["folder"].queryset = Folder.objects.filter(room=room).select_related("parent")
        self.fields["folder"].label_from_instance = lambda f: " / ".join(reversed(_path_names(f)))

    def clean_expires_on(self):
        day = self.cleaned_data.get("expires_on")
        if day is None:
            return None
        end = datetime.datetime.combine(day, datetime.time(23, 59, 59), tzinfo=datetime.timezone.utc)
        if end <= timezone.now():
            raise forms.ValidationError(_("The end date must be in the future."))
        return end


class NdaForm(forms.Form):
    """Тексты NDA по языкам. Обязателен текст на языке экземпляра."""

    def __init__(self, *args, default_language="en", **kwargs):
        super().__init__(*args, **kwargs)
        self.default_language = default_language
        for code, name in settings.LANGUAGES:
            self.fields[f"body_{code}"] = forms.CharField(
                label=name, required=False, max_length=20000, widget=forms.Textarea(attrs={"rows": 8, "lang": code}),
            )

    def clean(self):
        data = super().clean()
        if not (data.get(f"body_{self.default_language}") or "").strip():
            self.add_error(f"body_{self.default_language}", _("The text in the default language of the instance is required."))
        return data

    def texts(self):
        return {code: (self.cleaned_data.get(f"body_{code}") or "").strip() for code, _name in settings.LANGUAGES}


class NdaAcceptForm(forms.Form):
    accept = forms.BooleanField(label=_("I have read the NDA and accept it"), required=True)
