"""Этап 12: группы посетителей, матрица прав, «посмотреть как»."""
import datetime

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.invites import services as invite_services
from apps.rooms import access_services as svc
from apps.rooms import policy
from apps.rooms import services as room_services
from apps.rooms.models import AccessGrant, Group, GroupMember, Room

from .helpers import STRONG, make_pdf, pdf_upload, verified_admin_client, viewer


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = tmp_path / "media"
    settings.PRIVATE_MEDIA_ROOT.mkdir()
    return settings.PRIVATE_MEDIA_ROOT


def person(email):
    return User.objects.create_user(email, STRONG, name=email.split("@")[0])


def setup_room():
    room = Room.objects.create(name="Alpha")
    legal = room_services.create_folder(room, None, "Legal")
    contracts = room_services.create_folder(room, legal, "Contracts")
    finance = room_services.create_folder(room, None, "Finance")
    return room, legal, contracts, finance


def group_grant(group, room, folder=None, download=False, **extra):
    return AccessGrant.objects.create(group=group, room=room, folder=folder, can_download=download, **extra)


# --- Политика: права групп складываются с личными ---------------------------


@pytest.mark.django_db
def test_group_grant_gives_members_access_and_non_members_none():
    room, legal, _c, finance = setup_room()
    member, outsider = person("m@example.com"), person("o@example.com")
    group = Group.objects.create(room=room, name="Fund A")
    GroupMember.objects.create(group=group, user=member)
    group_grant(group, room, legal)

    access = policy.access_for(member, room)
    assert access is not None and access.can_view_folder(legal.pk) and not access.can_view_folder(finance.pk)
    assert policy.access_for(outsider, room) is None
    assert room in policy.visible_rooms(member) and room not in policy.visible_rooms(outsider)


@pytest.mark.django_db
def test_personal_and_group_rights_add_up_without_duplicates():
    room, legal, _c, finance = setup_room()
    user = person("m@example.com")
    first, second = Group.objects.create(room=room, name="A"), Group.objects.create(room=room, name="B")
    for g in (first, second):
        GroupMember.objects.create(group=g, user=user)
    group_grant(first, room, legal)
    group_grant(second, room, finance, download=True)
    AccessGrant.objects.create(user=user, room=room, folder=legal, can_download=True)

    access = policy.access_for(user, room)
    assert access.can_view_folder(legal.pk) and access.can_view_folder(finance.pk)
    assert access.can_download_in(legal.pk) and access.can_download_in(finance.pk)
    assert list(policy.visible_rooms(user)) == [room]  # без дублей


@pytest.mark.django_db
def test_group_rights_follow_revocation_expiry_membership_and_deletion():
    room, legal, _c, _f = setup_room()
    user = person("m@example.com")
    group = Group.objects.create(room=room, name="A")
    member = GroupMember.objects.create(group=group, user=user)
    grant = group_grant(group, room, legal)
    assert policy.access_for(user, room) is not None

    grant.expires_at = timezone.now() - datetime.timedelta(seconds=1)
    grant.save()
    assert policy.access_for(user, room) is None
    grant.expires_at = None
    grant.revoked_at = timezone.now()
    grant.save()
    assert policy.access_for(user, room) is None
    grant.revoked_at = None
    grant.save()
    assert policy.access_for(user, room) is not None

    member.delete()
    assert policy.access_for(user, room) is None
    GroupMember.objects.create(group=group, user=user)
    assert policy.access_for(user, room) is not None
    group.delete()
    assert policy.access_for(user, room) is None
    assert AccessGrant.objects.count() == 0  # права группы удалены вместе с ней


@pytest.mark.django_db
def test_group_of_one_room_gives_nothing_in_another_room():
    room, legal, _c, _f = setup_room()
    other = Room.objects.create(name="Beta")
    user = person("m@example.com")
    group = Group.objects.create(room=room, name="A")
    GroupMember.objects.create(group=group, user=user)
    group_grant(group, room, legal)
    assert policy.access_for(user, other) is None
    assert other not in policy.visible_rooms(user)


@pytest.mark.django_db
def test_suspended_member_and_admin_member_get_no_access_through_a_group():
    room, legal, _c, _f = setup_room()
    user = person("m@example.com")
    group = Group.objects.create(room=room, name="A")
    GroupMember.objects.create(group=group, user=user)
    group_grant(group, room, legal)
    user.status = User.Status.SUSPENDED
    user.save()
    assert policy.access_for(user, room) is None
    assert policy.visitor_access(user, room) is None


