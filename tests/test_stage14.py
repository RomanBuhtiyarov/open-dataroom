"""Этап 14: цепочка подписей журнала аудита."""
import csv
import importlib
import io

import pytest
from django.apps import apps as django_apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from apps.audit import chain, events
from apps.audit.models import AuditChainHead, AuditEvent
from apps.audit.service import record

from .helpers import make_admin, verified_admin_client, viewer


def fill(count=5, actor=None):
    for index in range(count):
        record(events.ROOM_CREATED, actor=actor, target=f"room:{index}")


@pytest.mark.django_db
def test_every_record_gets_a_number_and_a_signature_linked_to_the_previous_one():
    fill(4)
    rows = list(AuditEvent.objects.order_by("seq"))
    assert [r.seq for r in rows] == [1, 2, 3, 4]
    assert len({r.chain_mac for r in rows}) == 4 and all(len(r.chain_mac) == 64 for r in rows)
    head = AuditChainHead.objects.get()
    assert (head.last_seq, head.last_mac) == (4, rows[-1].chain_mac)
    result = chain.verify()
    assert result.ok and result.checked == 4 and result.legacy == 0


@pytest.mark.django_db
def test_empty_log_is_intact():
    assert chain.verify().ok and chain.anchor_line() == ""


@pytest.mark.django_db
def test_an_edited_entry_breaks_the_chain_at_that_entry():
    fill(5)
    AuditEvent.objects.filter(seq=3).update(target="room:999")
    result = chain.verify()
    assert not result.ok and result.broken_at == 3 and result.reason == "mismatch"


@pytest.mark.django_db
def test_changing_who_did_it_or_the_outcome_or_the_time_is_detected():
    fill(3)
    first = AuditEvent.objects.get(seq=2)
    for change in ({"outcome": "failed"}, {"code": "room.updated"}, {"request_id": "x"}, {"actor_ref": 1}):
        AuditEvent.objects.filter(pk=first.pk).update(**change)
        assert chain.verify().reason == "mismatch", change
        AuditEvent.objects.filter(pk=first.pk).update(outcome=first.outcome, code=first.code,
                                                       request_id=first.request_id, actor_ref=first.actor_ref)
        assert chain.verify().ok
    AuditEvent.objects.filter(pk=first.pk).update(created_at=first.created_at.replace(year=2001))
    assert chain.verify().reason == "mismatch"


@pytest.mark.django_db
def test_a_removed_entry_in_the_middle_is_a_gap():
    fill(5)
    AuditEvent.objects.filter(seq=3).delete()
    result = chain.verify()
    assert not result.ok and result.reason == "gap" and result.broken_at == 3


@pytest.mark.django_db
def test_removing_the_tail_is_caught_by_the_head_and_then_by_the_anchor():
    fill(5)
    line = chain.anchor_line()
    AuditEvent.objects.filter(seq__gt=3).delete()
    assert chain.verify().reason == "head"
    # злоумышленник поправил и «голову»: остаётся только внешний якорь
    third = AuditEvent.objects.get(seq=3)
    AuditChainHead.objects.update(last_seq=3, last_mac=third.chain_mac)
    assert chain.verify().ok
    result = chain.verify(chain.parse_anchor(line))
    assert not result.ok and result.reason == "anchor_missing" and result.broken_at == 5


@pytest.mark.django_db
def test_an_entry_inserted_behind_the_application_is_found():
    fill(3)
    AuditEvent.objects.bulk_create([AuditEvent(code="room.created", target="forged")])
    assert chain.verify().reason == "unsealed"
    AuditEvent.objects.filter(seq__isnull=True).update(seq=4, chain_mac="a" * 64)
    assert chain.verify().reason == "mismatch"


@pytest.mark.django_db
def test_a_different_key_cannot_validate_the_chain(settings):
    settings.AUDIT_CHAIN_KEY = "key-one"
    fill(3)
    assert chain.verify().ok
    settings.AUDIT_CHAIN_KEY = "key-two"
    assert chain.verify().reason == "mismatch"


@pytest.mark.django_db
def test_without_a_configured_key_one_is_derived_from_the_secret_key(settings):
    settings.AUDIT_CHAIN_KEY = ""
    fill(2)
    assert chain.verify().ok
    settings.SECRET_KEY = "another-secret"
    assert chain.verify().reason == "mismatch"


@pytest.mark.django_db
def test_deleting_a_user_does_not_break_the_chain():
    admin = make_admin()
    fill(3, actor=admin)
    admin.delete()
    assert AuditEvent.objects.filter(seq=2).get().actor is None
    assert chain.verify().ok


@pytest.mark.django_db
def test_anchor_line_roundtrip_and_check():
    fill(3)
    line = chain.anchor_line()
    assert chain.ANCHOR_RE.match(line)
    seq, mac = chain.parse_anchor(line)
    assert seq == 3 and mac == AuditEvent.objects.get(seq=3).chain_mac
    fill(2)
    assert chain.verify(chain.parse_anchor(line)).ok  # цепочка выросла, якорь по-прежнему верен
    assert chain.parse_anchor("AUDIT-ANCHOR v1 3 nothex") is None and chain.parse_anchor("") is None
    forged = f"AUDIT-ANCHOR v1 3 {'b' * 64}"
    assert chain.verify(chain.parse_anchor(forged)).reason == "anchor_mismatch"


