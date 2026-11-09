from django.urls import path

from . import views

urlpatterns = [
    path("", views.room_list, name="home"),
    path("rooms/new/", views.room_create, name="room_create"),
    path("rooms/<int:room_id>/", views.room_detail, name="room_detail"),
    path("rooms/<int:room_id>/edit/", views.room_edit, name="room_edit"),
    path("rooms/<int:room_id>/template/", views.room_apply_template, name="room_apply_template"),
    path("rooms/<int:room_id>/folders/new/", views.folder_create, name="folder_create"),
    path("folders/<int:folder_id>/rename/", views.folder_rename, name="folder_rename"),
    path("folders/<int:folder_id>/delete/", views.folder_delete, name="folder_delete"),
    path("rooms/<int:room_id>/upload/", views.document_upload, name="document_upload"),
    path("documents/<int:document_id>/", views.document_detail, name="document_detail"),
    path("documents/<int:document_id>/versions/new/", views.version_upload, name="version_upload"),
    path("documents/<int:document_id>/download/", views.document_download, name="document_download"),
    path("rooms/<int:room_id>/access/", views.access_list, name="access_list"),
    path("grants/<int:grant_id>/revoke/", views.grant_revoke, name="grant_revoke"),
    path("rooms/<int:room_id>/nda/", views.nda_accept, name="nda_accept"),
    path("rooms/<int:room_id>/nda/edit/", views.nda_edit, name="nda_edit"),
]