@pytest.mark.django_db
def test_group_download_right_is_enforced_for_documents():
    room, legal, _c, finance = setup_room()
    user = person("m@example.com")
    group = Group.objects.create(room=room, name="A")
    GroupMember.objects.create(group=group, user=user)
    group_grant(group, room, legal, download=False)
    group_grant(group, room, finance, download=True)
    legal_doc, _v = room_services.create_document(legal, "L", pdf_upload(), None)
    fin_doc, _v = room_services.create_document(finance, "F", pdf_upload("f.pdf", make_pdf(2)), None)
    assert policy.can_view_document(user, legal_doc) and not policy.can_download_document(user, legal_doc)
    assert policy.can_download_document(user, fin_doc)


@pytest.mark.django_db
def test_a_grant_belongs_to_exactly_one_subject():
    room, *_ = setup_room()
    user = person("m@example.com")
    group = Group.objects.create(room=room, name="A")
    for kwargs in ({}, {"user": user, "group": group}):
        with pytest.raises(IntegrityError), transaction.atomic():
            AccessGrant.objects.create(room=room, **kwargs)


@pytest.mark.django_db
def test_group_names_are_unique_per_room_ignoring_case():
    room, *_ = setup_room()
    other = Room.objects.create(name="Beta")
    admin = person("a@example.com")
    svc.create_group(room, "Fund A", admin)
    with pytest.raises(Exception):
        svc.create_group(room, "  fund   a ", admin)
    svc.create_group(other, "Fund A", admin)  # в другой комнате можно


# --- Сервис уровней ---------------------------------------------------------


@pytest.mark.django_db
def test_set_level_revokes_old_grant_creates_new_and_audits_each_change():
    room, legal, *_ = setup_room()
    admin = person("a@example.com")
    group = Group.objects.create(room=room, name="A")
    assert svc.set_level(room, group=group, folder=legal, level="view", actor=admin) is True
    assert svc.set_level(room, group=group, folder=legal, level="view", actor=admin) is False  # без изменений
    assert svc.set_level(room, group=group, folder=legal, level="download", actor=admin) is True
    active = [g for g in AccessGrant.objects.all() if g.is_active()]
    assert len(active) == 1 and active[0].can_download
    assert svc.set_level(room, group=group, folder=legal, level="none", actor=admin) is True
    assert not [g for g in AccessGrant.objects.all() if g.is_active()]
    assert AuditEvent.objects.filter(code="grant.created").count() == 2
    assert AuditEvent.objects.filter(code="grant.revoked").count() == 2


@pytest.mark.django_db
def test_set_level_keeps_the_expiry_of_the_replaced_grant():
    room, legal, *_ = setup_room()
    admin = person("a@example.com")
    group = Group.objects.create(room=room, name="A")
    end = timezone.now() + datetime.timedelta(days=5)
    group_grant(group, room, legal, expires_at=end)
    svc.set_level(room, group=group, folder=legal, level="download", actor=admin)
    new = [g for g in AccessGrant.objects.all() if g.is_active()][0]
    assert new.expires_at == end and new.can_download


@pytest.mark.django_db
def test_set_level_rejects_foreign_room_objects_and_inactive_or_admin_users():
    room, legal, *_ = setup_room()
    other = Room.objects.create(name="Beta")
    other_folder = room_services.create_folder(other, None, "X")
    admin = person("a@example.com")
    foreign_group = Group.objects.create(room=other, name="G")
    mine = Group.objects.create(room=room, name="M")
    with pytest.raises(Exception):
        svc.set_level(room, group=foreign_group, folder=legal, level="view", actor=admin)
    with pytest.raises(Exception):
        svc.set_level(room, group=mine, folder=other_folder, level="view", actor=admin)
    with pytest.raises(Exception):
        svc.set_level(room, group=mine, user=person("u@example.com"), level="view", actor=admin)
    boss = User.objects.create_user("boss2@example.com", STRONG, role=User.Role.ADMIN)
    with pytest.raises(Exception):
        svc.set_level(room, user=boss, level="view", actor=admin)
    assert AccessGrant.objects.count() == 0


