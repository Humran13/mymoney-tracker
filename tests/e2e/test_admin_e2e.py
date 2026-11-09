from apps.users.models import User

from .conftest import PASSWORD, unique_email


def test_admin_can_view_and_suspend_a_user(db, live_server, page, ui_login):
    staff = User.objects.create_user(email=unique_email("staffadmin"), password=PASSWORD, is_staff=True)
    target = User.objects.create_user(email=unique_email("target"), password=PASSWORD)

    ui_login(staff.email)
    page.goto(f"{live_server.url}/admin-panel/")
    assert "Administrator Panel" in page.content()

    page.goto(f"{live_server.url}/admin-panel/users/")
    page.fill('input[name="q"]', target.email)
    page.keyboard.press("Enter")
    page.wait_for_load_state("networkidle")
    assert target.email in page.content()

    page.click(f"text={target.email}")
    page.wait_for_load_state("networkidle")
    page.click('button:has-text("Suspend")')
    page.wait_for_load_state("networkidle")
    assert "has been suspended" in page.content() or "Suspended" in page.content()

    target.refresh_from_db()
    assert target.is_suspended is True


def test_non_staff_user_is_blocked_from_admin_panel(db, live_server, page, ui_login):
    regular = User.objects.create_user(email=unique_email("regular"), password=PASSWORD)
    ui_login(regular.email)

    page.goto(f"{live_server.url}/admin-panel/")
    page.wait_for_load_state("networkidle")
    assert "Administrator Panel" not in page.content()
