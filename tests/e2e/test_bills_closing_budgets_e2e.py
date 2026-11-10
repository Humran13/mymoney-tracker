import datetime

from apps.transactions.models import Category

from .conftest import submit_and_wait


def test_recurring_bill_create_and_pay(signed_in_page, live_server, db):
    page, user, account = signed_in_page
    Category.objects.get_or_create(user=None, name="Rent", kind="expense")

    page.goto(f"{live_server.url}/bills/new/")
    page.fill("#id_name", "Rent")
    page.fill("#id_amount", "150000")
    page.select_option("#id_payment_account", str(account.pk))
    page.select_option("#id_frequency", "monthly")
    today = datetime.date.today().isoformat()
    page.fill("#id_next_due_date", today)
    submit_and_wait(page)
    assert "Rent" in page.content()

    with page.expect_navigation():
        page.click("text=Mark paid")
    page.wait_for_load_state("networkidle")
    submit_and_wait(page)
    assert "recorded" in page.content().lower()

    page.goto(f"{live_server.url}/accounts/")
    assert "350,000" in page.content()  # 500,000 - 150,000


def test_daily_closing_close_and_reopen(signed_in_page, live_server):
    page, user, account = signed_in_page

    page.goto(f"{live_server.url}/closing/")
    page.wait_for_load_state("networkidle")
    assert "Open" in page.content()

    with page.expect_navigation():
        page.click('button[name="action"][value="close"]')
    page.wait_for_load_state("networkidle")
    assert "Closed" in page.content()

    with page.expect_navigation():
        page.click('button[name="action"][value="reopen"]')
    page.wait_for_load_state("networkidle")
    assert "Open" in page.content()


def test_budget_tracks_spending(signed_in_page, live_server, db):
    page, user, account = signed_in_page
    category, _ = Category.objects.get_or_create(user=None, name="Transport", kind="expense")

    page.goto(f"{live_server.url}/budgets/new/")
    page.select_option("#id_category", str(category.pk))
    page.fill("#id_month", datetime.date.today().isoformat())
    page.fill("#id_amount", "100000")
    submit_and_wait(page)

    page.goto(f"{live_server.url}/transactions/new/")
    page.select_option("#id_kind", "expense")
    page.select_option("#id_account", str(account.pk))
    page.select_option("#id_category", str(category.pk))
    page.fill("#id_amount", "40000")
    submit_and_wait(page)

    page.goto(f"{live_server.url}/budgets/")
    content = page.content()
    assert "40,000" in content
    assert "40%" in content