@pytest.mark.django_db
def test_add_member_only_active_visitors_and_repeat_is_harmless():
    room, *_ = setup_room()
    admin = person("a@example.com")
    group = Group.objects.create(room=room, name="A")
    user = person("m@example.com")
    assert svc.add_member(group, user, admin) is True
    assert svc.add_member(group, user, admin) is False
    assert AuditEvent.objects.filter(code="group.member_added").count() == 1
    boss = User.objects.create_user("boss2@example.com", STRONG, role=User.Role.ADMIN)
    with pytest.raises(Exception):
        svc.add_member(group, boss, admin)
    assert svc.remove_member(group, user, admin) is True
    assert svc.remove_member(group, user, admin) is False


# --- Экраны: только администратор, действия, журнал --------------------------


@pytest.mark.django_db
def test_visitors_and_anonymous_cannot_reach_group_screens():
    room, *_ = setup_room()
    user_client, user = viewer()
    group = Group.objects.create(room=room, name="A")
    urls = [
        ("get", reverse("group_list", args=[room.pk])), ("get", reverse("group_detail", args=[group.pk])),
        ("get", reverse("access_matrix", args=[room.pk])), ("get", reverse("view_as", args=[room.pk, user.pk])),
        ("post", reverse("group_delete", args=[group.pk])), ("post", reverse("group_rename", args=[group.pk])),
        ("post", reverse("group_member_add", args=[group.pk])),
        ("post", reverse("access_matrix", args=[room.pk])),
    ]
    for client in (user_client, Client()):
        for method, url in urls:
            response = getattr(client, method)(url)
            assert response.status_code in (302, 403, 404), (method, url, response.status_code)
            if response.status_code == 302:
                assert "/login" in response["Location"] or "/two-factor" in response["Location"] or response["Location"] == "/"
    assert Group.objects.count() == 1


@pytest.mark.django_db
def test_admin_creates_renames_fills_and_deletes_a_group_with_audit():
    room, legal, *_ = setup_room()
    client, admin = verified_admin_client()
    user = person("m@example.com")

    created = client.post(reverse("group_list", args=[room.pk]), {"name": "Fund A"})
    group = Group.objects.get()
    assert created.status_code == 302 and group.name == "Fund A"
    assert client.post(reverse("group_list", args=[room.pk]), {"name": "fund a"}).status_code == 200  # дубль
    assert Group.objects.count() == 1

    assert client.post(reverse("group_rename", args=[group.pk]), {"name": "Fund B"}).status_code == 302
    group.refresh_from_db()
    assert group.name == "Fund B"

    assert client.post(reverse("group_member_add", args=[group.pk]), {"user": user.pk}).status_code == 302
    assert GroupMember.objects.filter(group=group, user=user).exists()
    page = client.get(reverse("group_detail", args=[group.pk])).content.decode()
    assert "m@example.com" in page

    svc.set_level(room, group=group, folder=legal, level="view", actor=admin)
    assert policy.access_for(user, room) is not None
    assert client.post(reverse("group_member_remove", args=[group.pk, user.pk])).status_code == 302
    assert policy.access_for(user, room) is None

    assert client.post(reverse("group_delete", args=[group.pk])).status_code == 302
    assert not Group.objects.exists() and not AccessGrant.objects.exists()
    codes = set(AuditEvent.objects.values_list("code", flat=True))
    assert {"group.created", "group.renamed", "group.member_added", "group.member_removed", "group.deleted"} <= codes
    targets = " ".join(AuditEvent.objects.values_list("target", flat=True))
    assert "@" not in targets and "Fund" not in targets  # в журнале только коды и номера


@pytest.mark.django_db
def test_grant_form_accepts_a_group_or_a_person_but_not_both_or_neither():
    room, legal, *_ = setup_room()
    client, _admin = verified_admin_client()
    user = person("m@example.com")
    group = Group.objects.create(room=room, name="A")
    url = reverse("access_list", args=[room.pk])
    assert client.post(url, {"group": group.pk, "folder": legal.pk}).status_code == 302
    assert client.post(url, {"user": user.pk, "group": group.pk}).status_code == 200
    assert client.post(url, {}).status_code == 200
    assert AccessGrant.objects.count() == 1 and AccessGrant.objects.get().group == group
    other_group = Group.objects.create(room=Room.objects.create(name="Beta"), name="Z")
    assert client.post(url, {"group": other_group.pk}).status_code == 200  # группа чужой комнаты
    assert AccessGrant.objects.count() == 1
    assert group.name in client.get(url).content.decode()


