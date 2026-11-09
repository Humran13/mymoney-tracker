"""
Settings for running the automated test suite without requiring a running
PostgreSQL server. Never used for local dev or production — those stay on
PostgreSQL (see dev.py / prod.py) per the project's documented stack.
"""
from .base import *  # noqa: F401,F403

DEBUG = False

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "test_db.sqlite3",  # noqa: F405
    }
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
AXES_ENABLED = False
