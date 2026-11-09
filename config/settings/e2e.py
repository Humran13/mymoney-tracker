"""
Settings for running a live Django server that Playwright drives as a real
browser. Same SQLite-backed, no-external-dependencies approach as test.py
(so this never touches a real Postgres/dev/prod database), but with
DEBUG=True so `runserver` serves static assets (including the compiled
Tailwind build) the same way it would for everyday local development.
"""
from .test import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]