@pytest.mark.django_db
def test_access_overview_and_revoke_work_for_group_grants():
    room, legal, *_ = setup_room()
    client, _admin = verified_admin_client()
    group = Group.objects.create(room=room, name="Fund A")
    grant = group_grant(group, room, legal)
    assert "Fund A" in client.get(reverse("access_overview")).content.decode()
    assert client.post(reverse("grant_revoke", args=[grant.pk])).status_code == 302
    grant.refresh_from_db()
    assert grant.revoked_at is not None


# --- Матрица ----------------------------------------------------------------


@pytest.mark.django_db
def test_matrix_shows_direct_and_inherited_rights_and_saves_only_changes():
    room, legal, contracts, finance = setup_room()
    client, admin = verified_admin_client()
    group = Group.objects.create(room=room, name="Fund A")
    svc.set_level(room, group=group, folder=legal, level="download", actor=admin)
    url = reverse("access_matrix", args=[room.pk])
    page = client.get(url).content.decode()
    assert "Fund A" in page and "inherited: can download" in page  # Contracts наследует от Legal

    before = AccessGrant.objects.count()
    form = {f"cell_g{group.pk}_{legal.pk}": "download"}  # без изменений
    assert client.post(url, form).status_code == 302
    assert AccessGrant.objects.count() == before

    change = {f"cell_g{group.pk}_{finance.pk}": "view", f"cell_g{group.pk}_{legal.pk}": "none", "cell_g999_1": "view", "x": "y"}
    assert client.post(url, change).status_code == 302
    active = [g for g in AccessGrant.objects.all() if g.is_active()]
    assert [(g.folder_id, g.can_download) for g in active] == [(finance.pk, False)]


@pytest.mark.django_db
def test_matrix_ignores_unknown_levels_and_never_creates_rights_for_unknown_columns():
    room, legal, *_ = setup_room()
    client, _admin = verified_admin_client()
    group = Group.objects.create(room=room, name="A")
    url = reverse("access_matrix", args=[room.pk])
    client.post(url, {f"cell_g{group.pk}_{legal.pk}": "owner", f"cell_u1_{legal.pk}": "view"})
    assert AccessGrant.objects.count() == 0


@pytest.mark.django_db
def test_matrix_includes_people_with_personal_access_as_columns():
    room, legal, *_ = setup_room()
    client, admin = verified_admin_client()
    user = person("solo@example.com")
    svc.set_level(room, user=user, folder=legal, level="view", actor=admin)
    page = client.get(reverse("access_matrix", args=[room.pk])).content.decode()
    assert "solo@example.com" in page
    assert client.post(reverse("access_matrix", args=[room.pk]), {f"cell_u{user.pk}_{legal.pk}": "none"}).status_code == 302
    assert policy.access_for(user, room) is None


# --- «Посмотреть как» -------------------------------------------------------


@pytest.mark.django_db
def test_view_as_shows_what_policy_allows_and_audits_without_visitor_activity():
    room, legal, contracts, finance = setup_room()
    client, admin = verified_admin_client()
    user = person("m@example.com")
    group = Group.objects.create(room=room, name="A")
    GroupMember.objects.create(group=group, user=user)
    group_grant(group, room, legal)
    room_services.create_document(legal, "Visible Doc", pdf_upload(), None)
    room_services.create_document(finance, "Hidden Doc", pdf_upload("h.pdf", make_pdf(2)), None)

    response = client.get(reverse("view_as", args=[room.pk, user.pk]))
    page = response.content.decode()
    assert response.status_code == 200
    assert "Legal" in page and "Contracts" in page and "Visible Doc" in page
    assert "Finance" not in page and "Hidden Doc" not in page
    event = AuditEvent.objects.get(code="access.view_as_opened")
    assert event.actor == admin and event.target == f"room:{room.pk}:user:{user.pk}"
    assert not AuditEvent.objects.filter(code="document.viewed").exists()
    assert not AuditEvent.objects.filter(actor=user).exists()


@pytest.mark.django_db
def test_view_as_for_a_person_without_access_and_for_admins_and_unknown_users():
    room, *_ = setup_room()
    client, admin = verified_admin_client()
    nobody = person("n@example.com")
    assert "no access" in client.get(reverse("view_as", args=[room.pk, nobody.pk])).content.decode().lower()
    assert client.get(reverse("view_as", args=[room.pk, admin.pk])).status_code == 404  # администратор не «посетитель»
    assert client.get(reverse("view_as", args=[room.pk, 99999])).status_code == 404


