from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


def normalize_email(email):
    """Единая нормализация адреса: без пробелов по краям, в нижнем регистре."""
    return (email or "").strip().lower()


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email, password, **extra):
        email = normalize_email(email)
        if not email:
            raise ValueError("Email is required.")
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("role", User.Role.VIEWER)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra["role"] = User.Role.ADMIN
        return self._create(email, password, **extra)

    def get_by_natural_key(self, username):
        return self.get(email=normalize_email(username))


class User(AbstractBaseUser):
    """Пользователь экземпляра. Pending/rejected хранятся в заявке (этап 3), не здесь."""

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        SUSPENDED = "suspended", _("Suspended")

    class Role(models.TextChoices):
        ADMIN = "admin", _("Administrator")
        VIEWER = "viewer", _("Viewer")

    email = models.EmailField(_("email address"), max_length=254, unique=True)
    name = models.CharField(_("name"), max_length=150, blank=True)
    email_confirmed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.VIEWER)
    # Предпочитаемый язык интерфейса; пусто = брать язык экземпляра.
    language = models.CharField(max_length=5, blank=True, default="", choices=settings.LANGUAGES)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        constraints = [models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique")]

    def save(self, *args, **kwargs):
        self.email = normalize_email(self.email)
        super().save(*args, **kwargs)

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE

    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN

    def __str__(self):
        return self.email
