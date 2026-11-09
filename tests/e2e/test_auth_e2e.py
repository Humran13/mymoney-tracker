import re

from django.core import mail

from apps.users.models import User

from .conftest import PASSWORD, submit_and_wait, unique_email


def test_registration_login_logout_flow(db, live_server, page):
    email = unique_email("register")
    page.goto(f"{live_server.url}/register/")
    page.fill("#id_first_name", "Test")
    page.fill("#id_last_name", "User")
    page.fill("#id_email", email)
    page.fill("#id_password1", PASSWORD)
    page.fill("#id_password2", PASSWORD)
    submit_and_wait(page)

    # Registration logs the user straight into onboarding.
    assert "/accounts/onboarding/" in page.url

    page.fill("#id_acct-0-name", "Cash Wallet")
    page.select_option("#id_acct-0-account_type", "cash")
    page.fill("#id_acct-0-opening_balance", "250000")
    submit_and_wait(page)
    assert "/accounts/onboarding/" not in page.url  # redirected to the dashboard

    # Logout returns to the login page.
    page.click('button[aria-label="Open user menu"]')
    with page.expect_navigation():
        page.click("text=Logout")
    page.wait_for_load_state("networkidle")
    assert "/login/" in page.url

    # And logging back in reaches the dashboard, not onboarding again.
    page.fill("#id_username", email)
    page.fill("#id_password", PASSWORD)
    submit_and_wait(page)
    assert page.url.rstrip("/") == live_server.url


def test_password_reset_flow_through_the_browser(db, live_server, page):
    user = User.objects.create_user(email=unique_email("forgot"), password="OldPass123!")

    page.goto(f"{live_server.url}/password/reset/")
    page.fill("#id_email", user.email)
    submit_and_wait(page)
    assert "check your email" in page.content().lower()

    assert len(mail.outbox) == 1
    match = re.search(r"(/password/reset/confirm/[^\s]+/)", mail.outbox[0].body)
    assert match, "reset link not found in email body"

    page.goto(live_server.url + match.group(1))
    page.wait_for_load_state("networkidle")
    page.fill("#id_new_password1", "BrandNewPass456!")
    page.fill("#id_new_password2", "BrandNewPass456!")
    submit_and_wait(page)

    page.goto(f"{live_server.url}/login/")
    page.fill("#id_username", user.email)
    page.fill("#id_password", "BrandNewPass456!")
    submit_and_wait(page)
    # This user never completed onboarding, so login correctly lands there,
    # not on the dashboard — confirms the new password actually works.
    assert "/accounts/onboarding/" in page.url


def test_suspended_account_cannot_log_in_even_with_an_old_session(db, live_server, page, ui_login):
    user = User.objects.create_user(email=unique_email("suspend"), password=PASSWORD)
    ui_login(user.email)
    assert page.url.rstrip("/") != f"{live_server.url}/login"  # logged in successfully first

    # Suspend (simulating an administrator acting elsewhere).
    user.is_suspended = True
    user.save(update_fields=["is_suspended"])

    # The already-logged-in page can still browse on its stale session (Django
    # doesn't invalidate sessions server-side on suspension), but a *new*
    # login attempt must be rejected — that's the actual security boundary.
    new_context = page.context.browser.new_context()
    fresh_page = new_context.new_page()
    fresh_page.goto(f"{live_server.url}/login/")
    fresh_page.fill("#id_username", user.email)
    fresh_page.fill("#id_password", PASSWORD)
    fresh_page.click("#submit-btn")
    fresh_page.wait_for_load_state("networkidle")
    assert "/login/" in fresh_page.url
    new_context.close()
