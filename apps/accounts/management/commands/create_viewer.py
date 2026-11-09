"""Создание тестового посетителя для локальной разработки (до появления приглашений на этапе 3).

Пароль запрашивается интерактивно. Не используйте эту команду как способ выдачи доступа в продакшене:
там посетители приходят по приглашениям.
"""
from getpass import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.utils import timezone

from apps.accounts.models import User, normalize_email


class Command(BaseCommand):
    help = "Create a viewer account (development helper; invitations arrive in milestone 3)."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--name", default="")

    def handle(self, *args, **options):
        email = normalize_email(options["email"])
        try:
            validate_email(email)
        except ValidationError:
            raise CommandError("Enter a valid email address.")
        if User.objects.filter(email=email).exists():
            raise CommandError("A user with this email already exists.")
        try:
            password = getpass("Password: ")
            validate_password(password, user=User(email=email, name=options["name"]))
            if password != getpass("Password (again): "):
                raise CommandError("The passwords do not match.")
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages))
        except (EOFError, KeyboardInterrupt):
            raise CommandError("Input aborted. Nothing was created.")
        User.objects.create_user(email, password, name=options["name"], email_confirmed_at=timezone.now())
        self.stdout.write(self.style.SUCCESS("Viewer created."))
