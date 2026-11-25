from django.urls import path

from . import views

urlpatterns = [
    path("questions/", views.question_list, name="question_list"),
    path("questions/ask/", views.question_ask, name="question_ask"),
    path("questions/export.xlsx", views.question_export, name="question_export"),
    path("questions/<int:question_id>/", views.question_detail, name="question_detail"),
    path("questions/<int:question_id>/answer/", views.question_answer, name="question_answer"),
    path("questions/<int:question_id>/assign/", views.question_assign, name="question_assign"),
    path("questions/<int:question_id>/close/", views.question_close, name="question_close"),
    path("questions/<int:question_id>/share/", views.question_share, name="question_share"),
    path("questions/<int:question_id>/attachment/", views.question_attachment, name="question_attachment"),
]
