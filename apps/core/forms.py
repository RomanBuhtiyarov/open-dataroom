from django import forms
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from . import logo as logo_service
from .models import InstanceSettings


class InstanceSettingsForm(forms.ModelForm):
    default_locale = forms.ChoiceField(label=_("Default language of this instance"), choices=settings.LANGUAGES)
    logo = forms.FileField(
        label=_("Logo"),
        required=False,
        help_text=_("PNG, JPEG or WebP, up to 512 KB."),
        widget=forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}),
    )
    remove_logo = forms.BooleanField(label=_("Remove the current logo"), required=False)

    class Meta:
        model = InstanceSettings
        fields = ["site_name", "default_locale", "room_template", "self_registration_enabled"]
        labels = {
            "site_name": _("Name of the data room"),
            "room_template": _("Room template"),
            "self_registration_enabled": _("Allow people to request access without an invitation"),
        }
        help_texts = {
            "self_registration_enabled": _("Every request still needs approval by an administrator."),
            "room_template": _("Used when the first room is created."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["site_name"].required = True
        self.fields["site_name"].widget.attrs.update({"autocomplete": "organization"})
        self.fields["default_locale"].initial = self.instance.effective_locale
        self._new_logo = None
        if not self.instance.logo_file:
            del self.fields["remove_logo"]

    def clean_site_name(self):
        return " ".join(self.cleaned_data["site_name"].split())

    def clean_logo(self):
        upload = self.cleaned_data.get("logo")
        if upload:
            self._new_logo = logo_service.process_logo(upload)
        return upload

    def save(self, commit=True, complete_setup=False):
        instance = super().save(commit=False)
        old_logo = InstanceSettings.objects.filter(pk=1).values_list("logo_file", flat=True).first() or ""
        if complete_setup:
            instance.setup_completed_at = timezone.now()
        if self._new_logo:
            filename, payload = self._new_logo
            logo_service.store_logo(filename, payload)
            instance.logo_file = filename
        elif self.cleaned_data.get("remove_logo"):
            instance.logo_file = ""
        if commit:
            instance.save()
            if old_logo and old_logo != instance.logo_file:
                logo_service.delete_logo(old_logo)
        return instance
