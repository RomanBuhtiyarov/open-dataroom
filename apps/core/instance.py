from .models import InstanceSettings


def get_instance(request):
    """Настройки экземпляра, один запрос к БД на HTTP-запрос."""
    cached = getattr(request, "_instance_settings", None)
    if cached is None:
        cached = InstanceSettings.current()
        request._instance_settings = cached
    return cached
