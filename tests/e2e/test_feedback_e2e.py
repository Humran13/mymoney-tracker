def test_submit_feedback_and_see_it_in_own_list(signed_in_page, live_server):
    page, user, account = signed_in_page
    page.goto(f"{live_server.url}/feedback/new/")
    page.select_option("#id_category", "ask_for_help")
    page.fill("#id_subject", "How do I merge two accounts?")
    page.fill("#id_description", "Not sure if that's possible.")
    page.click("#submit-btn")
    page.wait_for_load_state("networkidle")
    assert "How do I merge two accounts?" in page.content()

    page.goto(f"{live_server.url}/feedback/")
    assert "How do I merge two accounts?" in page.content()
    assert "Pending" in page.content()


def test_feedback_page_has_no_overflow_on_mobile(signed_in_page, live_server):
    page, user, account = signed_in_page
    page.set_viewport_size({"width": 360, "height": 740})
    page.goto(f"{live_server.url}/feedback/new/")
    page.wait_for_load_state("networkidle")
    body_scroll_width = page.evaluate("document.documentElement.scrollWidth")
    body_client_width = page.evaluate("document.documentElement.clientWidth")
    assert body_scroll_width <= body_client_width + 1