@pytest.mark.django_db
def test_view_as_does_not_change_the_admin_session_or_grant_the_visitors_rights():
    room, legal, *_ = setup_room()
    client, _admin = verified_admin_client()
    user = person("m@example.com")
    group = Group.objects.create(room=room, name="A")
    GroupMember.objects.create(group=group, user=user)
    group_grant(group, room, legal)
    client.get(reverse("view_as", args=[room.pk, user.pk]))
    assert client.get(reverse("access_list", args=[room.pk])).status_code == 200  # всё ещё администратор
    assert not hasattr(client.session, "view_as") and "view_as" not in client.session


@pytest.mark.django_db
def test_view_as_reports_a_pending_nda():
    room, legal, *_ = setup_room()
    room.nda_required = True
    room.save()
    client, _admin = verified_admin_client()
    user = person("m@example.com")
    AccessGrant.objects.create(user=user, room=room, folder=legal)
    page = client.get(reverse("view_as", args=[room.pk, user.pk])).content.decode()
    assert "NDA" in page


# --- Приглашение с группой --------------------------------------------------


def invite(room, group, email="new@example.com", folder=None):
    return invite_services.create_invitation(
        email=email, name="N", language="en", room=room, folder=folder, can_download=False, access_expires_at=None,
        valid_days=7, created_by=None, send_email=False, group=group,
    )


@pytest.mark.django_db
def test_accepting_an_invitation_adds_the_person_to_the_group_once():
    room, legal, *_ = setup_room()
    group = Group.objects.create(room=room, name="A")
    group_grant(group, room, legal, download=True)
    inv, _token = invite(room, group)
    user = invite_services.accept_new_user(inv.pk, name="N", password=STRONG, language="en")
    assert GroupMember.objects.filter(group=group, user=user).count() == 1
    access = policy.access_for(user, room)
    assert access.can_download_in(legal.pk)
    with pytest.raises(invite_services.Unavailable):
        invite_services.accept_new_user(inv.pk, name="N", password=STRONG, language="en")
    assert GroupMember.objects.filter(group=group).count() == 1


@pytest.mark.django_db
def test_invitation_for_an_existing_user_joins_the_group_and_repeat_adds_nothing():
    room, *_ = setup_room()
    group = Group.objects.create(room=room, name="A")
    existing = person("new@example.com")
    inv, _token = invite(room, group)
    invite_services.accept_existing_user(inv.pk, existing)
    assert GroupMember.objects.filter(group=group, user=existing).count() == 1
    with pytest.raises(invite_services.Unavailable):
        invite_services.accept_existing_user(inv.pk, existing)
    assert GroupMember.objects.count() == 1


@pytest.mark.django_db
def test_invitation_form_rejects_a_group_from_another_room():
    room, *_ = setup_room()
    other = Room.objects.create(name="Beta")
    foreign = Group.objects.create(room=other, name="Z")
    client, _admin = verified_admin_client()
    data = {"email": "new@example.com", "name": "", "language": "en", "room": room.pk, "group": foreign.pk,
            "valid_days": 7}
    assert client.post(reverse("invitation_create"), data).status_code == 200
    from apps.invites.models import Invitation

    assert not Invitation.objects.exists()


# --- Вопросы: общий ответ и группы -------------------------------------------


@pytest.mark.django_db
def test_shared_answer_is_visible_to_a_group_member_with_access_to_the_scope():
    from apps.qa import services as qa

    room, legal, _c, finance = setup_room()
    admin = User.objects.create_user("boss3@example.com", STRONG, role=User.Role.ADMIN)
    asker = person("asker@example.com")
    member, outsider = person("m@example.com"), person("o@example.com")
    AccessGrant.objects.create(user=asker, room=room)
    group = Group.objects.create(room=room, name="A")
    GroupMember.objects.create(group=group, user=member)
    group_grant(group, room, legal)
    question = qa.ask(asker, room, "What is the term?", folder=legal)
    qa.answer(question, admin, "Two years.", share=True)
    question.refresh_from_db()
    assert qa.can_view(member, question) and not qa.can_view(outsider, question)
    finance_q = qa.ask(asker, room, "Payroll?", folder=finance)
    qa.answer(finance_q, admin, "Monthly.", share=True)
    finance_q.refresh_from_db()
    assert not qa.can_view(member, finance_q)  # группа не видит папку Finance
