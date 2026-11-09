from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.core.views import set_language_cookie

from .forms import ProfileForm


@login_required
@never_cache
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, _("Profile saved."))
        response = redirect("profile")
        # Cookie имеет приоритет над профилем, поэтому обновляем её вместе с профилем.
        if user.language:
            set_language_cookie(response, user.language)
        else:
            response.delete_cookie(settings.LANGUAGE_COOKIE_NAME)
        return response
    has_2fa = request.user.is_admin and TOTPDevice.objects.filter(user=request.user, confirmed=True).exists()
    return render(request, "accounts/profile.html", {"form": form, "has_2fa": has_2fa})
