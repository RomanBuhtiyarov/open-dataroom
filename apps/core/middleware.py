from django.conf import settings
from django.middleware.locale import LocaleMiddleware
from django.utils import translation


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
    if settings.DEFAULT_LOCALE in supported:
        return settings.DEFAULT_LOCALE
    return "en"


class InstanceLocaleMiddleware(LocaleMiddleware):
    """LocaleMiddleware с порядком выбора языка из ТЗ. Заголовок Content-Language ставит базовый класс."""

    def process_request(self, request):
        language = choose_language(request)
        translation.activate(language)
        request.LANGUAGE_CODE = translation.get_language()
