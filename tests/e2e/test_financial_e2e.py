from decimal import Decimal

from apps.accounts.models import FinancialAccount

from .conftest import submit_and_wait


def test_create_account_and_see_opening_balance(signed_in_page, live_server):
    page, user, _account = signed_in_page
    page.goto(f"{live_server.url}/accounts/new/")
    page.fill("#id_name", "Bank Account")
    page.select_option("#id_account_type", "bank")
    page.fill("#id_opening_balance", "1000000")
    submit_and_wait(page)
    assert "/accounts/" in page.url
    assert "Bank Account" in page.content()
    assert "1,000,000" in page.content()


def test_add_income_and_expense_update_balance(signed_in_page, live_server):
    page, user, account = signed_in_page

    page.goto(f"{live_server.url}/transactions/new/?kind=income")
    page.select_option("#id_kind", "income")
    page.select_option("#id_account", str(account.pk))
    page.fill("#id_amount", "20000")
    page.fill("#id_description", "Salary top-up")
    submit_and_wait(page)
    assert "/transactions/" in page.url

    page.goto(f"{live_server.url}/transactions/new/")
    page.select_option("#id_kind", "expense")
    page.select_option("#id_account", str(account.pk))
    page.fill("#id_amount", "5000")
    page.fill("#id_description", "Lunch")
    submit_and_wait(page)

    page.goto(f"{live_server.url}/accounts/")
    # 500,000 opening + 20,000 income - 5,000 expense = 515,000
    assert "515,000" in page.content()


def test_mobile_money_fee_deducts_principal_plus_fee(signed_in_page, live_server):
    page, user, account = signed_in_page

    page.goto(f"{live_server.url}/transactions/new/")
    page.select_option("#id_kind", "expense")
    page.select_option("#id_account", str(account.pk))
    page.fill("#id_amount", "50000")
    page.fill("#id_fee_amount", "1000")
    page.fill("#id_description", "Send money via MTN")
    submit_and_wait(page)

    page.goto(f"{live_server.url}/accounts/")
    # 500,000 - 50,000 principal - 1,000 fee = 449,000
    assert "449,000" in page.content()


def test_transfer_between_accounts_preserves_total_minus_fee(signed_in_page, live_server, db):
    page, user, account = signed_in_page
    second = FinancialAccount.objects.create(
        user=user, name="Savings", account_type="savings", opening_balance=Decimal("0.00")
    )

    page.goto(f"{live_server.url}/accounts/transfers/new/")
    page.select_option("#id_from_account", str(account.pk))
    page.select_option("#id_to_account", str(second.pk))
    page.fill("#id_amount", "100000")
    page.fill("#id_fee_amount", "500")
    page.fill("#id_occurred_on", "2026-01-15")
    submit_and_wait(page)
    assert "/accounts/transfers/" in page.url

    page.goto(f"{live_server.url}/accounts/")
    content = page.content()
    assert "399,500" in content  # source: 500,000 - 100,000 - 500 fee
    assert "100,000" in content  # destination received the full transfer amount


def test_transfer_submit_button_disables_to_prevent_duplicate_submission(signed_in_page, live_server, db):
    from apps.accounts.models import Transfer

    page, user, account = signed_in_page
    second = FinancialAccount.objects.create(
        user=user, name="Safe", account_type="cash_safe", opening_balance=Decimal("0.00")
    )

    page.goto(f"{live_server.url}/accounts/transfers/new/")
    page.select_option("#id_from_account", str(account.pk))
    page.select_option("#id_to_account", str(second.pk))
    page.fill("#id_amount", "10000")
    page.fill("#id_occurred_on", "2026-01-15")

    # Fire a rapid double-click entirely inside one synchronous evaluate()
    # call, so neither click's resulting navigation can start until this
    # script returns control to the browser -- this proves form-guard.js's
    # synchronous disable-on-submit without racing real navigation timing.
    submit_count, disabled_after_first_click = page.evaluate(
        """
        () => {
            let count = 0;
            const btn = document.querySelector('#submit-btn');
            btn.form.addEventListener('submit', () => { count++; });
            btn.click();
            const disabledAfterFirst = btn.disabled;
            btn.click();  // no-op: disabled buttons don't dispatch click/submit
            return [count, disabledAfterFirst];
        }
        """
    )
    assert disabled_after_first_click is True
    assert submit_count == 1

    page.wait_for_load_state("networkidle")
    assert Transfer.objects.filter(user=user).count() == 1


def test_editing_a_transaction_recalculates_balance(signed_in_page, live_server):
    page, user, account = signed_in_page

    page.goto(f"{live_server.url}/transactions/new/")
    page.select_option("#id_kind", "expense")
    page.select_option("#id_account", str(account.pk))
    page.fill("#id_amount", "10000")
    page.fill("#id_description", "Groceries")
    submit_and_wait(page)

    page.goto(f"{live_server.url}/transactions/")
    with page.expect_navigation():
        page.click("text=Edit")
    page.wait_for_load_state("networkidle")
    page.fill("#id_amount", "30000")
    submit_and_wait(page)

    page.goto(f"{live_server.url}/accounts/")
    # 500,000 - 30,000 (the corrected amount, not 500,000 - 10,000 - 30,000)
    assert "470,000" in page.content()
