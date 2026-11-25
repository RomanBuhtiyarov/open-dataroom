"""Проверка цепочки подписей журнала аудита."""
from django.core.management.base import BaseCommand, CommandError

from apps.audit import chain, events
from apps.audit.models import AuditEvent
from apps.audit.service import record

REASONS = {
    "mismatch": "the entry was changed (or the key is not the one that signed it)",
    "gap": "an entry is missing before this number",
    "unsealed": "an entry was added without a signature",
    "head": "the end of the log does not match the recorded head (entries removed from the end?)",
    "anchor_mismatch": "the entry differs from the anchor you supplied",
    "anchor_missing": "the entry named in the anchor is gone (the end of the log was removed)",
}


class Command(BaseCommand):
    help = "Check the audit log hash chain. Optionally compare it with an anchor line issued earlier."

    def add_arguments(self, parser):
        parser.add_argument("--anchor", help='Anchor line, for example "AUDIT-ANCHOR v1 120 <hex> 2026-11-25".')
        parser.add_argument("--anchor-file", help="File whose first line is an anchor.")

    def handle(self, *args, **options):
        text = options["anchor"]
        if options["anchor_file"]:
            try:
                with open(options["anchor_file"], encoding="utf-8") as handle:
                    text = handle.readline()
            except OSError as exc:
                raise CommandError(f"Cannot read the anchor file: {exc}")
        anchor = None
        if text:
            anchor = chain.parse_anchor(text)
            if anchor is None:
                raise CommandError("The anchor line is not valid.")
        result = chain.verify(anchor)
        if result.ok:
            record(events.AUDIT_VERIFIED, target=f"seq:{result.last_seq}")
            note = f" {result.legacy} of them were sealed at upgrade (not provable before that)." if result.legacy else ""
            self.stdout.write(f"OK: {result.checked} entries, chain intact up to #{result.last_seq}.{note}")
            return
        record(events.AUDIT_VERIFIED, target=f"broken:{result.broken_at}:{result.reason}", outcome=AuditEvent.Outcome.FAILED)
        raise CommandError(f"BROKEN at #{result.broken_at}: {REASONS.get(result.reason, result.reason)}.")
