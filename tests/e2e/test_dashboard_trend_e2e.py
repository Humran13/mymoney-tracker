def test_changing_period_updates_chart_title_and_persists_on_reload(signed_in_page, live_server):
    page, user, account = signed_in_page
    page.goto(live_server.url + "/")
    page.wait_for_load_state("networkidle")

    page.select_option("#trendPeriod", "12m")
    page.wait_for_function("document.getElementById('trendTitle').textContent.includes('12 Months')")

    page.reload()
    page.wait_for_load_state("networkidle")
    assert page.locator("#trendPeriod").input_value() == "12m"
    assert "12 Months" in page.locator("#trendTitle").inner_text()


def test_custom_range_apply_updates_chart(signed_in_page, live_server):
    page, user, account = signed_in_page
    page.goto(live_server.url + "/")
    page.wait_for_load_state("networkidle")

    page.select_option("#trendPeriod", "custom")
    page.fill("#trendStart", "2026-01-01")
    page.fill("#trendEnd", "2026-01-10")
    page.click("#trendCustomApply")
    page.wait_for_function(
        "document.getElementById('trendTitle').textContent.includes('01 Jan 2026')"
    )
