import pytest

from .conftest import submit_and_wait

MOBILE_WIDTHS = [320, 360, 390, 430]


def _assert_no_horizontal_overflow(page, width):
    scroll_width = page.evaluate("document.documentElement.scrollWidth")
    # A couple of px of tolerance for scrollbar/rounding quirks, never more.
    assert scroll_width <= width + 2, f"horizontal overflow at {width}px: scrollWidth={scroll_width}"


@pytest.mark.parametrize("width", MOBILE_WIDTHS)
def test_login_page_has_no_overflow_on_mobile(page, live_server, width):
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(f"{live_server.url}/login/")
    page.wait_for_load_state("networkidle")
    _assert_no_horizontal_overflow(page, width)


@pytest.mark.parametrize("width", MOBILE_WIDTHS)
def test_register_page_has_no_overflow_on_mobile(page, live_server, width):
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(f"{live_server.url}/register/")
    page.wait_for_load_state("networkidle")
    _assert_no_horizontal_overflow(page, width)


@pytest.mark.parametrize("width", MOBILE_WIDTHS)
def test_dashboard_has_no_overflow_and_mobile_nav_works(signed_in_page, live_server, width):
    page, user, account = signed_in_page
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(live_server.url + "/")
    page.wait_for_load_state("networkidle")
    _assert_no_horizontal_overflow(page, width)

    # The desktop sidebar must be hidden, and the hamburger must open a working drawer.
    assert not page.locator("aside").is_visible()
    page.click('button[aria-label="Open menu"]')
    drawer_link = page.locator("#mobile-nav-drawer a", has_text="Debts & Loans")
    drawer_link.wait_for(state="visible")
    drawer_link.click()
    page.wait_for_load_state("networkidle")
    assert "/debts/" in page.url
    _assert_no_horizontal_overflow(page, width)


@pytest.mark.parametrize("width", MOBILE_WIDTHS)
def test_quick_add_expense_from_mobile_bar(signed_in_page, live_server, width):
    page, user, account = signed_in_page
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(live_server.url + "/")
    page.wait_for_load_state("networkidle")

    with page.expect_navigation():
        page.click("text=+ Expense")
    page.wait_for_load_state("networkidle")
    assert "/transactions/new/" in page.url
    _assert_no_horizontal_overflow(page, width)

    page.select_option("#id_account", str(account.pk))
    page.fill("#id_amount", "7500")
    page.fill("#id_description", "Boda ride")
    submit_and_wait(page)
    assert "recorded" in page.content().lower()


@pytest.mark.parametrize("width", MOBILE_WIDTHS)
def test_monthly_report_table_scrolls_instead_of_overflowing_page(signed_in_page, live_server, width):
    page, user, account = signed_in_page
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(f"{live_server.url}/reports/")
    page.wait_for_load_state("networkidle")
    _assert_no_horizontal_overflow(page, width)
