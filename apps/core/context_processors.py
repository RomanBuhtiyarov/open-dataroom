from django.conf import settings


def branding(request):
    return {
        "SITE_NAME": settings.SITE_NAME,
        "BRAND_COLOR": settings.BRAND_COLOR,
        "LOGO": settings.LOGO,
        "SUPPORTED_LANGUAGES": settings.LANGUAGES,
    }
