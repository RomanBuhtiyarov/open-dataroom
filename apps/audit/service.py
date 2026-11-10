from apps.core.requestid import current as current_request_id

from .models import AuditEvent


def record(code, actor=None, target="", outcome=AuditEvent.Outcome.OK):
    """Записывает событие. `target` — идентификатор объекта, не персональные данные."""
    return AuditEvent.objects.create(code=code, actor=actor, target=target[:200], outcome=outcome,
                                   request_id=current_request_id())
