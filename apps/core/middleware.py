from django.conf import settings
from django.middleware.locale import LocaleMiddleware
from django.utils import translation

from .instance import get_instance


def choose_language(request):
    """Порядок выбора (ТЗ, 6): cookie браузера, затем профиль вошедшего, затем язык экземпляра.

    Accept-Language намеренно не учитывается. Допускаются только четыре кода.
    """
    supported = settings.SUPPORTED_LANGUAGE_CODES
    cookie = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
    if cookie in supported:
        return cookie
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.language in supported:
        return user.language
    instance_language = get_instance(request).effective_locale
    if instance_language in supported:
        return instance_language
    return "en"


class InstanceLocaleMiddleware(LocaleMiddleware):
    """LocaleMiddleware с порядком выбора языка из ТЗ. Заголовок Content-Language ставит базовый класс."""

    def process_request(self, request):
        language = choose_language(request)
        translation.activate(language)
        request.LANGUAGE_CODE = translation.get_language()


# Страницы с токенами и кодами: не кэшируются и не передают адрес в Referer.
SENSITIVE_URL_NAMES = {
    "invite_entry", "request_access", "request_confirm", "request_status",
    "password_reset", "password_reset_done", "password_reset_confirm",
}


class SensitivePagesMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        match = getattr(request, "resolver_match", None)
        if match is not None and match.url_name in SENSITIVE_URL_NAMES:
            response["Cache-Control"] = "no-store, private"
            # Не "no-referrer": при такой политике браузер шлёт на POST заголовок "Origin: null",
            # и проверка CSRF отклоняет каждую форму. "same-origin" ничего не передаёт на чужие сайты,
            # а токен с этих страниц уходит из адреса сразу после первого открытия.
            response["Referrer-Policy"] = "same-origin"
        return response
