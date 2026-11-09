"""Токены и коды. В базе лежат только хеши; открытое значение существует лишь у получателя."""
import hashlib
import hmac
import secrets

from django.conf import settings


def new_token():
    return secrets.token_urlsafe(32)


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_code():
    return f"{secrets.randbelow(10**6):06d}"


def hash_code(kind, subject_id, code):
    message = f"{kind}:{subject_id}:{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def codes_equal(a, b):
    return hmac.compare_digest(a, b)


def mask_email(email):
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"
