from django.contrib.auth import views as auth_views
from django.urls import path

from . import twofactor, views
from .forms import EmailAuthenticationForm

urlpatterns = [
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="accounts/login.html",
            authentication_form=EmailAuthenticationForm,
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("2fa/", twofactor.two_factor, name="two_factor"),
    path("profile/", views.profile, name="profile"),
]
