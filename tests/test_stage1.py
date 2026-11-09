"""Этап 1: первый администратор, второй фактор, экран первого запуска, настройки и профиль."""
import io
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice
from PIL import Image

from apps.accounts.management.commands import bootstrap_admin as bootstrap_module
from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.core.models import InstanceSettings

STRONG = "Correct-horse-battery-9"


def code_for(device):
    return f"{TOTP(device.bin_key, device.step, device.t0, device.digits).token():06d}"


def make_admin(email="boss@example.com"):
    return User.objects.create_user(email, STRONG, name="Boss", role=User.Role.ADMIN)


def password_login(email="boss@example.com", password=STRONG):
    client = Client()
    response = client.post(reverse("login"), {"username": email, "password": password})
    assert response.status_code == 302
    return client


def verified_admin_client(email="boss@example.com"):
    """Администратор прошёл вход и подключил TOTP."""
    admin = User.objects.filter(email=email).first() or make_admin(email)
    client = password_login(email)
    client.get(reverse("two_factor"))
    device = TOTPDevice.objects.get(user=admin)
    response = client.post(reverse("two_factor"), {"code": code_for(device)})
    assert response.status_code == 302
    return client, admin


def png_upload(size=(64, 64), color="#235244", name="logo.png"):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


SETUP_DATA = {
    "site_name": "Acme Fund",
    "default_locale": "de",
    "room_template": "due_diligence",
    "self_registration_enabled": "on",
}


# --- bootstrap_admin --------------------------------------------------------


def run_bootstrap(passwords, email="first@example.com", name="First Admin"):
    with mock.patch.object(bootstrap_module, "getpass", side_effect=passwords):
        call_command("bootstrap_admin", email=email, name=name, stdout=io.StringIO(), stderr=io.StringIO())


@pytest.mark.django_db
def test_bootstrap_admin_creates_first_admin_and_records_event():
    run_bootstrap([STRONG, STRONG], email=" First@Example.com ")
    admin = User.objects.get()
    assert admin.email == "first@example.com"
    assert admin.role == User.Role.ADMIN and admin.email_confirmed_at
    assert admin.check_password(STRONG)
    assert InstanceSettings.objects.get(pk=1).bootstrapped_at
    event = AuditEvent.objects.get(code="admin.bootstrapped")
    assert event.target == f"user:{admin.pk}" and event.actor == admin
    assert "example.com" not in event.target and STRONG not in event.target


@pytest.mark.django_db
def test_bootstrap_admin_refuses_second_run():
    run_bootstrap([STRONG, STRONG])
    with pytest.raises(CommandError, match="only once"):
        run_bootstrap([STRONG, STRONG], email="second@example.com")
    assert User.objects.count() == 1


@pytest.mark.django_db
def test_bootstrap_admin_refuses_when_an_admin_already_exists():
    make_admin()
    with pytest.raises(CommandError, match="only once"):
        run_bootstrap([STRONG, STRONG])
    assert User.objects.count() == 1


@pytest.mark.django_db
def test_bootstrap_admin_rechecks_under_lock_against_concurrent_run():
    """Другой процесс успел создать администратора между ранней проверкой и транзакцией."""
    make_admin("other@example.com")
    with mock.patch.object(bootstrap_module, "already_bootstrapped", return_value=False):
        with pytest.raises(CommandError, match="only once"):
            run_bootstrap([STRONG, STRONG])
    assert User.objects.filter(role=User.Role.ADMIN).count() == 1


@pytest.mark.django_db
def test_bootstrap_admin_rejects_weak_password_and_mismatch_then_accepts():
    # "short" не проходит проверку; затем подтверждение не совпадает; затем всё верно.
    run_bootstrap(["short", STRONG, "different-1234567", STRONG, STRONG])
    assert User.objects.get().check_password(STRONG)


@pytest.mark.django_db
def test_bootstrap_admin_never_prints_the_password():
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(bootstrap_module, "getpass", side_effect=[STRONG, STRONG]):
        call_command("bootstrap_admin", email="p@example.com", name="P", stdout=out, stderr=err)
    assert STRONG not in out.getvalue() + err.getvalue()


# --- Второй фактор ----------------------------------------------------------


@pytest.mark.django_db
def test_admin_without_second_factor_is_sent_to_2fa_everywhere():
    make_admin()
    client = password_login()
    for name in ("home", "instance_settings", "setup", "profile"):
        response = client.get(reverse(name))
        assert response.status_code == 302 and response["Location"].startswith("/2fa/"), name
    # Выйти и сменить язык можно без второго фактора.
    assert client.get(reverse("healthz")).status_code == 200
    assert client.post(reverse("set_language"), {"language": "de", "next": "/"}).status_code == 302


