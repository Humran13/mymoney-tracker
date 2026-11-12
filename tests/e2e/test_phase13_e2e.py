"""Phase 13 browser tests: the monthly comparison card, the clickable warning
into the analysis page, the analysis filters, and the repayment fee field."""

import datetime
from decimal import Decimal

from django.utils import timezone

from apps.accounts.models import FinancialAccount
from apps.debts.services import create_borrowing
from apps.transactions.services import create_transaction

from .conftest import submit_and_wait

TODAY = timezone.localdate()


def _make_expense(account, user, amount):
    create_transaction(
        user=user, account=account, kind="expense", category=None,
        amount=Decimal(amount), fee_amount=Decimal("0.00"), date=TODAY, description="E2E expense",
    )


def _make_income(account, user, amount):
    create_transaction(
        user=user, account=account, kind="income", category=None,
        amount=Decimal(amount), fee_amount=Decimal("0.00"), date=TODAY, description="E2E income",
    )


def test_dashboard_shows_monthly_comparison_and_links_to_the_analysis(signed_in_page, live_server):
    page, user, account = signed_in_page
    _make_income(account, user, "2500000.00")
    _make_expense(account, user, "2900000.00")

    page.goto(live_server.url + "/")
    page.wait_for_load_state("networkidle")

    assert "UGX 2,500,000" in page.inner_text("body")
    assert "UGX 2,900,000" in page.inner_text("body")
    assert "UGX -400,000" in page.inner_text("body")
    assert "exceed your income" in page.inner_text("body")

    page.click("text=See the daily income vs expenses analysis")
    page.wait_for_load_state("networkidle")
    assert "/reports/income-vs-expenses/" in page.url
    assert "Income vs Expenses Analysis" in page.inner_text("h1")


def test_analysis_page_period_filter_and_drilldown(signed_in_page, live_server):
    page, user, account = signed_in_page
    _make_income(account, user, "150000.00")
    _make_expense(account, user, "185000.00")

    page.goto(live_server.url + "/reports/income-vs-expenses/")
    page.wait_for_load_state("networkidle")
    assert "You spent 35,000.00 more than you received." in page.inner_text("body")

    # Switch to a 7-day window: the daily table appears with a drill-down link.
    page.select_option("#preset", "7d")
    submit_and_wait(page)
    assert "Daily breakdown" in page.inner_text("body")

    page.click(f"table a[href='/reports/income-vs-expenses/{TODAY.isoformat()}/']")
    page.wait_for_load_state("networkidle")
    assert "E2E income" in page.inner_text("body")
    assert "E2E expense" in page.inner_text("body")
    assert page.locator("a", has_text="Back").first.is_visible()


def test_month_selector_changes_the_comparison(signed_in_page, live_server):
    page, user, account = signed_in_page
    previous_month_day = TODAY.replace(day=1) - datetime.timedelta(days=1)
    create_transaction(
        user=user, account=account, kind="income", category=None,
        amount=Decimal("700000.00"), fee_amount=Decimal("0.00"), date=previous_month_day, description="old income",
    )
    create_transaction(
        user=user, account=account, kind="expense", category=None,
        amount=Decimal("900000.00"), fee_amount=Decimal("0.00"), date=previous_month_day, description="old expense",
    )
    value = previous_month_day.strftime("%Y-%m")

    page.goto(f"{live_server.url}/?month={value}")
    page.wait_for_load_state("networkidle")
    body = page.inner_text("body")
    assert "UGX 700,000" in body
    assert "UGX 900,000" in body
    assert "UGX -200,000" in body


def test_repayment_form_accepts_a_transaction_fee(signed_in_page, live_server):
    page, user, account = signed_in_page
    proceeds = FinancialAccount.objects.create(
        user=user, name="Loan Proceeds", account_type="bank", opening_balance=Decimal("0.00")
    )
    borrowing = create_borrowing(
        user=user, lender_name="E2E Lender", amount=Decimal("300000.00"),
        date_borrowed=TODAY - datetime.timedelta(days=5), receiving_account=proceeds,
    )

    page.goto(f"{live_server.url}/debts/borrowings/{borrowing.pk}/")
    page.wait_for_load_state("networkidle")
    assert page.locator("#id_fee_amount").is_visible()

    page.fill("#id_amount", "100000.00")
    page.fill("#id_fee_amount", "1500.00")
    page.select_option("#id_paying_account", str(account.pk))
    submit_and_wait(page)
    assert "Repayment recorded." in page.inner_text("body")
    assert "Transaction fee (not part of the debt repaid)" in page.inner_text("body")
    # Debt reduced by the principal only.
    borrowing.refresh_from_db()
    assert borrowing.outstanding() == Decimal("200000.00")


def test_reconciliation_page_is_reachable_and_read_only(signed_in_page, live_server):
    page, user, account = signed_in_page
    page.goto(live_server.url + "/accounts/")
    page.wait_for_load_state("networkidle")
    page.click("text=Review balances")
    page.wait_for_load_state("networkidle")
    assert "/accounts/reconciliation/" in page.url
    assert "Your starting balances" in page.inner_text("h1")
    # Nothing is applied without an explicit confirm step.
    account.refresh_from_db()
    assert account.opening_balance_date is None
