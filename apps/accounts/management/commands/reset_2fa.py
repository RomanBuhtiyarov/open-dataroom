"""Сброс второго фактора администратора при потере устройства. Доступен только с доступом к серверу."""
from django.core.management.base import BaseCommand, CommandError
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import User, normalize_email
from apps.audit import events
from apps.audit.service import record


class Command(BaseCommand):
    help = "Remove the TOTP devices of a user. At the next sign-in the user enrols a new device."

    def add_arguments(self, parser):
        parser.add_argument("email")

    def handle(self, *args, **options):
        try:
            user = User.objects.get(email=normalize_email(options["email"]))
        except User.DoesNotExist:
            raise CommandError("No user with this email.")
        removed, _ = TOTPDevice.objects.filter(user=user).delete()
        record(events.AUTH_2FA_RESET, target=f"user:{user.pk}")
        self.stdout.write(f"Removed {removed} device(s). The user must enrol a new one at the next sign-in.")
