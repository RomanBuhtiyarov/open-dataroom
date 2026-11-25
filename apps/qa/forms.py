from django import forms
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User

from .models import MAX_TEXT, Question


class AskForm(forms.Form):
    text = forms.CharField(
        label=_("Your question"), max_length=MAX_TEXT, widget=forms.Textarea(attrs={"rows": 6}),
        help_text=_("If the administrator shares the answer, other visitors with access will see your question and the answer, without your name."),
    )


class AnswerForm(forms.Form):
    text = forms.CharField(label=_("Answer"), max_length=MAX_TEXT, widget=forms.Textarea(attrs={"rows": 6}))
    file = forms.FileField(label=_("Attachment"), required=False, help_text=_("PDF, DOCX or XLSX. A new file replaces the old one."))
    share = forms.BooleanField(label=_("Share with other visitors who can see this area (your name stays hidden)"), required=False)


class AssignForm(forms.Form):
    assignee = forms.ModelChoiceField(label=_("Assigned to"), queryset=User.objects.none(), required=False)
    due_date = forms.DateField(label=_("Answer by"), required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignee"].queryset = User.objects.filter(role=User.Role.ADMIN, status=User.Status.ACTIVE).order_by("email")
        self.fields["assignee"].label_from_instance = lambda u: u.name or u.email


class InboxFilterForm(forms.Form):
    status = forms.ChoiceField(label=_("Status"), required=False, choices=[("", _("All"))] + list(Question.Status.choices) + [("overdue", _("Overdue"))])
    assignee = forms.ChoiceField(label=_("Assigned to"), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        admins = User.objects.filter(role=User.Role.ADMIN, status=User.Status.ACTIVE).order_by("email")
        self.fields["assignee"].choices = [("", _("Anyone")), ("none", _("Unassigned"))] + [(str(u.pk), u.name or u.email) for u in admins]
