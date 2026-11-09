from .conftest import submit_and_wait


def test_borrowing_lifecycle(signed_in_page, live_server):
    page, user, account = signed_in_page

    page.goto(f"{live_server.url}/debts/borrowings/new/")
    page.fill("#id_lender_name", "John")
    page.fill("#id_amount", "500000")
    page.fill("#id_date_borrowed", "2026-01-01")
    page.select_option("#id_receiving_account", str(account.pk))
    submit_and_wait(page)
    assert "John" in page.content()
    assert "500,000" in page.content()

    # Partial repayment.
    page.fill("#id_amount", "200000")
    page.fill("#id_date", "2026-01-15")
    page.select_option("#id_paying_account", str(account.pk))
    submit_and_wait(page)
    assert "300,000" in page.content()  # outstanding = 500,000 - 200,000

    page.goto(f"{live_server.url}/debts/")
    assert "300,000" in page.content()


def test_lending_lifecycle(signed_in_page, live_server):
    page, user, account = signed_in_page

    page.goto(f"{live_server.url}/debts/lendings/new/")
    page.fill("#id_borrower_name", "Sarah")
    page.fill("#id_amount", "300000")
    page.fill("#id_date_lent", "2026-01-01")
    page.select_option("#id_source_account", str(account.pk))
    submit_and_wait(page)
    assert "Sarah" in page.content()

    # Repayment received.
    page.fill("#id_principal_amount", "100000")
    page.fill("#id_date", "2026-01-20")
    page.select_option("#id_receiving_account", str(account.pk))
    submit_and_wait(page)
    assert "200,000" in page.content()  # still owed = 300,000 - 100,000

    page.goto(f"{live_server.url}/debts/")
    assert "200,000" in page.content()
