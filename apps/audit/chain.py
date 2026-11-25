"""Цепочка подписей журнала аудита (этап 14).

Каждая запись получает номер `seq` и подпись HMAC-SHA256 от своих полей и подписи предыдущей записи.
Ключ берётся из окружения (AUDIT_CHAIN_KEY); без него используется ключ, выведенный из SECRET_KEY.
Правка, вставка или удаление записи в середине ломает цепочку. Удаление хвоста видно по «голове»
цепочки и по внешнему якорю (anchor): его администратор хранит вне сервера.
"""
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import timezone as dt_timezone

from django.conf import settings

GENESIS = "0" * 64
VERSION = "v1"
ANCHOR_RE = re.compile(r"^AUDIT-ANCHOR v1 (\d+) ([0-9a-f]{64})(?: \d{4}-\d{2}-\d{2})?$")


def key_bytes():
    configured = getattr(settings, "AUDIT_CHAIN_KEY", "") or ""
    if configured:
        return configured.encode("utf-8")
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), b"dataroom-audit-chain", hashlib.sha256).digest()


def compute_mac(*, seq, created_at, actor_ref, code, target, outcome, request_id, legacy, prev_mac, key=None):
    """Подпись записи. Время приводится к UTC с микросекундами, чтобы после БД оно совпало."""
    stamp = created_at.astimezone(dt_timezone.utc).isoformat(timespec="microseconds")
    material = json.dumps(
        [VERSION, seq, stamp, actor_ref, code, target, outcome, request_id, bool(legacy), prev_mac],
        separators=(",", ":"), ensure_ascii=True,
    )
    return hmac.new(key or key_bytes(), material.encode("ascii"), hashlib.sha256).hexdigest()


def mac_of(event, prev_mac, key=None):
    return compute_mac(seq=event.seq, created_at=event.created_at, actor_ref=event.actor_ref, code=event.code,
                       target=event.target, outcome=event.outcome, request_id=event.request_id,
                       legacy=event.legacy, prev_mac=prev_mac, key=key)


def _head():
    from .models import AuditChainHead

    AuditChainHead.objects.get_or_create(pk=1)
    return AuditChainHead.objects.select_for_update().get(pk=1)


def seal(event):
    """Вызывается внутри транзакции перед сохранением новой записи."""
    head = _head()
    event.seq = head.last_seq + 1
    event.actor_ref = event.actor_id
    event.chain_mac = mac_of(event, head.last_mac or GENESIS)


def advance(event):
    from .models import AuditChainHead

    AuditChainHead.objects.filter(pk=1).update(last_seq=event.seq, last_mac=event.chain_mac)


@dataclass
class Result:
    ok: bool
    checked: int = 0
    broken_at: int | None = None
    reason: str = ""
    legacy: int = 0
    last_seq: int = 0
    last_mac: str = ""


def parse_anchor(text):
    match = ANCHOR_RE.match((text or "").strip())
    return (int(match.group(1)), match.group(2)) if match else None


def current_head():
    from .models import AuditChainHead

    head = AuditChainHead.objects.filter(pk=1).first()
    return (head.last_seq, head.last_mac) if head else (0, "")


def anchor_line(now=None):
    from django.utils import timezone

    seq, mac = current_head()
    if not seq:
        return ""
    day = (now or timezone.now()).astimezone(dt_timezone.utc).strftime("%Y-%m-%d")
    return f"AUDIT-ANCHOR {VERSION} {seq} {mac} {day}"


def verify(anchor=None, key=None):
    """Проходит цепочку от начала. `anchor` — пара (seq, mac) из ранее выданной строки-якоря."""
    from .models import AuditEvent

    key = key or key_bytes()
    unsealed = AuditEvent.objects.filter(seq__isnull=True).order_by("id").first()
    if unsealed:
        return Result(False, 0, unsealed.pk, "unsealed")
    prev, expected_seq, legacy, last_mac, checked = GENESIS, 1, 0, "", 0
    anchor_seen = anchor is None
    for event in AuditEvent.objects.order_by("seq").iterator(chunk_size=2000):
        if event.seq != expected_seq:
            return Result(False, checked, expected_seq, "gap", legacy)
        if not hmac.compare_digest(mac_of(event, prev, key), event.chain_mac):
            return Result(False, checked, event.seq, "mismatch", legacy)
        if anchor and event.seq == anchor[0]:
            if not hmac.compare_digest(anchor[1], event.chain_mac):
                return Result(False, checked, event.seq, "anchor_mismatch", legacy)
            anchor_seen = True
        legacy += 1 if event.legacy else 0
        prev = last_mac = event.chain_mac
        expected_seq += 1
        checked += 1
    last_seq = expected_seq - 1
    head_seq, head_mac = current_head()
    if (head_seq, head_mac or "") != (last_seq, last_mac):
        return Result(False, checked, last_seq + 1, "head", legacy, last_seq, last_mac)
    if not anchor_seen:
        return Result(False, checked, anchor[0], "anchor_missing", legacy, last_seq, last_mac)
    return Result(True, checked, None, "", legacy, last_seq, last_mac)
