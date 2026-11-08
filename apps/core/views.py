from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST


@login_required
@never_cache
def home(request):
    # Заглушка до этапа 2: список комнат появится вместе с моделью комнат.
    return render(request, "core/home.html")


@require_POST
def set_language(request):
    """Сохраняет язык в cookie и, для вошедшего пользователя, в профиле.

    Принимает только четыре допустимых кода. Тело запроса не содержит паролей, кодов и токенов.
    При fetch-запросе возвращает 204, иначе перенаправляет на безопасный next.
    """
    code = request.POST.get("language", "")
    if code not in settings.SUPPORTED_LANGUAGE_CODES:
        return HttpResponseBadRequest("Unsupported language")
    if request.user.is_authenticated and request.user.language != code:
        request.user.language = code
        request.user.save(update_fields=["language"])

    next_url = request.POST.get("next", "")
    allowed = url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    if request.headers.get("X-Requested-With") == "fetch":
        response = HttpResponse(status=204)
    else:
        from django.http import HttpResponseRedirect

        response = HttpResponseRedirect(next_url if allowed else "/")
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        code,
        max_age=settings.LANGUAGE_COOKIE_AGE,
        secure=settings.LANGUAGE_COOKIE_SECURE,
        httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
        samesite=settings.LANGUAGE_COOKIE_SAMESITE,
    )
    return response


@require_GET
@never_cache
def healthz(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return HttpResponse("ok", content_type="text/plain")
