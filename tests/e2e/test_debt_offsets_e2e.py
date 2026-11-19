import datetime
import uuid
from decimal import Decimal

import pytest

from apps.debts import services as debt_services
from apps.accounts.services import account_balance

from .conftest import submit_and_wait

TODAY = datetime.date.today()


def _seed_john(user, account, long_name=""):
    name = long_name or "John"
    debt_services.create_borrowing(
        user=user, lender_name=f"  {name.upper()} ", contact="", amount=Decimal("100000"),
        date_borrowed=TODAY - datetime.timedelta(days=2), notes="transport",
        debt_type="cash_loan", receiving_account=account, due_date=None, interest_amount=Decimal("0"),
    )
    debt_services.create_borrowing(
        user=user, lender_name=name, contact="", amount=Decimal("400000"), date_borrowed=TODAY,
        notes="school fees", debt_type="cash_loan", receiving_account=account, due_date=None, interest_amount=Decimal("0"),
    )
    debt_services.create_lending(
        user=user, borrower_name=name, contact="", amount=Decimal("300000"), date_lent=TODAY, notes="",
        source_account=account, due_date=None, interest_amount=Decimal("0"),
    )


def test_pay_button_offsets_with_confirmation_and_keeps_history(signed_in_page, live_server):
    page, user, account = signed_in_page
    _seed_john(user, account)
    balance = account_balance(account)

    page.goto(f"{live_server.url}/debts/")
    assert page.locator('[data-person="John"]').count() == 2  # one under Lenders, one under Borrowers
    page.locator('[data-person="John"]').first.locator("summary").click()
    assert "transport" in page.content() and "school fees" in page.content()

    page.get_by_role("link", name="Pay", exact=True).first.click()
    page.wait_for_load_state()
    assert "300,000" in page.content()
    submit_and_wait(page)  # Review offset
    assert page.locator('[data-testid="settle-confirmation"]').is_visible()
    submit_and_wait(page)  # Confirm offset

    page.wait_for_url("**/debts/")
    assert page.locator('[data-person="John"]').count() == 1  # only the remaining debt of 200,000
    assert "200,000" in page.locator('[data-person="John"]').inner_text()
    page.locator('[data-testid="settlement-history"] summary').click()
    assert "no account balance changed" in page.content()
    assert account_balance(account) == balance


@pytest.mark.parametrize("width", [320, 375])
def test_debts_dashboard_has_no_horizontal_overflow(signed_in_page, live_server, width):
    page, user, account = signed_in_page
    _seed_john(user, account, long_name="Bartholomew Wanyama Kiprotich Junior Senior")
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(f"{live_server.url}/debts/")
    page.locator("details summary").first.click()
    assert page.evaluate("document.documentElement.scrollWidth") <= width + 2


def test_undo_settlement_restores_balances_and_keeps_history(signed_in_page, live_server):
    from apps.debts import settlements

    page, user, account = signed_in_page
    _seed_john(user, account)
    settlements.settle_with_person(
        user=user, name="John", amount=Decimal("300000"), date=TODAY, token=uuid.uuid4()
    )
    balance = account_balance(account)

    page.goto(f"{live_server.url}/debts/")
    page.locator('[data-testid="settlement-history"] summary').click()
    page.get_by_role("link", name="Undo").click()
    page.wait_for_load_state()
    assert "owed again" in page.content()
    submit_and_wait(page)  # Confirm undo

    page.wait_for_url("**/debts/")
    page.locator('[data-testid="settlement-history"] summary').click()
    assert page.locator('[data-testid="reversed-badge"]').is_visible()
    assert page.locator('[data-testid="reversal-entry"]').is_visible()
    assert page.get_by_role("link", name="Undo").count() == 0
    assert "500,000" in page.locator('[data-person="John"]').first.inner_text()
    assert "300,000" in page.locator('[data-person="John"]').last.inner_text()
    assert account_balance(account) == balance


def test_missing_contact_needs_confirmation_before_pay(signed_in_page, live_server):
    page, user, account = signed_in_page
    debt_services.create_borrowing(
        user=user, lender_name="Ann", contact="", amount=Decimal("50000"), date_borrowed=TODAY, notes="",
        debt_type="cash_loan", receiving_account=account, due_date=None, interest_amount=Decimal("0"),
    )
    debt_services.create_lending(
        user=user, borrower_name="ann ", contact="0772 000", amount=Decimal("20000"), date_lent=TODAY, notes="",
        source_account=account, due_date=None, interest_amount=Decimal("0"),
    )
    page.goto(f"{live_server.url}/debts/")
    assert page.get_by_role("link", name="Pay", exact=True).count() == 0
    page.locator('[data-testid="confirm-identity"]').first.click()
    page.wait_for_load_state()
    submit_and_wait(page)  # Yes, same person
    page.wait_for_url("**/debts/")
    assert page.get_by_role("link", name="Pay", exact=True).count() == 2


@pytest.mark.parametrize("width", [320, 375])
def test_identity_and_history_have_no_overflow_on_mobile(signed_in_page, live_server, width):
    from apps.debts import settlements

    page, user, account = signed_in_page
    _seed_john(user, account, long_name="Bartholomew Wanyama Kiprotich Junior Senior")
    settlements.settle_with_person(
        user=user, name="Bartholomew Wanyama Kiprotich Junior Senior", amount=Decimal("1000"), date=TODAY, token=uuid.uuid4()
    )
    page.set_viewport_size({"width": width, "height": 800})
    for path in ("/debts/", ):
        page.goto(f"{live_server.url}{path}")
        page.locator('[data-testid="settlement-history"] summary').click()
        assert page.evaluate("document.documentElement.scrollWidth") <= width + 2
