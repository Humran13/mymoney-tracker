from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403

DEBUG = False

# Fail loudly at startup rather than silently running production on the
# insecure dev default — this is the one setting that must never be the
# fallback outside local development.
if SECRET_KEY == "insecure-dev-key-change-me":  # noqa: F405
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY is not set (or still the insecure dev default). "
        "Set a real secret in your .env file before running with prod settings."
    )

# Caddy terminates TLS and proxies to Gunicorn over plain HTTP, setting
# X-Forwarded-Proto. Without this, request.is_secure() is always False
# behind the proxy, and SECURE_SSL_REDIRECT causes an infinite redirect
# loop (verified against a real container — curl -k https://localhost/
# looped with "Location: https://localhost/" until this was added).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# "localhost" is only ever reached from inside the container network (the
# Docker HEALTHCHECK, and Gunicorn's own bind) — the web service has no
# host port mapping, so this can't be hit from outside. Added unconditionally
# so the healthcheck doesn't depend on the operator remembering to list it.
ALLOWED_HOSTS = list(set(ALLOWED_HOSTS + ["localhost", "127.0.0.1"]))  # noqa: F405

SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)  # noqa: F405
# The Docker HEALTHCHECK hits this container directly over plain HTTP
# (Caddy, not Gunicorn, terminates TLS) — without this exemption the
# redirect-to-HTTPS sends it to a port with no TLS listener and it hangs.
SECURE_REDIRECT_EXEMPT = [r"^health/$"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])  # noqa: F405

AXES_ENABLED = True

# -------- Email (password reset, future notifications) --------
# Defaults to the console backend so the app still runs without any SMTP
# provider configured; set SMTP_HOST (and friends) in .env to send real
# email. Never hardcode credentials — they only ever come from the
# environment.
if env("SMTP_HOST", default=""):  # noqa: F405
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = env("SMTP_HOST")  # noqa: F405
    EMAIL_PORT = env.int("SMTP_PORT", default=587)  # noqa: F405
    EMAIL_HOST_USER = env("SMTP_USERNAME", default="")  # noqa: F405
    EMAIL_HOST_PASSWORD = env("SMTP_PASSWORD", default="")  # noqa: F405
    EMAIL_USE_TLS = env.bool("SMTP_USE_TLS", default=True)  # noqa: F405
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="MyMoney Tracker <noreply@localhost>")  # noqa: F405
