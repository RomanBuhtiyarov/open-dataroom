"""Подписи кодов событий. В журнале хранятся только коды; перевод выполняется при показе."""
from django.utils.translation import gettext_lazy as _

from . import events

LABELS = {
    events.ADMIN_BOOTSTRAPPED: _("First administrator created"),
    events.AUTH_2FA_ENROLLED: _("Two-factor device added"),
    events.AUTH_2FA_VERIFIED: _("Two-factor code accepted"),
    events.AUTH_2FA_FAILED: _("Two-factor code rejected"),
    events.AUTH_2FA_RESET: _("Two-factor devices removed"),
    events.AUTH_PASSWORD_RESET_REQUESTED: _("Password reset requested"),
    events.AUTH_PASSWORD_RESET_COMPLETED: _("Password changed by reset link"),
    events.AUTH_SIGNED_IN: _("Signed in"),
    events.AUTH_SIGNED_OUT: _("Signed out"),
    events.AUTH_SIGN_IN_FAILED: _("Sign-in failed"),
    events.INSTANCE_SETUP_COMPLETED: _("First launch completed"),
    events.INSTANCE_SETTINGS_CHANGED: _("Instance settings changed"),
    events.ROOM_CREATED: _("Room created"),
    events.ROOM_UPDATED: _("Room changed"),
    events.ROOM_TEMPLATE_APPLIED: _("Room template applied"),
    events.FOLDER_CREATED: _("Folder created"),
    events.FOLDER_RENAMED: _("Folder renamed"),
    events.FOLDER_DELETED: _("Folder deleted"),
    events.FOLDER_MOVED: _("Folder moved"),
    events.DOCUMENT_MOVED: _("Document moved"),
    events.DOCUMENT_DELETED: _("Document deleted"),
    events.ROOM_STARTER_APPLIED: _("Starter rooms created"),
    events.DOCUMENT_UPLOADED: _("Document uploaded"),
    events.DOCUMENT_VERSION_ADDED: _("New document version published"),
    events.DOCUMENT_VIEWED: _("Document viewed"),
    events.DOCUMENT_DOWNLOADED: _("Document downloaded"),
    events.DOCUMENT_DOWNLOAD_DENIED: _("Document download refused"),
    events.GRANT_CREATED: _("Access granted"),
    events.GRANT_REVOKED: _("Access revoked"),
    events.NDA_PUBLISHED: _("NDA version published"),
    events.NDA_ACCEPTED: _("NDA accepted"),
    events.INVITATION_CREATED: _("Invitation created"),
    events.INVITATION_RESENT: _("Invitation sent again"),
    events.INVITATION_REVOKED: _("Invitation revoked"),
    events.INVITATION_ACCEPTED: _("Invitation accepted"),
    events.INVITATION_CODE_FAILED: _("Invitation code rejected"),
    events.REQUEST_SUBMITTED: _("Access request submitted"),
    events.REQUEST_CONFIRMED: _("Access request confirmed by email"),
    events.REQUEST_CODE_FAILED: _("Access request code rejected"),
    events.REQUEST_APPROVED: _("Access request approved"),
    events.REQUEST_REJECTED: _("Access request rejected"),
    events.AUDIT_EXPORTED: _("Audit log exported"),
    events.BACKUP_CREATED: _("Backup created"),
    events.BACKUP_RESTORED: _("Instance restored from a backup"),
}

OUTCOME_LABELS = {
    "ok": _("success"),
    "denied": _("denied"),
    "failed": _("failed"),
}


def label(code):
    """Подпись на текущем языке; неизвестный код показывается как есть."""
    return str(LABELS.get(code, code))


def outcome_label(outcome):
    return str(OUTCOME_LABELS.get(outcome, outcome))


def all_codes():
    return sorted(LABELS, key=lambda code: str(LABELS[code]).lower())
