from .conftest import submit_and_wait


def test_goods_on_credit_does_not_increase_cash_dashboard_total(signed_in_page, live_server):
    page, user, account = signed_in_page
    page.goto(live_server.url + "/")
    page.wait_for_load_state("networkidle")
    before = page.locator("text=Total available").locator("..").inner_text()

    page.goto(live_server.url + "/debts/borrowings/new/")
    page.wait_for_load_state("networkidle")
    page.select_option("#id_debt_type", "goods_credit")
    page.fill("#id_lender_name", "Tasha Cargo")
    page.fill("#id_amount", "446000")
    page.fill("#id_date_borrowed", "2026-11-11")
    submit_and_wait(page)
    assert "/debts/borrowings/" in page.url

    page.goto(live_server.url + "/")
    page.wait_for_load_state("networkidle")
    after = page.locator("text=Total available").locator("..").inner_text()
    assert before == after


def test_reclassify_flow_requires_preview_before_confirm(signed_in_page, live_server):
    page, user, account = signed_in_page
    page.goto(live_server.url + "/debts/borrowings/new/")
    page.wait_for_load_state("networkidle")
    page.fill("#id_lender_name", "Cash Lender")
    page.fill("#id_amount", "50000")
    page.fill("#id_date_borrowed", "2026-11-11")
    page.select_option("#id_receiving_account", value=str(account.pk))
    submit_and_wait(page)

    page.click("text=Change debt type")
    page.wait_for_load_state("networkidle")
    page.select_option("#id_debt_type", "goods_credit")
    with page.expect_navigation():
        page.click("#submit-btn")
    assert "Preview" in page.content() or "preview" in page.content().lower()

    with page.expect_navigation():
        page.click("#submit-btn")
    page.wait_for_load_state("networkidle")
    assert "Goods on Credit" in page.content()