@pytest.mark.django_db
def test_post_before_second_factor_is_not_executed():
    make_admin()
    client = password_login()
    response = client.post(reverse("profile"), {"name": "Hacked", "language": ""})
    assert response.status_code == 302 and response["Location"].startswith("/2fa/")
    assert User.objects.get().name == "Boss"


@pytest.mark.django_db
def test_enrolment_shows_qr_and_secret_keeps_secret_and_confirms_with_valid_code():
    admin = make_admin()
    client = password_login()
    first = client.get(reverse("two_factor"))
    html = first.content.decode()
    assert "<svg" in html and 'class="secret"' in html
    assert "no-store" in first["Cache-Control"]
    device = TOTPDevice.objects.get(user=admin)
    assert device.confirmed is False
    key = device.key
    client.get(reverse("two_factor"))  # обновление страницы не меняет секрет
    assert TOTPDevice.objects.get(user=admin).key == key

    bad = client.post(reverse("two_factor"), {"code": "000000" if code_for(device) != "000000" else "111111"})
    assert bad.status_code == 200
    assert AuditEvent.objects.filter(code="auth.2fa_failed", outcome="failed").exists()
    assert client.get(reverse("home"))["Location"].startswith("/2fa/")

    device = TOTPDevice.objects.get(user=admin)
    device.throttling_failure_count = 0
    device.throttling_failure_timestamp = None
    device.save()
    ok = client.post(reverse("two_factor"), {"code": code_for(device)})
    assert ok.status_code == 302
    assert TOTPDevice.objects.get(user=admin).confirmed is True
    assert AuditEvent.objects.filter(code="auth.2fa_enrolled", actor=admin).exists()
    assert client.get(reverse("home")).status_code == 302  # теперь на /setup/, а не на /2fa/
    assert client.get(reverse("home"))["Location"] == reverse("setup")


@pytest.mark.django_db
def test_qr_page_does_not_leak_the_secret_outside_the_page():
    admin = make_admin()
    client = password_login()
    response = client.get(reverse("two_factor"))
    device = TOTPDevice.objects.get(user=admin)
    assert device.key not in response.content.decode()  # hex-ключ не выводится
    assert "otpauth://" not in response.content.decode()  # ссылка только внутри QR-картинки


@pytest.mark.django_db
def test_returning_admin_verifies_with_code_and_wrong_code_is_throttled(settings):
    settings.OTP_TOTP_THROTTLE_FACTOR = 600
    admin = make_admin()
    verified_admin_client()  # подключает устройство
    client = password_login()
    device = TOTPDevice.objects.get(user=admin)
    page = client.get(reverse("two_factor"))
    assert "<svg" not in page.content.decode()  # QR показывается только при подключении

    wrong = "000000" if code_for(device) != "000000" else "111111"
    assert client.post(reverse("two_factor"), {"code": wrong}).status_code == 200
    # После ошибки даже верный код временно не принимается.
    device.refresh_from_db()
    device.last_t = -1
    device.save()
    blocked = client.post(reverse("two_factor"), {"code": code_for(device)})
    assert blocked.status_code == 200
    assert "Too many wrong codes" in blocked.content.decode()
    assert client.get(reverse("home"))["Location"].startswith("/2fa/")


@pytest.mark.django_db
def test_returning_admin_gets_in_with_valid_code():
    admin = make_admin()
    verified_admin_client()
    client = password_login()
    device = TOTPDevice.objects.get(user=admin)
    device.last_t = -1
    device.save()
    response = client.post(reverse("two_factor"), {"code": code_for(device)})
    assert response.status_code == 302
    assert AuditEvent.objects.filter(code="auth.2fa_verified", actor=admin).exists()


@pytest.mark.django_db
def test_viewer_does_not_need_second_factor_and_cannot_open_admin_pages():
    User.objects.create_user("v@example.com", STRONG)
    client = password_login("v@example.com")
    assert client.get(reverse("home")).status_code == 200
    assert client.get(reverse("setup")).status_code == 403
    assert client.get(reverse("instance_settings")).status_code == 403
    assert client.get(reverse("two_factor")).status_code == 302  # нечего подтверждать


