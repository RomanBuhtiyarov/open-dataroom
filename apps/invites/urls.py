from django.urls import path

from . import admin_views, views

urlpatterns = [
    path("invite/", views.invite_entry, name="invite_entry"),
    path("request-access/", views.request_access, name="request_access"),
    path("request-access/confirm/", views.request_confirm, name="request_confirm"),
    path("invitations/", admin_views.invitation_list, name="invitation_list"),
    path("invitations/new/", admin_views.invitation_create, name="invitation_create"),
    path("invitations/<int:invitation_id>/resend/", admin_views.invitation_resend, name="invitation_resend"),
    path("invitations/<int:invitation_id>/revoke/", admin_views.invitation_revoke, name="invitation_revoke"),
    path("requests/", admin_views.request_list, name="request_list"),
    path("requests/<int:request_id>/", admin_views.request_detail, name="request_detail"),
    path("requests/<int:request_id>/resend-email/", admin_views.request_retry_email, name="request_retry_email"),
]
