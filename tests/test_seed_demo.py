"""Команда seed_demo: только для разработки, повторный запуск ничего не дублирует, доступ как задумано."""
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.management.commands.seed_demo import DEMO_PASSWORD
from apps.invites.models import AccessRequest, Invitation
from apps.rooms import policy
from apps.rooms.models import AccessGrant, Document, Room

from .helpers import make_admin


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = tmp_path
    settings.DEBUG = True  # pytest выключает DEBUG; команда работает только при включённом
    return tmp_path


@pytest.mark.django_db
def test_refuses_outside_debug(settings):
    make_admin()
    settings.DEBUG = False
    with pytest.raises(CommandError, match="development only"):
        call_command("seed_demo")
    assert Room.objects.count() == 0


@pytest.mark.django_db
def test_requires_an_administrator():
    with pytest.raises(CommandError, match="administrator"):
        call_command("seed_demo")


@pytest.mark.django_db
def test_seed_is_complete_and_repeatable():
    make_admin()
    call_command("seed_demo")
    counts = (Room.objects.count(), Document.objects.count(), AccessGrant.objects.count(), User.objects.count())
    assert counts[0] == 3 and counts[1] == 10 and counts[3] == 5
    assert Invitation.objects.filter(email="invited@example.com").count() == 1
    assert AccessRequest.objects.get(email="karl@example.com").status == AccessRequest.Status.PENDING
    call_command("seed_demo")
    assert (Room.objects.count(), Document.objects.count(), AccessGrant.objects.count(), User.objects.count()) == counts


@pytest.mark.django_db
def test_seeded_visitors_have_the_intended_access():
    make_admin()
    call_command("seed_demo")
    full = Client()
    assert full.post(reverse("login"), {"username": "investor1@example.com", "password": DEMO_PASSWORD}).status_code == 302
    report = Document.objects.get(title="Отчёт за 3 квартал")
    secret = Document.objects.get(title="Реестр споров")
    assert full.get(reverse("document_download", args=[report.pk])).status_code == 200
    assert full.get(reverse("document_page", args=[report.pk, 1])).status_code == 200
    assert full.get(reverse("document_detail", args=[secret.pk])).status_code == 404  # закрытая комната

    limited = Client()
    limited.post(reverse("login"), {"username": "investor2@example.com", "password": DEMO_PASSWORD})
    assert limited.get(reverse("document_download", args=[report.pk])).status_code == 403
    assert limited.get(reverse("document_page", args=[report.pk, 1])).status_code == 200
    deck = Document.objects.get(title="Презентация для инвесторов")
    assert limited.get(reverse("document_detail", args=[deck.pk])).status_code == 404  # только Financials и Team

    expired = User.objects.get(email="investor4@example.com")
    assert all(policy.access_for(expired, room) is None for room in Room.objects.all())
