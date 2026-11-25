"""Выдаёт строку-якорь: номер и подпись последней записи журнала. Храните её вне сервера."""
from django.core.management.base import BaseCommand, CommandError

from apps.audit import chain, events
from apps.audit.service import record


class Command(BaseCommand):
    help = "Print an anchor line for the audit log. Keep it off the server (mail, ticket, paper)."

    def handle(self, *args, **options):
        line = chain.anchor_line()
        if not line:
            raise CommandError("The audit log is empty.")
        record(events.AUDIT_ANCHOR_ISSUED, target=f"seq:{line.split()[2]}")
        self.stdout.write(line)
