"""Debt editing in a real browser: Edit button, per-transaction editor, confirmation,
Add Another Transaction and payment editing, at phone and desktop widths."""

import datetime
from decimal import Decimal

import pytest

from apps.accounts.services import account_balance
from apps.debts import services as ds
from apps.debts.models import Lending, LendingRepayment

from .conftest import submit_and_wait

TODAY = datetime.date.today()
WIDTHS = [320, 375, 1100]


def _lend(user, account, name, amount, days_ago=0):
    return ds.create_lending(
        user=user, borrower_name=name, contact="", amount=Decimal(amount),
        date_lent=TODAY - datetime.timedelta(days=days_ago), notes="", source_account=account,
        due_date=None, interest_amount=Decimal("0"),
    )


def _no_overflow(page):
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize("width", WIDTHS)
def test_edit_lists_transactions_edits_one_with_confirmation(signed_in_page, live_server, width):
    page, user, account = signed_in_page
    first = _lend(user, account, "DAKA", "60000", 5)
    second = _lend(user, account, "DAKA", "44000", 0)  # dated today, so it is inside the balance
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server.url}/debts/")
    page.locator('[data-person="DAKA"] [data-testid="edit-person"]').click()
    page.wait_for_url("**/people/borrower/edit/**")
    assert page.locator('[data-testid="transaction-row"]').count() == 2
    assert _no_overflow(page)

    balance = account_balance(account)
    page.locator('[data-testid="transaction-row"]').nth(1).locator('[data-testid="edit-transaction"]').click()
    page.wait_for_url(f"**/debts/lendings/{second.pk}/edit/")
    assert page.input_value("#id_amount") == "44000.00"
    assert page.locator('input[name="contact"]').count() == 0
    assert _no_overflow(page)

    page.fill("#id_amount", "50000")
    submit_and_wait(page)
    assert "Please confirm these changes" in page.content()
    assert _no_overflow(page)
    assert account_balance(account) == balance  # previewed, not written
    submit_and_wait(page)
    page.wait_for_url(f"**/debts/lendings/{second.pk}/")
    second.refresh_from_db()
    first.refresh_from_db()
    assert second.amount == Decimal("50000.00") and first.amount == Decimal("60000.00")
    assert account_balance(account) == balance - Decimal("6000")


@pytest.mark.parametrize("width", WIDTHS)
def test_add_another_transaction_keeps_the_person(signed_in_page, live_server, width):
    page, user, account = signed_in_page
    _lend(user, account, "DAKA", "60000", 5)
    _lend(user, account, "DAKA", "44000", 1)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server.url}/debts/")
    page.locator('[data-person="DAKA"] [data-testid="edit-person"]').click()
    page.wait_for_url("**/people/borrower/edit/**")
    page.locator('[data-testid="add-another"]').click()
    page.wait_for_url("**/people/borrower/add/**")
    assert "DAKA" in page.locator('[data-testid="add-for"]').inner_text()
    assert page.get_attribute("#id_borrower_name", "readonly") is not None
    assert _no_overflow(page)
    page.fill("#id_amount", "10000")
    page.select_option("#id_source_account", str(account.pk))
    submit_and_wait(page)
    page.wait_for_url("**/people/borrower/edit/**")
    assert page.locator('[data-testid="transaction-row"]').count() == 3
    assert Lending.objects.filter(borrower_name="DAKA").count() == 3


@pytest.mark.parametrize("width", [320, 1100])
def test_edit_a_recorded_payment(signed_in_page, live_server, width):
    page, user, account = signed_in_page
    loan = _lend(user, account, "Solo", "30000", 3)
    ds.record_lending_repayment(
        user=user, lending=loan, principal_amount=Decimal("10000"), interest_amount=Decimal("0"),
        date=TODAY, receiving_account=account, notes="",
    )
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server.url}/debts/")
    page.locator('[data-person="Solo"] [data-testid="edit-person"]').click()
    page.wait_for_url(f"**/debts/lendings/{loan.pk}/edit/")  # one transaction: straight into the editor
    page.locator('[data-testid="payment-history"] [data-testid="edit-payment"]').click()
    page.wait_for_url("**/lending-repayments/**/edit/")
    assert _no_overflow(page)
    page.fill("#id_principal_amount", "15000")
    submit_and_wait(page)
    assert "Please confirm these changes" in page.content()
    submit_and_wait(page)
    page.wait_for_url(f"**/debts/lendings/{loan.pk}/")
    assert LendingRepayment.objects.get().principal_amount == Decimal("15000.00")
    loan.refresh_from_db()
    assert loan.outstanding() == Decimal("15000.00")
