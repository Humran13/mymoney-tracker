import os
import uuid
from decimal import Decimal

import pytest

# pytest-playwright's sync wrapper starts an event loop in the test thread.
# Any Django ORM call made directly from a test body (as opposed to through
# a real HTTP request handled by live_server's own thread) then trips
# Django's "no sync DB access from an async context" guard, even though
# nothing here is actually async. This is a known friction point between
# pytest-playwright and pytest-django; the sanctioned escape hatch Django
# provides for exactly this situation is DJANGO_ALLOW_ASYNC_UNSAFE. It only
# affects this test process, never the running application.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")

from apps.accounts.models import FinancialAccount
from apps.users.models import User

PASSWORD = "StrongPass123!"


def unique_email(prefix="user"):
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def submit_and_wait(page, selector="#submit-btn"):
    """Click a submit button and robustly wait for the resulting navigation.

    `page.wait_for_load_state("networkidle")` alone is not reliable here:
    WebKit in particular can resolve it against the *current* page before a
    click-triggered form submission's navigation has even started, so a
    follow-up assertion on the post-submit page sometimes runs too early.
    `expect_navigation` blocks until the navigation itself actually happens.
    """
    with page.expect_navigation():
        page.click(selector)
    page.wait_for_load_state("networkidle")


@pytest.fixture
def ui_login(page, live_server):
    """Logs a user in through the real login form (so 'login' itself stays
    covered by every test that uses this), returning (user, login_fn) so
    callers can create whatever user they need first."""

    def _login(email, password=PASSWORD):
        page.goto(f"{live_server.url}/login/")
        page.fill("#id_username", email)
        page.fill("#id_password", password)
        submit_and_wait(page)

    return _login


@pytest.fixture
def seeded_user(db):
    """A user with first-time setup already done and one funded account —
    for tests whose focus is NOT registration/onboarding themselves."""
    user = User.objects.create_user(email=unique_email(), password=PASSWORD)
    user.profile.first_time_setup_complete = True
    user.profile.save()
    account = FinancialAccount.objects.create(
        user=user, name="Cash Wallet", account_type="cash", opening_balance=Decimal("500000.00")
    )
    return user, account


@pytest.fixture
def signed_in_page(seeded_user, live_server, page, ui_login):
    user, account = seeded_user
    ui_login(user.email)
    return page, user, account
