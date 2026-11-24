from django.utils import timezone

from apps.accounting.models import HeldFund, InventoryLot, InventorySale, ReimbursementClaim

from .conftest import submit_and_wait


def test_agency_and_resale_workflows_are_usable_on_mobile(signed_in_page, live_server):
    page, user, account = signed_in_page
    today = timezone.localdate().isoformat()
    page.set_viewport_size({"width": 375, "height": 812})

    page.goto(f"{live_server.url}/accounting/held/new/")
    page.fill("#id_owner_name", "Community Group")
    page.fill("#id_purpose", "Charity collection")
    submit_and_wait(page)
    fund = HeldFund.objects.get(user=user)
    assert "Outstanding funds held" in page.inner_text("body")

    page.goto(f"{live_server.url}/accounting/held/{fund.pk}/movement/new/?kind=receipt")
    page.select_option("#id_account", str(account.pk))
    page.fill("#id_amount", "500000")
    page.fill("#id_fee_amount", "1000")
    page.select_option("#id_fee_bearer", "owner")
    page.fill("#id_date", today)
    submit_and_wait(page)
    assert "UGX 499,000" in page.inner_text("body")

    page.goto(f"{live_server.url}/accounting/reimbursements/new/")
    page.fill("#id_responsible_party", "NGO")
    page.select_option("#id_account", str(account.pk))
    page.fill("#id_purchase_amount", "80000")
    page.fill("#id_fee_amount", "0")
    page.fill("#id_purchase_date", today)
    page.fill("#id_description", "Event supplies")
    submit_and_wait(page)
    assert ReimbursementClaim.objects.filter(user=user).count() == 1
    assert "Outstanding" in page.inner_text("body")

    page.goto(f"{live_server.url}/accounting/inventory/new/")
    page.fill("#id_product_name", "Imported phones")
    page.select_option("#id_source", "international")
    page.select_option("#id_account", str(account.pk))
    page.fill("#id_acquisition_date", today)
    page.fill("#id_quantity", "10")
    page.fill("#id_purchase_cost", "150000")
    page.fill("#id_shipping_cost", "30000")
    page.fill("#id_customs_cost", "0")
    page.fill("#id_transport_cost", "0")
    submit_and_wait(page)
    lot = InventoryLot.objects.get(user=user)
    assert "UGX 180,000" in page.inner_text("body")

    page.goto(f"{live_server.url}/accounting/inventory/{lot.pk}/sale/new/")
    page.fill("#id_customer", "Customer")
    page.fill("#id_sale_date", today)
    page.fill("#id_quantity", "2")
    page.fill("#id_sale_amount", "50000")
    submit_and_wait(page)
    sale = InventorySale.objects.get(user=user)
    assert sale.outstanding() == sale.sale_amount
    assert "Gross profit" in page.inner_text("body")
    assert page.locator("body").evaluate("el => el.scrollWidth <= window.innerWidth")
