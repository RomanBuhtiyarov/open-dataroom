"""Создание первого администратора. Выполняется один раз, второй запуск отказывает.

Пароль запрашивается только интерактивно (getpass): он не передаётся аргументом, переменной
окружения или файлом и не попадает в журналы.
"""
from getpass import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User, normalize_email
from apps.audit import events
from apps.audit.service import record
from apps.core.models import InstanceSettings

ALREADY_DONE = "An administrator already exists. bootstrap_admin can run only once."


def already_bootstrapped():
    inst = InstanceSettings.objects.filter(pk=1).first()
    return bool(inst and inst.bootstrapped_at) or User.objects.filter(role=User.Role.ADMIN).exists()


class Command(BaseCommand):
    help = "Create the first administrator of this instance (interactive, runs once)."

    def add_arguments(self, parser):
        parser.add_argument("--email", help="Administrator email (asked if omitted).")
        parser.add_argument("--name", help="Administrator name (asked if omitted).")

    def handle(self, *args, **options):
        # Ранний отказ, чтобы не заставлять вводить пароль зря. Окончательная проверка ниже, под блокировкой.
        if already_bootstrapped():
            raise CommandError(ALREADY_DONE)
        try:
            email = self._ask_email(options.get("email"))
            name = (options.get("name") or input("Name (optional): ")).strip()
            password = self._ask_password(email, name)
        except (EOFError, KeyboardInterrupt):
            raise CommandError("Input aborted. Nothing was created.")

        with transaction.atomic():
            InstanceSettings.objects.get_or_create(pk=1)
            # Блокировка строки настроек: параллельный запуск дождётся и увидит результат этого.
            inst = InstanceSettings.objects.select_for_update().get(pk=1)
            if inst.bootstrapped_at or User.objects.filter(role=User.Role.ADMIN).exists():
                raise CommandError(ALREADY_DONE)
            user = User.objects.create_user(
                email, password, name=name, role=User.Role.ADMIN, email_confirmed_at=timezone.now()
            )
            inst.bootstrapped_at = timezone.now()
            inst.save(update_fields=["bootstrapped_at", "updated_at"])
            record(events.ADMIN_BOOTSTRAPPED, actor=user, target=f"user:{user.pk}")

        self.stdout.write(self.style.SUCCESS("Administrator created."))
        self.stdout.write("Sign in, then set up two-factor authentication and the instance profile.")

    def _ask_email(self, given):
        while True:
            email = normalize_email(given or input("Email: "))
            try:
                validate_email(email)
            except ValidationError:
                self.stderr.write("Enter a valid email address.")
                given = None
                continue
            return email

    def _ask_password(self, email, name):
        candidate = User(email=email, name=name)
        while True:
            password = getpass("Password: ")
            try:
                validate_password(password, user=candidate)
            except ValidationError as exc:
                for message in exc.messages:
                    self.stderr.write(message)
                continue
            if password != getpass("Password (again): "):
                self.stderr.write("The passwords do not match.")
                continue
            return password
