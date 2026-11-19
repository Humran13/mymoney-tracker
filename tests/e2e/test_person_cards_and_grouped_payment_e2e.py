import datetime
import uuid
from decimal import Decimal

import pytest

from apps.accounts.models import FinancialAccount
from apps.accounts.services import account_balance
from apps.debts import services as ds
from apps.debts.models import Lending

from .conftest import submit_and_wait

TODAY = datetime.date.today()
WIDTHS = [320, 375, 768, 1100]


def _lend(user, account, name, amount, days_ago=0):
    return ds.create_lending(
        user=user, borrower_name=name, contact="", amount=Decimal(amount),
        date_lent=TODAY - datetime.timedelta(days=days_ago), notes="", source_account=account,
        due_date=None, interest_amount=Decimal("0"),
    )


def _owe(user, account, name, amount):
    return ds.create_borrowing(
        user=user, lender_name=name, contact="", amount=Decimal(amount), date_borrowed=TODAY, notes="",
        debt_type="cash_loan", receiving_account=account, due_date=None, interest_amount=Decimal("0"),
    )


def _click_total(page, name):
    """Real mouse click on the card's total: the stretched link overlays it."""
    box = _card(page, name).locator("p.font-bold").bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


def _card(page, name):
    return page.locator(f'[data-person="{name}"]')


@pytest.mark.parametrize("width", WIDTHS)
def test_card_click_opens_person_or_transaction(signed_in_page, live_server, width):
    page, user, account = signed_in_page
    _lend(user, account, "DAKA", "60000", 5)
    _lend(user, account, "DAKA", "44000", 1)
    solo = _lend(user, account, "Solo", "5000")
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server.url}/debts/")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    # click an empty part of the card (the right-hand total), not the name link
    _click_total(page, "Solo")
    page.wait_for_url(f"**/debts/lendings/{solo.pk}/")

    page.goto(f"{live_server.url}/debts/")
    _click_total(page, "DAKA")
    page.wait_for_url("**/debts/people/borrower/**")
    assert "104,000" in page.locator('[data-testid="person-total"]').inner_text()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize("width", [320, 1100])
def test_edit_and_record_payment_do_not_trigger_card_navigation(signed_in_page, live_server, width):
    page, user, account = signed_in_page
    _lend(user, account, "DAKA", "60000", 5)
    _lend(user, account, "DAKA", "44000", 1)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server.url}/debts/")
    _card(page, "DAKA").locator('[data-testid="edit-person"]').click()
    page.wait_for_url("**/people/borrower/rename/**")
    page.goto(f"{live_server.url}/debts/")
    _card(page, "DAKA").locator('[data-testid="record-payment"]').click()
    page.wait_for_url("**/people/borrower/pay/**")


def test_keyboard_opens_card(signed_in_page, live_server):
    page, user, account = signed_in_page
    solo = _lend(user, account, "Solo", "5000")
    page.goto(f"{live_server.url}/debts/")
    _card(page, "Solo").locator('[data-testid="person-link"]').focus()
    with page.expect_navigation():
        page.keyboard.press("Enter")
    assert page.url.endswith(f"/debts/lendings/{solo.pk}/")


@pytest.mark.parametrize("width", WIDTHS)
def test_daka_grouped_payment_end_to_end(signed_in_page, live_server, width):
    page, user, cash = signed_in_page
    airtel = FinancialAccount.objects.create(
        user=user, name="Airtel Money", account_type="mobile_money_airtel", opening_balance=Decimal("0")
    )
    l1 = _lend(user, cash, "DAKA", "60000", 5)
    l2 = _lend(user, cash, "DAKA", "44000", 1)
    _owe(user, cash, "DAKA", "20000")
    _lend(user, cash, "Other", "10000")
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server.url}/debts/")

    # existing noncash Pay/offset still works
    page.get_by_role("link", name="Pay", exact=True).first.click()
    page.wait_for_load_state()
    page.fill("#id_amount", "20000")
    submit_and_wait(page)
    submit_and_wait(page)
    page.wait_for_url("**/debts/")
    assert "84,000" in _card(page, "DAKA").inner_text()

    # grouped real payment
    _card(page, "DAKA").locator('[data-testid="record-payment"]').click()
    page.wait_for_load_state()
    assert page.input_value("#id_amount") in ("84000.00", "84000")
    page.select_option("#id_account", value=str(airtel.pk))
    submit_and_wait(page)
    text = page.locator('[data-testid="payment-confirmation"]').inner_text()
    assert "40,000" in text and "44,000" in text
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # double click on confirm must still post exactly once
    page.dblclick("#submit-btn")
    page.wait_for_url("**/debts/")
    page.wait_for_load_state("networkidle")

    assert account_balance(airtel) == Decimal("84000.00")
    assert _card(page, "DAKA").count() == 0
    assert _card(page, "Other").count() == 1
    for lending in (l1, l2):
        lending.refresh_from_db()
        assert lending.status == Lending.Status.SETTLED
