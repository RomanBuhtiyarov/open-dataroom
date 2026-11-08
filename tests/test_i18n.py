import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User

LANGS = ["ru", "uk", "de", "en"]
HEADINGS = {
    "ru": "Вход в Data Room",
    "uk": "Вхід до Data Room",
    "de": "Anmeldung im Data Room",
    "en": "Sign in to the Data Room",
}


def set_lang(client, code, **extra):
    return client.post(reverse("set_language"), {"language": code, "next": "/login/"}, **extra)


@pytest.mark.django_db
@pytest.mark.parametrize("code", LANGS)
def test_login_page_in_every_language(code):
    client = Client()
    assert set_lang(client, code).status_code == 302
    response = client.get(reverse("login"))
    html = response.content.decode()
    assert HEADINGS[code] in html
    assert f'<html lang="{code}">' in html
    assert response["Content-Language"] == code


@pytest.mark.django_db
def test_instance_language_is_default_and_accept_language_ignored(settings):
    settings.DEFAULT_LOCALE = "de"
    response = Client(HTTP_ACCEPT_LANGUAGE="ru").get(reverse("login"))
    assert HEADINGS["de"] in response.content.decode()


@pytest.mark.django_db
def test_unknown_instance_language_falls_back_to_english(settings):
    settings.DEFAULT_LOCALE = "fr"  # недопустимый код
    response = Client().get(reverse("login"))
    assert HEADINGS["en"] in response.content.decode()


@pytest.mark.django_db
def test_only_four_codes_are_accepted():
    client = Client()
    for bad in ["fr", "ru-RU", "", "../etc", "RU"]:
        assert set_lang(client, bad).status_code == 400
    assert settings.LANGUAGE_COOKIE_NAME not in client.cookies


@pytest.mark.django_db
def test_get_is_not_allowed_for_language_switch():
    assert Client().get(reverse("set_language")).status_code == 405


@pytest.mark.django_db
def test_language_cookie_attributes():
    client = Client()
    set_lang(client, "uk")
    cookie = client.cookies[settings.LANGUAGE_COOKIE_NAME]
    assert cookie.value == "uk"
    assert cookie["httponly"]
    assert cookie["samesite"] == "Lax"


@pytest.mark.django_db
def test_fetch_switch_returns_204_without_redirect():
    client = Client()
    response = set_lang(client, "de", HTTP_X_REQUESTED_WITH="fetch")
    assert response.status_code == 204


@pytest.mark.django_db
def test_next_to_foreign_host_is_ignored():
    client = Client()
    response = client.post(reverse("set_language"), {"language": "ru", "next": "https://evil.example/"})
    assert response.status_code == 302
    assert response["Location"] == "/"


@pytest.mark.django_db
def test_order_cookie_then_profile_then_instance(settings):
    settings.DEFAULT_LOCALE = "en"
    user = User.objects.create_user("a@example.com", "pw-pw-pw-pw-1", language="uk")
    client = Client()
    client.force_login(user)
    # профиль важнее языка экземпляра
    assert "Ваші кімнати" in client.get(reverse("home")).content.decode()
    # cookie важнее профиля
    set_lang(client, "de")
    assert "Ihre Räume" in client.get(reverse("home")).content.decode()


@pytest.mark.django_db
def test_switch_updates_profile_for_signed_in_user():
    user = User.objects.create_user("b@example.com", "pw-pw-pw-pw-1")
    client = Client()
    client.force_login(user)
    set_lang(client, "ru")
    user.refresh_from_db()
    assert user.language == "ru"


@pytest.mark.django_db
def test_switcher_lists_all_languages_with_native_names():
    html = Client().get(reverse("login")).content.decode()
    for name in ["Русский", "Українська", "Deutsch", "English"]:
        assert name in html


@pytest.mark.django_db
def test_switch_request_carries_no_secrets_and_forms_have_csrf():
    html = Client().get(reverse("login")).content.decode()
    assert "language-form" in html and "csrfmiddlewaretoken" in html


def test_translation_catalogs_are_complete():
    import polib
    from pathlib import Path

    root = Path(settings.BASE_DIR) / "locale"
    base_ids = {e.msgid for e in polib.pofile(str(root / "ru/LC_MESSAGES/django.po")) if e.msgid}
    for code in ["uk", "de"]:
        po = polib.pofile(str(root / code / "LC_MESSAGES/django.po"))
        ids = {e.msgid for e in po if e.msgid}
        assert ids == base_ids, code
        assert all(e.msgstr for e in po if e.msgid), code
