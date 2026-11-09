from django.shortcuts import redirect
from django.urls import Resolver404, resolve
from django.utils.http import urlencode

# Страницы, доступные администратору до подтверждения второго фактора.
EXEMPT_URL_NAMES = {"two_factor", "logout", "set_language", "healthz", "branding_logo"}


class RequireTwoFactorMiddleware:
    """Администратор без подтверждённого второго фактора видит только страницу /2fa/.

    Проверка стоит перед всеми представлениями, поэтому любое действие администратора
    (в том числе будущие разделы) требует TOTP.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and user.is_admin and not user.is_verified():
            try:
                name = resolve(request.path_info).url_name
            except Resolver404:
                name = None
            if name is not None and name not in EXEMPT_URL_NAMES:
                query = urlencode({"next": request.get_full_path()}) if request.method == "GET" else ""
                return redirect("/2fa/" + (f"?{query}" if query else ""))
        return self.get_response(request)