@pytest.mark.django_db
def test_sealing_existing_entries_marks_them_legacy_and_verifies():
    fill(4)
    # так выглядела база до этапа 14: без номеров и подписей
    AuditEvent.objects.update(seq=None, actor_ref=None, chain_mac="", legacy=False)
    AuditChainHead.objects.all().delete()
    migration = importlib.import_module("apps.audit.migrations.0003_audit_chain")
    migration.seal_existing(django_apps, None)
    result = chain.verify()
    assert result.ok and result.legacy == 4 and result.checked == 4
    record(events.ROOM_UPDATED, target="room:1")  # новая запись продолжает цепочку
    result = chain.verify()
    assert result.ok and result.checked == 5 and result.legacy == 4
    assert AuditEvent.objects.get(seq=5).legacy is False


@pytest.mark.django_db
def test_log_page_shows_the_state_and_the_check_button_records_the_result():
    client, _admin = verified_admin_client()
    page = client.get(reverse("audit_list")).content.decode()
    assert "has not been checked yet" in page and reverse("audit_verify") in page
    response = client.post(reverse("audit_verify"))
    assert response.status_code == 302
    event = AuditEvent.objects.filter(code=events.AUDIT_VERIFIED).get()
    assert event.outcome == "ok" and event.target.startswith("seq:")
    page = client.get(reverse("audit_list")).content.decode()
    assert "the chain is intact up to entry" in page


@pytest.mark.django_db
def test_a_broken_chain_is_shown_with_the_entry_number():
    client, _admin = verified_admin_client()
    fill(3)
    AuditEvent.objects.filter(code=events.ROOM_CREATED, target="room:1").update(target="room:77")
    broken = AuditEvent.objects.get(target="room:77").seq
    client.post(reverse("audit_verify"))
    event = AuditEvent.objects.filter(code=events.AUDIT_VERIFIED).get()
    assert event.outcome == "failed" and event.target == f"broken:{broken}:mismatch"
    page = client.get(reverse("audit_list")).content.decode()
    assert f"the chain is broken at entry #{broken}" in page and "badge-failed" in page


@pytest.mark.django_db
def test_verify_with_a_bad_anchor_is_refused_and_a_good_one_is_used():
    client, _admin = verified_admin_client()
    client.post(reverse("audit_verify"), {"anchor": "garbage"})
    assert not AuditEvent.objects.filter(code=events.AUDIT_VERIFIED).exists()
    line = client.get(reverse("audit_anchor")).content.decode().strip()
    client.post(reverse("audit_verify"), {"anchor": line})
    assert AuditEvent.objects.filter(code=events.AUDIT_VERIFIED, outcome="ok").exists()


@pytest.mark.django_db
def test_anchor_download_is_a_plain_text_attachment_and_is_audited():
    client, _admin = verified_admin_client()
    response = client.get(reverse("audit_anchor"))
    assert response.status_code == 200 and response["Content-Type"].startswith("text/plain")
    assert "attachment" in response["Content-Disposition"] and response["Cache-Control"].find("no-store") >= 0
    assert chain.ANCHOR_RE.match(response.content.decode().strip())
    assert AuditEvent.objects.filter(code=events.AUDIT_ANCHOR_ISSUED).exists()


@pytest.mark.django_db
def test_visitors_and_anonymous_users_cannot_verify_or_get_an_anchor():
    visitor, _user = viewer()
    from django.test import Client

    for client in (visitor, Client()):
        assert client.post(reverse("audit_verify")).status_code in (302, 403, 404)
        assert client.get(reverse("audit_anchor")).status_code in (302, 403, 404)
    assert not AuditEvent.objects.filter(code__in=[events.AUDIT_VERIFIED, events.AUDIT_ANCHOR_ISSUED]).exists()


@pytest.mark.django_db
def test_verify_needs_post():
    client, _admin = verified_admin_client()
    assert client.get(reverse("audit_verify")).status_code == 405


@pytest.mark.django_db
def test_csv_export_carries_the_number_and_the_signature():
    client, _admin = verified_admin_client()
    fill(2)
    rows = list(csv.reader(io.StringIO(client.get(reverse("audit_export")).content.decode())))
    assert rows[0][-2:] == ["seq", "chain_mac"]
    body = [r for r in rows[1:] if r[2] == events.ROOM_CREATED]
    assert body and all(r[-2].isdigit() and len(r[-1]) == 64 for r in body)


@pytest.mark.django_db
def test_chain_material_has_no_personal_data_and_the_page_shows_no_signature_material():
    fill(2)
    client, _admin = verified_admin_client()
    page = client.get(reverse("audit_list")).content.decode()
    assert AuditEvent.objects.get(seq=1).chain_mac not in page


@pytest.mark.django_db
def test_commands_report_success_and_failure():
    fill(3)
    out = io.StringIO()
    call_command("verify_audit", stdout=out)
    assert "OK: " in out.getvalue()
    anchor_out = io.StringIO()
    call_command("audit_anchor", stdout=anchor_out)
    line = anchor_out.getvalue().strip()
    assert chain.ANCHOR_RE.match(line)
    call_command("verify_audit", "--anchor", line, stdout=io.StringIO())
    AuditEvent.objects.filter(seq=2).update(target="changed")
    with pytest.raises(CommandError, match="BROKEN at #2"):
        call_command("verify_audit", stdout=io.StringIO())
    with pytest.raises(CommandError, match="not valid"):
        call_command("verify_audit", "--anchor", "nonsense")


@pytest.mark.django_db
def test_audit_anchor_command_refuses_an_empty_log():
    with pytest.raises(CommandError, match="empty"):
        call_command("audit_anchor")


@pytest.mark.django_db
def test_a_rolled_back_transaction_leaves_no_gap():
    from django.db import transaction

    fill(2)
    try:
        with transaction.atomic():
            record(events.ROOM_CREATED, target="room:x")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    fill(1)
    assert [e.seq for e in AuditEvent.objects.order_by("seq")] == [1, 2, 3]
    assert chain.verify().ok
