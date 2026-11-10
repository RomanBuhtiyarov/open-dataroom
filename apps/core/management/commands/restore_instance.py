"""Восстановление из резервной копии. Архив проверяется целиком до любых изменений."""
from django.core.management.base import BaseCommand, CommandError

from apps.audit import events
from apps.audit.service import record
from apps.core import backup


class Command(BaseCommand):
    help = "Restore the database and documents from a backup archive."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--replace", action="store_true", help="Overwrite an instance that already has data.")

    def handle(self, *args, **options):
        try:
            manifest = backup.restore_backup(options["path"], replace=options["replace"])
        except backup.BackupError as exc:
            raise CommandError(str(exc))
        record(events.BACKUP_RESTORED)
        self.stdout.write(f"Restored {len(manifest['files'])} files and the database. Run migrate if the program is newer.")
