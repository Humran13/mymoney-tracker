from .base import *  # noqa: F401,F403

DEBUG = False

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

EMAIL_BACKEND = env("DJANGO_EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")  # noqa: F405

AXES_ENABLED = True
