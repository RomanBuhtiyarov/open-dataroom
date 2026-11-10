from django.contrib.auth import views as auth_views
from django.urls import path

from . import passwordreset, twofactor, views
from .forms import EmailAuthenticationForm

urlpatterns = [
    path(
        "login/",
        views.RateLimitedLoginView.as_view(
            template_name="accounts/login.html",
            authentication_form=EmailAuthenticationForm,
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("2fa/", twofactor.two_factor, name="two_factor"),
    path("profile/", views.profile, name="profile"),
    path("password-reset/", passwordreset.password_reset, name="password_reset"),
    path("password-reset/sent/", passwordreset.password_reset_done, name="password_reset_done"),
    path("password-reset/confirm/", passwordreset.password_reset_confirm, name="password_reset_confirm"),
]
