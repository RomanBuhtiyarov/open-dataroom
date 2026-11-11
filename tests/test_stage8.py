"""Этап 8: водяные знаки на страницах предпросмотра."""
import io
import re

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.audit import events
from apps.audit.models import AuditEvent
from apps.rooms import services, watermark
from apps.rooms.models import AccessGrant, Room

from .helpers import make_pdf, pdf_upload, verified_admin_client, viewer


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = tmp_path
    return tmp_path


def setup_document(watermark_enabled=True):
    client, admin = verified_admin_client()
    room = Room.objects.create(name="Alpha", watermark_enabled=watermark_enabled)
    folder = services.create_folder(room, None, "Docs")
    doc, _v = services.create_document(folder, "Plan", pdf_upload(), admin)
    vclient, user = viewer()
    AccessGrant.objects.create(user=user, room=room)
    return client, admin, room, doc, vclient, user


def pixels(response):
    return Image.open(io.BytesIO(response.content)).convert("RGB")


def view_code(vclient, doc):
    page = vclient.get(reverse("document_view", args=[doc.pk])).content.decode()
    return re.search(r"\?c=([0-9a-f]{8})", page).group(1)


@pytest.mark.django_db
def test_page_is_stamped_and_differs_from_the_plain_render():
    _, _, room, doc, vclient, _ = setup_document()
    stamped = vclient.get(reverse("document_page", args=[doc.pk, 1]))
    room.watermark_enabled = False
    room.save()
    plain = vclient.get(reverse("document_page", args=[doc.pk, 1]))
    assert stamped.status_code == plain.status_code == 200
    assert stamped.content != plain.content
    # на белой пустой странице знак — единственное, что не белое
    assert pixels(plain).getextrema() == ((255, 255), (255, 255), (255, 255))
    assert pixels(stamped).getextrema()[0][0] < 255


@pytest.mark.django_db
def test_stamp_changes_with_user_and_time(monkeypatch):
    _, _, room, doc, vclient, user = setup_document()
    other_client, other = viewer("other@example.com")
    AccessGrant.objects.create(user=other, room=room)
    a = vclient.get(reverse("document_page", args=[doc.pk, 1])).content
    b = other_client.get(reverse("document_page", args=[doc.pk, 1])).content
    assert a != b


@pytest.mark.django_db
def test_disabled_room_serves_the_plain_page():
    _, _, _, doc, vclient, _ = setup_document(watermark_enabled=False)
    response = vclient.get(reverse("document_page", args=[doc.pk, 1]))
    assert pixels(response).getextrema() == ((255, 255), (255, 255), (255, 255))


@pytest.mark.django_db
def test_view_code_is_logged_and_only_accepted_for_the_owner():
    _, _, room, doc, vclient, user = setup_document()
    code = view_code(vclient, doc)
    event = AuditEvent.objects.get(code=events.DOCUMENT_VIEWED, actor=user)
    assert event.target.endswith(f":view:{code}")
    other_client, other = viewer("other@example.com")
    AccessGrant.objects.create(user=other, room=room)
    # чужой код в знак не попадает: страница с этим кодом совпадает со страницей без кода
    with_code = other_client.get(reverse("document_page", args=[doc.pk, 1]), {"c": code}).content
    without = other_client.get(reverse("document_page", args=[doc.pk, 1])).content
    assert pixels_equal(with_code, without)


def pixels_equal(a, b):
    # время в знаке с точностью до минуты; за время теста оно может смениться, поэтому сравниваем размер и крайние значения
    ia, ib = Image.open(io.BytesIO(a)).convert("L"), Image.open(io.BytesIO(b)).convert("L")
    return ia.size == ib.size and ia.getextrema() == ib.getextrema()


@pytest.mark.django_db
def test_access_rules_are_unchanged_by_watermark():
    _, _, room, doc, vclient, user = setup_document()
    AccessGrant.objects.filter(user=user).update(revoked_at=timezone.now())
    assert vclient.get(reverse("document_page", args=[doc.pk, 1])).status_code == 404


@pytest.mark.django_db
def test_room_form_has_the_switch():
    client, _ = verified_admin_client()
    room = Room.objects.create(name="Beta")
    response = client.post(reverse("room_edit", args=[room.pk]), {"name": "Beta", "description": ""})
    assert response.status_code == 302
    room.refresh_from_db()
    assert room.watermark_enabled is False  # снятая галочка не приходит в форме
    client.post(reverse("room_edit", args=[room.pk]), {"name": "Beta", "description": "", "watermark_enabled": "on"})
    room.refresh_from_db()
    assert room.watermark_enabled is True


def test_stamp_lines_are_ascii_only():
    import datetime

    lines = watermark.stamp_lines("ёжик@example.com", datetime.datetime(2026, 1, 2, 3, 4), "10.0.0.1", "abcd1234")
    assert all(line.isascii() for line in lines)
    assert lines[1].startswith("2026-01-02 03:04 UTC") and lines[2] == "#abcd1234"


def test_apply_handles_pages_of_any_size():
    for size in [(40, 40), (300, 400), (1600, 900)]:
        buf = io.BytesIO()
        Image.new("RGB", size, "white").save(buf, format="PNG")
        out = Image.open(io.BytesIO(watermark.apply(buf.getvalue(), ["a@example.com", "x"])))
        assert out.size == size


def test_no_lines_returns_the_page_unchanged():
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(buf, format="PNG")
    assert watermark.apply(buf.getvalue(), ["", ""]) == buf.getvalue()


