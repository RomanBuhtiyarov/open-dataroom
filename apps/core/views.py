from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.audit import events
from apps.audit.service import record

from . import logo as logo_service
from .decorators import admin_required
from .forms import InstanceSettingsForm
from .instance import get_instance
from .models import InstanceSettings


@login_required
@never_cache
def home(request):
    # Список комнат появится на этапе 2. Пока администратор сначала проходит первичную настройку.
    if request.user.is_admin and not get_instance(request).is_setup_complete:
        return redirect("setup")
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
        response = HttpResponseRedirect(next_url if allowed else "/")
    set_language_cookie(response, code)
    return response


def set_language_cookie(response, code):
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        code,
        max_age=settings.LANGUAGE_COOKIE_AGE,
        secure=settings.LANGUAGE_COOKIE_SECURE,
        httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
        samesite=settings.LANGUAGE_COOKIE_SAMESITE,
    )


@require_GET
@never_cache
def healthz(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return HttpResponse("ok", content_type="text/plain")


@require_GET
def branding_logo(request, filename):
    """Логотип экземпляра. Публичный (нужен на странице входа), но только текущий файл."""
    inst = get_instance(request)
    path = logo_service.logo_path(filename)
    if path is None or filename != inst.logo_file or not path.exists():
        raise Http404
    ext = filename.rsplit(".", 1)[1]
    response = FileResponse(open(path, "rb"), content_type=logo_service.CONTENT_TYPES[ext])
    # Имя файла содержит хэш содержимого, поэтому кэш безопасен.
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


def _changed_fields(form):
    return ",".join(sorted(form.changed_data))


@admin_required
@never_cache
def setup(request):
    """Экран первого запуска: название, логотип, язык, шаблон комнаты, самостоятельная заявка."""
    inst = InstanceSettings.objects.get_or_create(pk=1)[0]
    if inst.is_setup_complete:
        return redirect("instance_settings")
    form = InstanceSettingsForm(request.POST or None, request.FILES or None, instance=inst)
    if request.method == "POST" and form.is_valid():
        form.save(complete_setup=True)
        record(events.INSTANCE_SETUP_COMPLETED, actor=request.user, target="instance")
        messages.success(request, _("The data room is set up."))
        return redirect("home")
    return render(request, "core/instance_form.html", {"form": form, "setup": True})


@admin_required
@never_cache
def instance_settings(request):
    inst = InstanceSettings.objects.get_or_create(pk=1)[0]
    if not inst.is_setup_complete:
        return redirect("setup")
    form = InstanceSettingsForm(request.POST or None, request.FILES or None, instance=inst)
    if request.method == "POST" and form.is_valid():
        changed = _changed_fields(form)
        form.save()
        record(events.INSTANCE_SETTINGS_CHANGED, actor=request.user, target=f"fields:{changed}")
        messages.success(request, _("Settings saved."))
        return redirect("instance_settings")
    return render(request, "core/instance_form.html", {"form": form, "setup": False})
