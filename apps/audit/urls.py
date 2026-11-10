from django.urls import path

from . import views

urlpatterns = [
    path("audit/", views.audit_list, name="audit_list"),
    path("audit/export.csv", views.audit_export, name="audit_export"),
]
