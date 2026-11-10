"""Идентификатор запроса для журнала аудита. Создаётся сервером; заголовки клиента не используются."""
import contextvars
import uuid

_current = contextvars.ContextVar("request_id", default="")


def current():
    return _current.get()


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        value = uuid.uuid4().hex
        token = _current.set(value)
        request.request_id = value
        try:
            response = self.get_response(request)
        finally:
            _current.reset(token)
        response["X-Request-ID"] = value
        return response
