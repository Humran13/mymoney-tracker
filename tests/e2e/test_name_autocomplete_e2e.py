import datetime
from decimal import Decimal

import pytest

from apps.debts import services as ds
from apps.debts.models import Borrowing, Lending

from .conftest import submit_and_wait

TODAY = datetime.date.today()
WIDTHS = [320, 375, 1100]


def _seed(user, account):
    for name in ("DAKA", "David", "Daniel", "Brian"):
        ds.create_lending(
            user=user, borrower_name=name, contact="", amount=Decimal("1000"), date_lent=TODAY, notes="",
            source_account=account, due_date=None, interest_amount=Decimal("0"),
        )


def _options(page):
    return [t.strip() for t in page.locator('[role="option"]').all_inner_texts()]


@pytest.mark.parametrize("width", WIDTHS)
def test_suggestions_after_one_letter_and_filter(signed_in_page, live_server, width):
    page, user, account = signed_in_page
    _seed(user, account)
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(f"{live_server.url}/debts/borrowings/new/")
    assert page.locator('[name="contact"]').count() == 0
    field = page.locator("#id_lender_name")
    field.click()
    field.press_sequentially("d")
    page.wait_for_selector('[role="option"]')
    assert _options(page) == ["DAKA", "Daniel", "David"]  # "Brian" excluded; remembered from the lending side
    field.press_sequentially("a")
    field.press_sequentially("k")
    page.wait_for_function("document.querySelectorAll('[role=option]').length === 1")
    assert _options(page) == ["DAKA"]
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_keyboard_selection_and_escape(signed_in_page, live_server):
    page, user, account = signed_in_page
    _seed(user, account)
    page.goto(f"{live_server.url}/debts/borrowings/new/")
    field = page.locator("#id_lender_name")
    field.click()
    field.press_sequentially("da")
    page.wait_for_selector('[role="option"]')
    field.press("ArrowDown")
    field.press("ArrowDown")
    field.press("Enter")  # second option (Daniel)
    assert field.input_value() == "Daniel"
    assert page.locator('[role="listbox"]').is_hidden()
    field.fill("")
    field.press_sequentially("d")
    page.wait_for_selector('[role="option"]')
    field.press("Escape")
    assert page.locator('[role="listbox"]').is_hidden()
    assert field.input_value() == "d"  # Escape never changes the typed text


@pytest.mark.parametrize("width", [320, 375])
def test_mouse_pick_then_save_and_new_name_allowed(signed_in_page, live_server, width):
    page, user, account = signed_in_page
    _seed(user, account)
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(f"{live_server.url}/debts/borrowings/new/")
    field = page.locator("#id_lender_name")
    field.press_sequentially("dak")
    page.locator('[role="option"]').first.click()
    assert field.input_value() == "DAKA"
    page.fill("#id_amount", "20000")
    page.select_option("#id_receiving_account", index=1)
    submit_and_wait(page)
    assert Borrowing.objects.get().lender_name == "DAKA"

    page.goto(f"{live_server.url}/debts/lendings/new/")
    page.fill("#id_borrower_name", "Totally New Person")  # nothing forced
    page.fill("#id_amount", "500")
    page.select_option("#id_source_account", index=1)
    submit_and_wait(page)
    assert Lending.objects.filter(borrower_name="Totally New Person").exists()


def test_pay_offset_after_picking_remembered_name(signed_in_page, live_server):
    page, user, account = signed_in_page
    ds.create_lending(
        user=user, borrower_name="DAKA", contact="", amount=Decimal("104000"), date_lent=TODAY, notes="",
        source_account=account, due_date=None, interest_amount=Decimal("0"),
    )
    page.goto(f"{live_server.url}/debts/borrowings/new/")
    page.locator("#id_lender_name").press_sequentially("d")
    page.locator('[role="option"]').first.click()
    page.fill("#id_amount", "20000")
    page.select_option("#id_receiving_account", index=1)
    submit_and_wait(page)
    page.goto(f"{live_server.url}/debts/")
    assert page.locator('[data-person="DAKA"]').count() == 2  # one card per side, not duplicated people
    page.get_by_role("link", name="Pay", exact=True).first.click()
    page.wait_for_load_state()
    assert "20,000" in page.content()