@pytest.mark.django_db
def test_reset_2fa_forces_new_enrolment_and_is_audited():
    admin = make_admin()
    verified_admin_client()
    call_command("reset_2fa", "BOSS@example.com", stdout=io.StringIO())
    assert not TOTPDevice.objects.filter(user=admin).exists()
    assert AuditEvent.objects.filter(code="auth.2fa_reset").exists()
    client = password_login()
    assert "<svg" in client.get(reverse("two_factor")).content.decode()
    with pytest.raises(CommandError):
        call_command("reset_2fa", "nobody@example.com", stdout=io.StringIO())


# --- Экран первого запуска и настройки --------------------------------------


@pytest.mark.django_db
def test_first_launch_flow_saves_settings_and_applies_instance_language():
    client, admin = verified_admin_client()
    assert client.get(reverse("home"))["Location"] == reverse("setup")
    page = client.get(reverse("setup"))
    assert page.status_code == 200 and "Set up your data room" in page.content.decode()

    response = client.post(reverse("setup"), SETUP_DATA)
    assert response.status_code == 302 and response["Location"] == reverse("home")
    inst = InstanceSettings.objects.get(pk=1)
    assert inst.site_name == "Acme Fund" and inst.default_locale == "de"
    assert inst.room_template == "due_diligence" and inst.self_registration_enabled is True
    assert inst.setup_completed_at
    assert AuditEvent.objects.filter(code="instance.setup_completed", actor=admin).exists()

    # Язык экземпляра действует для анонима без cookie, название — в шапке.
    anonymous = Client().get(reverse("login"))
    html = anonymous.content.decode()
    assert "Anmeldung im Data Room" in html and "Acme Fund" in html
    # После настройки /setup/ ведёт в настройки.
    assert client.get(reverse("setup"))["Location"] == reverse("instance_settings")
    assert client.get(reverse("home")).status_code == 200


@pytest.mark.django_db
def test_setup_validates_input():
    client, _admin = verified_admin_client()
    bad_locale = client.post(reverse("setup"), {**SETUP_DATA, "default_locale": "fr"})
    assert bad_locale.status_code == 200 and not InstanceSettings.objects.get(pk=1).is_setup_complete
    no_name = client.post(reverse("setup"), {**SETUP_DATA, "site_name": "   "})
    assert no_name.status_code == 200 and not InstanceSettings.objects.get(pk=1).is_setup_complete
    bad_template = client.post(reverse("setup"), {**SETUP_DATA, "room_template": "evil"})
    assert bad_template.status_code == 200


@pytest.mark.django_db
def test_settings_page_updates_values_and_audits_changed_fields_only():
    client, admin = verified_admin_client()
    client.post(reverse("setup"), SETUP_DATA)
    response = client.post(reverse("instance_settings"), {**SETUP_DATA, "site_name": "Acme Capital", "default_locale": "uk"})
    assert response.status_code == 302
    inst = InstanceSettings.objects.get(pk=1)
    assert inst.site_name == "Acme Capital" and inst.default_locale == "uk"
    event = AuditEvent.objects.filter(code="instance.settings_changed").get()
    assert event.target == "fields:default_locale,site_name"
    assert "Acme" not in event.target


@pytest.mark.django_db
def test_anonymous_cannot_open_setup_or_settings():
    for name in ("setup", "instance_settings", "profile"):
        response = Client().get(reverse(name))
        assert response.status_code == 302 and response["Location"].startswith("/login/")


@pytest.mark.django_db
def test_no_request_can_make_a_user_an_administrator():
    viewer = User.objects.create_user("v@example.com", STRONG)
    client = password_login("v@example.com")
    client.post(reverse("profile"), {"name": "V", "language": "", "role": "admin", "status": "active", "is_admin": "1"})
    viewer.refresh_from_db()
    assert viewer.role == User.Role.VIEWER
    assert not User.objects.filter(role=User.Role.ADMIN).exists()


# --- Логотип ----------------------------------------------------------------


