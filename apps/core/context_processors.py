from django.conf import settings
from django.urls import reverse

from .instance import get_instance


def branding(request):
    inst = get_instance(request)
    if inst.logo_file:
        logo = reverse("branding_logo", args=[inst.logo_file])
    else:
        logo = settings.LOGO
    return {
        "SITE_NAME": inst.effective_site_name,
        "BRAND_COLOR": settings.BRAND_COLOR,
        "LOGO": logo,
        "SUPPORTED_LANGUAGES": settings.LANGUAGES,
        "SELF_REGISTRATION": inst.self_registration_enabled,
    }
