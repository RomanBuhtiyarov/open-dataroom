from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("i18n/set-language/", views.set_language, name="set_language"),
    path("healthz/", views.healthz, name="healthz"),
]
