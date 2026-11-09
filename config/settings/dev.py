from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = "MyMoney Tracker <noreply@localhost>"

STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"

AXES_ENABLED = env.bool("AXES_ENABLED", default=False)  # noqa: F405
