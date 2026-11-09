import datetime

from .conftest import submit_and_wait


def test_create_goal_add_contribution_and_see_progress(signed_in_page, live_server):
    page, user, account = signed_in_page

    page.goto(f"{live_server.url}/goals/new/")
    page.fill("#id_name", "Laptop")
    page.fill("#id_target_amount", "1000000")
    submit_and_wait(page)
    assert "/goals/" in page.url

    page.fill("#id_amount", "250000")
    page.fill("#id_date", datetime.date.today().isoformat())
    submit_and_wait(page)
    assert "250,000" in page.content()

    page.goto(f"{live_server.url}/goals/")
    assert "25%" in page.content()


def test_goal_forecast_shows_insufficient_history_guardrail(signed_in_page, live_server):
    page, user, account = signed_in_page

    page.goto(f"{live_server.url}/goals/new/")
    page.fill("#id_name", "Car")
    page.fill("#id_target_amount", "5000000")
    submit_and_wait(page)

    content = page.content()
    assert "Not enough transaction history" in content


def test_goal_scenario_calculator_renders_hypothetical_result(signed_in_page, live_server):
    page, user, account = signed_in_page

    page.goto(f"{live_server.url}/goals/new/")
    page.fill("#id_name", "Motorbike")
    page.fill("#id_target_amount", "2000000")
    submit_and_wait(page)

    page.fill("#id_increase_income_monthly", "100000")
    submit_and_wait(page, "#scenario-submit-btn")
    # No history yet, so the scenario must say so rather than inventing a date.
    assert "Not enough transaction history" in page.content()


def test_monthly_report_loads_and_csv_downloads(signed_in_page, live_server):
    page, user, account = signed_in_page

    page.goto(f"{live_server.url}/reports/")
    page.wait_for_load_state("networkidle")
    assert "Monthly Report" in page.content()

    with page.expect_download() as download_info:
        page.click("text=CSV")
    download = download_info.value
    assert download.suggested_filename.endswith(".csv")
