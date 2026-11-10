"""События входа и выхода. Адрес почты и пароль при неудачном входе не записываются."""
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from . import events
from .models import AuditEvent
from .service import record


@receiver(user_logged_in)
def on_signed_in(sender, request, user, **kwargs):
    record(events.AUTH_SIGNED_IN, actor=user, target=f"user:{user.pk}")


@receiver(user_logged_out)
def on_signed_out(sender, request, user, **kwargs):
    if user is not None and getattr(user, "pk", None):
        record(events.AUTH_SIGNED_OUT, actor=user, target=f"user:{user.pk}")


@receiver(user_login_failed)
def on_sign_in_failed(sender, credentials=None, request=None, **kwargs):
    record(events.AUTH_SIGN_IN_FAILED, outcome=AuditEvent.Outcome.FAILED)
