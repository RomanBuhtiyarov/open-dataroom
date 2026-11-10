from django import forms
from django.utils.translation import gettext_lazy as _

from . import labels
from .models import AuditEvent


class AuditFilterForm(forms.Form):
    """Фильтры журнала. Время в UTC; даты включительно."""

    code = forms.ChoiceField(label=_("Event"), required=False)
    outcome = forms.ChoiceField(label=_("Outcome"), required=False)
    actor = forms.CharField(label=_("Person (part of the email)"), required=False, max_length=100)
    date_from = forms.DateField(label=_("From (UTC)"), required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(label=_("To (UTC)"), required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code"].choices = [("", _("All events"))] + [(c, labels.label(c)) for c in labels.all_codes()]
        self.fields["outcome"].choices = [("", _("Any outcome"))] + [(o, labels.outcome_label(o)) for o, _name in AuditEvent.Outcome.choices]
