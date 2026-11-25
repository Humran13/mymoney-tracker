import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from apps.accounting import services
from apps.accounting.models import (
    AccountingAttachment,
    HeldFund,
    HeldFundEntry,
    ReimbursementClaim,
)
from apps.accounting.reporting import period_summary
from apps.accounts.models import AccountPosting, FinancialAccount
from apps.users.models import User


D = Decimal
TODAY = timezone.localdate()


class AccountingEnhancementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="enhancement@example.com", password="StrongPass123!")
        self.account = FinancialAccount.objects.create(
            user=self.user,
            name="UGX wallet",
            account_type=FinancialAccount.AccountType.MOBILE_MONEY_MTN,
            currency_code="UGX",
            opening_balance=D("1000000"),
            opening_balance_date=TODAY.replace(day=1),
        )
        self.client.force_login(self.user)

    def test_first_receipt_keeps_hidden_idempotency_and_is_atomic(self):
        key = str(uuid.uuid4())
        payload = {
            "owner_name": "Charity Uganda",
            "purpose": "School supplies",
            "amount": "500000",
            "account": self.account.pk,
            "date": TODAY.isoformat(),
            "sender_name": "Donor A",
            "reference": "MM-123",
            "fee_amount": "2000",
            "fee_bearer": "owner",
            "notes": "First receipt",
            "operation_key": key,
        }
        form = self.client.get("/accounting/held/receive/")
        self.assertContains(form, 'type="hidden" name="operation_key"')
        self.assertNotContains(form, ">Operation key<")

        response = self.client.post("/accounting/held/receive/", payload)
        self.assertEqual(response.status_code, 302)
        fund = HeldFund.objects.get()
        entry = HeldFundEntry.objects.get()
        self.assertEqual(entry.sender_name, "Donor A")
        self.assertEqual(entry.reference, "MM-123")
        self.assertEqual(fund.outstanding(), D("498000"))

        duplicate = self.client.post("/accounting/held/receive/", payload)
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(HeldFund.objects.count(), 1)
        self.assertEqual(HeldFundEntry.objects.count(), 1)
        self.assertEqual(AccountPosting.objects.filter(source_type="held_receive").count(), 1)

    def test_cost_basis_is_locked_after_sale_and_realized_cogs_is_stable(self):
        lot = services.save_inventory_lot(
            user=self.user,
            product_name="Headsets",
            supplier="Supplier",
            source="international",
            account=self.account,
            acquisition_date=TODAY,
            quantity=D("2"),
            purchase_cost=D("150000"),
            shipping_cost=D("30000"),
            customs_cost=0,
            transport_cost=0,
            other_cost=0,
            invoice_reference="INV-1",
            notes="",
        )
        sale = services.save_inventory_sale(
            user=self.user,
            lot=lot,
            customer="Company",
            sale_date=TODAY,
            quantity=D("1"),
            sale_amount=D("115000"),
            invoice_reference="SALE-1",
            notes="",
        )
        self.assertEqual(sale.cost_of_goods_sold, D("90000"))
        postings_before = list(AccountPosting.objects.order_by("pk").values("pk", "amount", "object_id"))
        with self.assertRaisesMessage(ValidationError, "cannot be changed after a sale"):
            services.save_inventory_lot(
                user=self.user,
                instance=lot,
                product_name=lot.product_name,
                supplier=lot.supplier,
                source=lot.source,
                account=self.account,
                acquisition_date=TODAY,
                quantity=D("2"),
                purchase_cost=D("170000"),
                shipping_cost=D("30000"),
                customs_cost=0,
                transport_cost=0,
                other_cost=0,
                invoice_reference="INV-1-CORRECTED",
                notes="",
            )
        sale.refresh_from_db()
        lot.refresh_from_db()
        self.assertEqual(sale.cost_of_goods_sold, D("90000"))
        self.assertEqual(lot.landed_cost, D("180000"))
        self.assertEqual(lot.remaining_value(), D("90000"))
        self.assertEqual(list(AccountPosting.objects.order_by("pk").values("pk", "amount", "object_id")), postings_before)

    def test_writeoff_status_and_business_loss_reporting_are_distinct(self):
        claim = services.create_reimbursement(
            user=self.user,
            responsible_party="Employer",
            supplier="Shop",
            account=self.account,
            purchase_amount=D("80000"),
            fee_amount=D("1000"),
            fee_bearer="user",
            purchase_date=TODAY,
            description="Company supplies",
            receipt_reference="R-1",
            writeoff_scope=ReimbursementClaim.WriteoffScope.BUSINESS,
            notes="",
        )
        services.save_reimbursement_payment(
            user=self.user,
            claim=claim,
            account=self.account,
            amount=D("30000"),
            unrecovered_amount=D("50000"),
            fee_amount=0,
            fee_bearer="user",
            date=TODAY,
            reference="PAY-1",
            notes="",
        )
        report = period_summary(self.user, TODAY, TODAY)
        self.assertEqual(claim.status, "written_off")
        self.assertEqual(claim.total_reimbursed(), D("30000"))
        self.assertEqual(claim.total_written_off(), D("50000"))
        self.assertEqual(report["business_reimbursement_losses"], D("50000"))
        self.assertEqual(report["personal_reimbursement_losses"], D("0"))

    def test_non_ugx_account_is_rejected_by_web_and_api_without_postings(self):
        usd = FinancialAccount.objects.create(
            user=self.user,
            name="USD wallet",
            currency_code="USD",
            opening_balance=0,
            opening_balance_date=TODAY.replace(day=1),
        )
        response = self.client.post(
            "/api/v1/reimbursements/",
            {
                "responsible_party": "Employer",
                "account": usd.pk,
                "purchase_amount": "20",
                "purchase_date": TODAY.isoformat(),
                "description": "Supplies",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ReimbursementClaim.objects.count(), 0)
        self.assertEqual(AccountPosting.objects.count(), 0)
        page = self.client.get("/accounting/reimbursements/new/")
        self.assertNotContains(page, "USD wallet")

    def test_private_attachment_validates_content_and_is_user_isolated(self):
        fund = services.create_held_fund(user=self.user, owner_name="Community", purpose="Aid", notes="")
        valid = SimpleUploadedFile("receipt.pdf", b"%PDF-1.4\n%%EOF", content_type="application/pdf")
        response = self.client.post(
            f"/accounting/documents/fund/{fund.pk}/add/",
            {"label": "Mobile Money receipt", "document": valid},
        )
        self.assertEqual(response.status_code, 302)
        attachment = AccountingAttachment.objects.get()

        other = User.objects.create_user(email="other-enhancement@example.com", password="StrongPass123!")
        self.client.force_login(other)
        self.assertEqual(self.client.get(f"/accounting/documents/{attachment.pk}/view/").status_code, 404)

        self.client.force_login(self.user)
        invalid = SimpleUploadedFile("fake.pdf", b"not a pdf", content_type="application/pdf")
        self.client.post(
            f"/accounting/documents/fund/{fund.pk}/add/",
            {"label": "Fake", "document": invalid},
        )
        self.assertEqual(AccountingAttachment.objects.count(), 1)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(f"/accounting/documents/{attachment.pk}/delete/")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AccountingAttachment.objects.exists())

    def test_dashboard_search_and_api_expose_workflow_fields(self):
        fund = services.create_held_fund(user=self.user, owner_name="Alpha Charity", purpose="Aid", notes="")
        services.create_held_fund(user=self.user, owner_name="Beta Group", purpose="Other", notes="")
        services.save_held_entry(
            user=self.user,
            fund=fund,
            kind="receipt",
            account=self.account,
            amount=100,
            fee_amount=0,
            fee_bearer="owner",
            date=TODAY,
            sender_name="Named Donor",
            recipient_name="",
            reference="REF-1",
            description="Aid",
            notes="",
        )
        page = self.client.get("/accounting/", {"q": "Alpha"})
        self.assertContains(page, "Alpha Charity")
        self.assertNotContains(page, "Beta Group")
        api = self.client.get("/api/v1/held-fund-entries/")
        self.assertEqual(api.status_code, 200)
        payload = api.json()
        row = payload["results"][0] if "results" in payload else payload[0]
        self.assertEqual(row["sender_name"], "Named Donor")
        self.assertEqual(row["reference"], "REF-1")
