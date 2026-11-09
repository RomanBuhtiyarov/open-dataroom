"""Общие помощники тестов: администратор с подтверждённым TOTP, посетители, файлы."""
import io
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import User

STRONG = "Correct-horse-battery-9"
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def code_for(device):
    return f"{TOTP(device.bin_key, device.step, device.t0, device.digits).token():06d}"


def make_admin(email="boss@example.com"):
    return User.objects.create_user(email, STRONG, name="Boss", role=User.Role.ADMIN)


def password_login(email, password=STRONG):
    client = Client()
    response = client.post(reverse("login"), {"username": email, "password": password})
    assert response.status_code == 302
    return client


def verified_admin_client(email="boss@example.com"):
    admin = User.objects.filter(email=email).first() or make_admin(email)
    client = password_login(email)
    client.get(reverse("two_factor"))
    device = TOTPDevice.objects.get(user=admin)
    assert client.post(reverse("two_factor"), {"code": code_for(device)}).status_code == 302
    return client, admin


def viewer(email="v@example.com", **extra):
    user = User.objects.create_user(email, STRONG, **extra)
    return password_login(email), user


def pdf_upload(name="report.pdf", payload=PDF):
    return SimpleUploadedFile(name, payload, content_type="application/pdf")


def office_bytes(kind="docx", extra=()):
    parts = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(parts[kind], "<x/>")
        for name in extra:
            archive.writestr(name, b"x")
    return buf.getvalue()


def office_upload(kind="docx", name=None, extra=()):
    return SimpleUploadedFile(name or f"file.{kind}", office_bytes(kind, extra), content_type="application/octet-stream")