# --- Аналитика просмотров ---------------------------------------------------

import datetime  # noqa: E402

from apps.rooms import analytics  # noqa: E402
from apps.rooms.models import PageTime  # noqa: E402


def pulse(client, doc, page=1):
    return client.post(reverse("document_pulse", args=[doc.pk]), {"page": page})


@pytest.mark.django_db
def test_pulse_counts_continuous_viewing_only(monkeypatch):
    _, _, _, doc, vclient, user = setup_document()
    version = doc.versions.first()
    t0 = timezone.now()
    analytics.add_time(user, version, 1, now=t0)
    analytics.add_time(user, version, 1, now=t0 + datetime.timedelta(seconds=2))  # слишком рано: игнорируется
    analytics.add_time(user, version, 1, now=t0 + datetime.timedelta(seconds=10))
    analytics.add_time(user, version, 1, now=t0 + datetime.timedelta(seconds=20))
    assert PageTime.objects.get().seconds == 20
    analytics.add_time(user, version, 1, now=t0 + datetime.timedelta(seconds=500))  # ушёл и вернулся: перерыв не считается
    assert PageTime.objects.get().seconds == 20


@pytest.mark.django_db
def test_seconds_are_capped_per_page():
    _, _, _, doc, _, user = setup_document()
    version = doc.versions.first()
    now = timezone.now()
    row = PageTime.objects.create(user=user, version=version, page=1, seconds=analytics.PAGE_SECONDS_CAP - 3, last_seen=now - datetime.timedelta(seconds=10))
    analytics.add_time(user, version, 1, now=now)
    row.refresh_from_db()
    assert row.seconds == analytics.PAGE_SECONDS_CAP


@pytest.mark.django_db
def test_pulse_requires_access_and_valid_page():
    _, _, room, doc, vclient, user = setup_document()
    assert pulse(vclient, doc, 1).status_code == 204
    assert pulse(vclient, doc, 99).status_code == 404
    assert vclient.post(reverse("document_pulse", args=[doc.pk]), {"page": "x"}).status_code == 404
    stranger, _u = viewer("stranger@example.com")
    assert pulse(stranger, doc, 1).status_code == 404
    assert pulse(Client(), doc, 1).status_code == 404
    AccessGrant.objects.filter(user=user).update(revoked_at=timezone.now())
    assert pulse(vclient, doc, 1).status_code == 404
    assert vclient.get(reverse("document_pulse", args=[doc.pk])).status_code == 405


@pytest.mark.django_db
def test_admin_views_are_not_counted():
    client, admin, _, doc, _, _ = setup_document()
    assert pulse(client, doc, 1).status_code == 204
    assert not PageTime.objects.exists()
    client.get(reverse("document_view", args=[doc.pk]))
    rows = analytics.summary()["documents"]
    assert rows[0]["viewers"] == 0 and rows[0]["views"] == 0


@pytest.mark.django_db
def test_summary_shows_opened_unopened_and_time():
    _, _, room, doc, vclient, user = setup_document()
    folder = doc.folder
    other_doc, _v = services.create_document(folder, "Other", pdf_upload(), doc.created_by)
    vclient.get(reverse("document_view", args=[doc.pk]))
    pulse(vclient, doc, 1)
    data = analytics.summary()
    row = next(r for r in data["documents"] if r["document"].pk == doc.pk)
    assert (row["viewers"], row["views"]) == (1, 1)
    visitor = next(v for v in data["visitors"] if v["user"].pk == user.pk)
    assert visitor["visible"] == 2 and visitor["opened"] == 1
    assert [d.pk for d in visitor["unopened"]] == [other_doc.pk]
    assert sum(d["views"] for d in data["days"]) == 1 and len(data["days"]) == analytics.DAYS


@pytest.mark.django_db
def test_screens_and_export_are_admin_only():
    client, _, _, doc, vclient, _ = setup_document()
    vclient.get(reverse("document_view", args=[doc.pk]))
    for name, args in [("analytics", []), ("analytics_export", []), ("analytics_document", [doc.pk])]:
        assert client.get(reverse(name, args=args)).status_code == 200
        assert vclient.get(reverse(name, args=args)).status_code == 403
    csv_text = client.get(reverse("analytics_export")).content.decode()
    assert csv_text.splitlines()[0].startswith("room,document,visitor") and "v@example.com" in csv_text
    assert AuditEvent.objects.filter(code=events.ANALYTICS_EXPORTED).exists()


@pytest.mark.django_db
def test_view_page_tracks_visitors_only():
    client, _, _, doc, vclient, _ = setup_document()
    assert "data-pulse-url" in vclient.get(reverse("document_view", args=[doc.pk])).content.decode()
    assert "data-pulse-url" not in client.get(reverse("document_view", args=[doc.pk])).content.decode()


@pytest.mark.django_db
def test_one_pulse_can_carry_several_pages():
    _, _, _, doc, vclient, _ = setup_document()
    response = vclient.post(reverse("document_pulse", args=[doc.pk]), {"page": ["1"]})
    assert response.status_code == 204
    again = vclient.post(reverse("document_pulse", args=[doc.pk]), {"page": ["1", "1"]})
    assert again.status_code == 204 and PageTime.objects.count() == 1  # повтор номера не множит строки
    assert vclient.post(reverse("document_pulse", args=[doc.pk]), {"page": ["1", "2"]}).status_code == 404  # одна лишняя страница отклоняет сигнал