@pytest.fixture
def media(settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.mark.django_db
def test_logo_upload_is_validated_stored_privately_and_served(media):
    client, _admin = verified_admin_client()
    client.post(reverse("setup"), {**SETUP_DATA, "logo": png_upload()})
    inst = InstanceSettings.objects.get(pk=1)
    assert inst.logo_file.startswith("logo-") and inst.logo_file.endswith(".png")
    assert (media / "branding" / inst.logo_file).exists()

    url = reverse("branding_logo", args=[inst.logo_file])
    response = Client().get(url)  # публичный: нужен на странице входа
    assert response.status_code == 200 and response["Content-Type"] == "image/png"
    assert "immutable" in response["Cache-Control"]
    assert url in Client().get(reverse("login")).content.decode()
    assert Client().get(reverse("branding_logo", args=["logo-000000000000.png"])).status_code == 404
    assert Client().get("/branding/logo/..%2Fsecret.png").status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize(
    "upload",
    [
        SimpleUploadedFile("logo.png", b"not an image", content_type="image/png"),
        SimpleUploadedFile("logo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", content_type="image/svg+xml"),
        SimpleUploadedFile("logo.png", b"GIF89a" + b"\x00" * 32, content_type="image/png"),
    ],
)
def test_logo_rejects_non_images_and_svg(media, upload):
    client, _admin = verified_admin_client()
    response = client.post(reverse("setup"), {**SETUP_DATA, "logo": upload})
    assert response.status_code == 200
    inst = InstanceSettings.objects.get(pk=1)
    assert inst.logo_file == "" and not inst.is_setup_complete
    assert not (media / "branding").exists() or not list((media / "branding").iterdir())


@pytest.mark.django_db
def test_logo_rejects_oversized_file_and_dimensions(media, settings):
    client, _admin = verified_admin_client()
    settings.MAX_LOGO_BYTES = 100
    assert client.post(reverse("setup"), {**SETUP_DATA, "logo": png_upload()}).status_code == 200
    settings.MAX_LOGO_BYTES = 512 * 1024
    settings.MAX_LOGO_PIXELS = 32
    assert client.post(reverse("setup"), {**SETUP_DATA, "logo": png_upload()}).status_code == 200
    assert InstanceSettings.objects.get(pk=1).logo_file == ""


@pytest.mark.django_db
def test_logo_replace_and_remove_delete_old_files(media):
    client, _admin = verified_admin_client()
    client.post(reverse("setup"), {**SETUP_DATA, "logo": png_upload(color="#ff0000")})
    first = InstanceSettings.objects.get(pk=1).logo_file
    client.post(reverse("instance_settings"), {**SETUP_DATA, "logo": png_upload(color="#0000ff")})
    second = InstanceSettings.objects.get(pk=1).logo_file
    assert second != first
    assert not (media / "branding" / first).exists() and (media / "branding" / second).exists()
    client.post(reverse("instance_settings"), {**SETUP_DATA, "remove_logo": "on"})
    assert InstanceSettings.objects.get(pk=1).logo_file == ""
    assert not (media / "branding" / second).exists()


# --- Профиль ----------------------------------------------------------------


@pytest.mark.django_db
def test_profile_saves_name_and_language_and_updates_cookie():
    User.objects.create_user("v@example.com", STRONG)
    client = password_login("v@example.com")
    response = client.post(reverse("profile"), {"name": "Vera", "language": "uk"})
    assert response.status_code == 302
    user = User.objects.get()
    assert user.name == "Vera" and user.language == "uk"
    assert response.cookies["dataroom_lang"].value == "uk"
    assert "Профіль" in client.get(reverse("profile")).content.decode()

    cleared = client.post(reverse("profile"), {"name": "Vera", "language": ""})
    assert cleared.cookies["dataroom_lang"].value == ""  # cookie удалена, действует язык экземпляра
    assert User.objects.get().language == ""


@pytest.mark.django_db
def test_profile_rejects_unknown_language():
    User.objects.create_user("v@example.com", STRONG)
    client = password_login("v@example.com")
    assert client.post(reverse("profile"), {"name": "V", "language": "fr"}).status_code == 200
    assert User.objects.get().language == ""


@pytest.mark.django_db
def test_profile_shows_two_factor_status_for_admin():
    make_admin()
    client, _admin = verified_admin_client()
    assert "Enabled." in client.get(reverse("profile")).content.decode()


# --- Переводы ---------------------------------------------------------------

TWO_FA_HEADINGS = {
    "ru": "Настройка двухфакторной аутентификации",
    "uk": "Налаштування двофакторної автентифікації",
    "de": "Zwei-Faktor-Authentifizierung einrichten",
    "en": "Set up two-factor authentication",
}


@pytest.mark.django_db
@pytest.mark.parametrize("code", ["ru", "uk", "de", "en"])
def test_two_factor_page_is_translated(code):
    make_admin()
    client = password_login()
    client.post(reverse("set_language"), {"language": code, "next": "/"})
    assert TWO_FA_HEADINGS[code] in client.get(reverse("two_factor")).content.decode()


def test_every_string_used_in_code_exists_in_every_catalogue():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import check_messages

    assert check_messages.missing() == {}
