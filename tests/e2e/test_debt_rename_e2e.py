import datetime
from decimal import Decimal

import pytest

from apps.accounts.services import account_balance
from apps.debts import services as debt_services

from .conftest import submit_and_wait

TODAY = datetime.date.today()


def _seed(user, account, name="Bartholomew Wanyama Kiprotich Junior"):
    debt_services.create_borrowing(
        user=user, lender_name="Dakka", contact="", amount=Decimal("20000"), date_borrowed=TODAY,
        notes="", debt_type="cash_loan", receiving_account=account, due_date=None, interest_amount=Decimal("0"),
    )
    debt_services.create_lending(
        user=user, borrower_name="DAKA", contact="", amount=Decimal("104000"), date_lent=TODAY, notes="",
        source_account=account, due_date=None, interest_amount=Decimal("0"),
    )
    debt_services.create_lending(
        user=user, borrower_name=name, contact="", amount=Decimal("1000"), date_lent=TODAY, notes="",
        source_account=account, due_date=None, interest_amount=Decimal("0"),
    )


@pytest.mark.parametrize("width", [320, 375, 1100])
def test_rename_then_pay(signed_in_page, live_server, width):
    page, user, account = signed_in_page
    _seed(user, account)
    balance = account_balance(account)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server.url}/debts/")
    assert page.get_by_role("link", name="Pay").count() == 0
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    page.locator('[data-person="Dakka"] [data-testid="edit-person"]').click()
    page.wait_for_load_state()
    assert "1 transaction" in page.locator('[data-testid="rename-scope"]').inner_text()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.fill("#id_name", "DAKA")
    submit_and_wait(page)
    page.wait_for_url("**/debts/")

    assert page.locator('[data-person="DAKA"]').count() == 2
    page.get_by_role("link", name="Pay").first.click()
    page.wait_for_load_state()
    assert "20,000" in page.content()
    submit_and_wait(page)
    submit_and_wait(page)
    page.wait_for_url("**/debts/")
    assert page.locator('[data-person="DAKA"]').count() == 1
    assert "84,000" in page.locator('[data-person="DAKA"]').inner_text()
    assert account_balance(account) == balance
