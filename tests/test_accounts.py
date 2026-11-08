import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User


@pytest.mark.django_db
def test_email_is_normalized_and_unique_case_insensitive():
    user = User.objects.create_user("  Anna@Example.COM ", "pw-pw-pw-pw-1")
    assert user.email == "anna@example.com"
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user("ANNA@example.com", "pw-pw-pw-pw-1")


@pytest.mark.django_db
def test_suspended_user_cannot_sign_in():
    User.objects.create_user("s@example.com", "pw-pw-pw-pw-1", status=User.Status.SUSPENDED)
    response = Client().post(reverse("login"), {"username": "s@example.com", "password": "pw-pw-pw-pw-1"})
    assert response.status_code == 200
    assert "_auth_user_id" not in response.wsgi_request.session


@pytest.mark.django_db
def test_sign_in_with_mixed_case_email_and_redirect_home():
    User.objects.create_user("m@example.com", "pw-pw-pw-pw-1")
    client = Client()
    response = client.post(reverse("login"), {"username": " M@Example.com", "password": "pw-pw-pw-pw-1"})
    assert response.status_code == 302 and response["Location"] == "/"


@pytest.mark.django_db
def test_home_requires_login():
    response = Client().get(reverse("home"))
    assert response.status_code == 302 and response["Location"].startswith("/login/")


@pytest.mark.django_db
def test_security_headers_and_cookie_names():
    response = Client().get(reverse("login"))
    assert response["Referrer-Policy"] == "no-referrer"
    assert response["X-Frame-Options"] == "DENY"


@pytest.mark.django_db
def test_healthz():
    assert Client().get(reverse("healthz")).content == b"ok"


@pytest.mark.django_db
def test_foreign_host_is_rejected():
    response = Client().get("/login/", HTTP_HOST="evil.example")
    assert response.status_code == 400
