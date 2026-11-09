"""Ограничение частоты на базе данных (без Redis). Ключи хешируются: адреса и IP не хранятся в открытом виде."""
import hashlib
import hmac
import random
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import RateLimitHit


def client_ip(request):
    """Адрес клиента. X-Forwarded-For учитывается только при TRUST_PROXY_HEADERS (последний элемент добавлен нашим proxy)."""
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.META.get("REMOTE_ADDR", "")


def make_key(scope, value):
    digest = hmac.new(settings.SECRET_KEY.encode(), f"{scope}:{value}".encode(), hashlib.sha256).hexdigest()
    return f"{scope}:{digest[:32]}"


def count(key, window_seconds):
    since = timezone.now() - timedelta(seconds=window_seconds)
    return RateLimitHit.objects.filter(key=key, created_at__gte=since).count()


def exceeded(key, limit, window_seconds):
    return count(key, window_seconds) >= limit


def hit(key):
    RateLimitHit.objects.create(key=key)
    if random.random() < 0.02:
        purge()


def allow(key, limit, window_seconds):
    """Если лимит не исчерпан, записывает обращение и возвращает True."""
    if exceeded(key, limit, window_seconds):
        return False
    hit(key)
    return True


def purge(older_than_seconds=24 * 3600):
    cutoff = timezone.now() - timedelta(seconds=older_than_seconds)
    return RateLimitHit.objects.filter(created_at__lt=cutoff).delete()[0]
