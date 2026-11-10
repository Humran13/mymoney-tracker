"""Phase 11 Priority 3: Back buttons on screens that previously had no way
back except the browser's own back button. Covers a representative sample
across apps (a plain form, a form with a sidebar, and a detail page), not
every single template -- the same back_link.html partial and back_url_name
pattern is used everywhere else too (see templates/base/_back_link.html).
"""

from decimal import Decimal

from apps.accounts.models import FinancialAccount
from apps.debts.services import create_borrowing


def test_back_link_on_account_form_returns_to_account_list(signed_in_page, live_server):
    page, user, account = signed_in_page
    page.goto(f"{live_server.url}/accounts/new/")
    back = page.get_by_role("link", name="Back")
    assert back.is_visible()
    back.click()
    page.wait_for_load_state("networkidle")
    assert page.url.rstrip("/") == f"{live_server.url}/accounts"


def test_back_link_on_transaction_form_returns_to_transaction_list(signed_in_page, live_server):
    page, user, account = signed_in_page
    page.goto(f"{live_server.url}/transactions/new/")
    back = page.get_by_role("link", name="Back")
    assert back.is_visible()
    back.click()
    page.wait_for_load_state("networkidle")
    assert page.url.rstrip("/") == f"{live_server.url}/transactions"


def test_back_link_on_borrowing_detail_returns_to_debts_dashboard(signed_in_page, live_server, db):
    page, user, account = signed_in_page
    borrowing = create_borrowing(
        user=user, lender_name="Frank", amount=Decimal("10000.00"),
        date_borrowed="2026-01-01", receiving_account=account,
    )
    page.goto(f"{live_server.url}/debts/borrowings/{borrowing.pk}/")
    back = page.get_by_role("link", name="Back")
    assert back.is_visible()
    back.click()
    page.wait_for_load_state("networkidle")
    assert page.url.rstrip("/") == f"{live_server.url}/debts"


def test_back_link_warns_before_discarding_unsaved_form_input(signed_in_page, live_server):
    """form-guard's back-guard.js: typing into a form and then clicking Back
    must trigger a native confirm() dialog before actually navigating away."""
    page, user, account = signed_in_page
    page.goto(f"{live_server.url}/accounts/new/")
    page.fill("#id_name", "Some half-typed account name")

    dialog_seen = []
    page.once("dialog", lambda dialog: (dialog_seen.append(dialog.message), dialog.dismiss()))
    page.get_by_role("link", name="Back").click()
    page.wait_for_timeout(200)

    assert dialog_seen, "expected a confirm() dialog warning about unsaved changes"
    # Dismissed above, so navigation must NOT have happened.
    assert "/accounts/new" in page.url
