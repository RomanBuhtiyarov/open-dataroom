"""Резервная копия: база и документы в одном архиве."""
from django.core.management.base import BaseCommand, CommandError

from apps.audit import events
from apps.audit.service import record
from apps.core import backup


class Command(BaseCommand):
    help = "Write a backup archive (database and documents together). Keep it encrypted and off the server."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Archive file to create (must not exist).")

    def handle(self, *args, **options):
        try:
            manifest = backup.create_backup(options["path"])
        except backup.BackupError as exc:
            raise CommandError(str(exc))
        record(events.BACKUP_CREATED)
        self.stdout.write(f"Backup written: {options['path']} ({len(manifest['files'])} files).")
