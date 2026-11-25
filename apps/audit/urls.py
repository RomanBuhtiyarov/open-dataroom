from django.urls import path

from . import views

urlpatterns = [
    path("audit/", views.audit_list, name="audit_list"),
    path("audit/export.csv", views.audit_export, name="audit_export"),
    path("audit/verify/", views.audit_verify, name="audit_verify"),
    path("audit/anchor.txt", views.audit_anchor, name="audit_anchor"),
]
