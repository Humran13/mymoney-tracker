from decimal import Decimal
from unittest import mock
import uuid

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounting import services
from apps.accounting.models import HeldFundEntry
from apps.accounts.models import AccountPosting, FinancialAccount
from apps.accounts.services import account_balance
from apps.core.models import AuditEvent
from apps.reports.cash_flow import period_cash_flow
from apps.reports.services import compute_monthly_report
from apps.users.models import User

D = Decimal
TODAY = timezone.localdate()
START = TODAY.replace(day=1)


class AccountingWorkflowBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="agency@example.com", password="StrongPass123!")
        self.account = FinancialAccount.objects.create(
            user=self.user, name="Mobile Money", account_type="mobile_money_mtn",
            opening_balance=D("1000000.00"), opening_balance_date=START,
        )

    def balance(self):
        return account_balance(FinancialAccount.objects.get(pk=self.account.pk))

    def held_fund(self):
        return services.create_held_fund(user=self.user, owner_name="Community Group", purpose="Charity", notes="")


class HeldFundsTests(AccountingWorkflowBase):
    def test_receipt_partial_distribution_and_owner_fee_are_not_personal_results(self):
        fund = self.held_fund()
        services.save_held_entry(
            user=self.user, fund=fund, kind="receipt", account=self.account, amount=D("500000"),
            fee_amount=D("2000"), fee_bearer="owner", date=TODAY, description="Collection", notes="",
        )
        self.assertEqual(fund.outstanding(), D("498000.00"))
        self.assertEqual(self.balance(), D("1498000.00"))
        services.save_held_entry(
            user=self.user, fund=fund, kind="distribution", account=self.account, amount=D("200000"),
            fee_amount=D("0"), fee_bearer="owner", date=TODAY, description="Part payment", notes="",
        )
        self.assertEqual(fund.outstanding(), D("298000.00"))
        report = compute_monthly_report(self.user, TODAY.year, TODAY.month)
        self.assertEqual(report["total_income"], D("0"))
        self.assertEqual(report["total_expenses"], D("0"))
        self.assertEqual(report["held_funds_liability"], D("298000"))
        cash = period_cash_flow(self.user, TODAY, TODAY)
        self.assertEqual(cash["received"], D("500000"))
        self.assertEqual(cash["spent"], D("202000"))

    def test_user_borne_mobile_money_fee_is_personal_expense_once(self):
        fund = self.held_fund()
        entry = services.save_held_entry(
            user=self.user, fund=fund, kind="receipt", account=self.account, amount=D("100000"),
            fee_amount=D("1500"), fee_bearer="user", date=TODAY, description="Collection", notes="",
        )
        report = compute_monthly_report(self.user, TODAY.year, TODAY.month)
        self.assertEqual(report["total_expenses"], D("1500"))
        self.assertEqual(fund.outstanding(), D("100000"))
        self.assertEqual(entry.account.postings.filter(source_type=AccountPosting.SourceType.FEE).count(), 1)

    def test_over_distribution_is_atomic(self):
        fund = self.held_fund()
        services.save_held_entry(user=self.user, fund=fund, kind="receipt", account=self.account, amount=D("50000"), fee_amount=0, fee_bearer="owner", date=TODAY, description="", notes="")
        before = self.balance()
        with self.assertRaises(ValidationError):
            services.save_held_entry(user=self.user, fund=fund, kind="distribution", account=self.account, amount=D("50001"), fee_amount=0, fee_bearer="owner", date=TODAY, description="", notes="")
        self.assertEqual(self.balance(), before)
        self.assertEqual(fund.entries.count(), 1)

    def test_edit_replaces_postings_and_reversal_preserves_audit(self):
        fund = self.held_fund()
        entry = services.save_held_entry(user=self.user, fund=fund, kind="receipt", account=self.account, amount=D("50000"), fee_amount=0, fee_bearer="owner", date=TODAY, description="", notes="")
        services.save_held_entry(user=self.user, instance=entry, fund=fund, kind="receipt", account=self.account, amount=D("60000"), fee_amount=0, fee_bearer="owner", date=TODAY, description="", notes="")
        self.assertEqual(AccountPosting.objects.filter(object_id=entry.pk, source_type="held_receive").count(), 1)
        self.assertEqual(self.balance(), D("1060000"))
        services.reverse(entry, actor=self.user, reason="Duplicate collection")
        self.assertEqual(self.balance(), D("1000000"))
        self.assertTrue(AuditEvent.objects.filter(object_type="HeldFundEntry", metadata__before__isnull=False).exists())


class ReimbursementTests(AccountingWorkflowBase):
    def test_purchase_partial_reimbursement_and_unrecovered_amount(self):
        claim = services.create_reimbursement(
            user=self.user, responsible_party="NGO", account=self.account, purchase_amount=D("80000"),
            fee_amount=0, fee_bearer="user", purchase_date=TODAY, description="Supplies", receipt_reference="R1", notes="",
        )
        self.assertEqual(self.balance(), D("920000"))
        services.save_reimbursement_payment(user=self.user, claim=claim, account=self.account, amount=D("30000"), unrecovered_amount=0, fee_amount=0, fee_bearer="user", date=TODAY, notes="")
        self.assertEqual(claim.outstanding(), D("50000"))
        services.save_reimbursement_payment(user=self.user, claim=claim, account=self.account, amount=D("45000"), unrecovered_amount=D("5000"), fee_amount=0, fee_bearer="user", date=TODAY, notes="Short settlement")
        self.assertEqual(claim.outstanding(), D("0"))
        self.assertEqual(self.balance(), D("995000"))
        report = compute_monthly_report(self.user, TODAY.year, TODAY.month)
        self.assertEqual(report["total_income"], D("0"))
        self.assertEqual(report["total_expenses"], D("5000"))
        self.assertEqual(report["reimbursements_received"], D("75000"))

    def test_reimbursement_cannot_exceed_receivable(self):
        claim = services.create_reimbursement(user=self.user, responsible_party="A", account=self.account, purchase_amount=D("10000"), fee_amount=0, fee_bearer="user", purchase_date=TODAY, description="x", receipt_reference="", notes="")
        with self.assertRaises(ValidationError):
            services.save_reimbursement_payment(user=self.user, claim=claim, account=self.account, amount=D("10001"), unrecovered_amount=0, fee_amount=0, fee_bearer="user", date=TODAY, notes="")
        self.assertEqual(claim.payments.count(), 0)


class InventoryTests(AccountingWorkflowBase):
    def test_landed_cost_partial_sale_revenue_cogs_profit_and_partial_payment(self):
        lot = services.save_inventory_lot(
            user=self.user, product_name="Phones", supplier="Supplier", source="international", account=self.account,
            acquisition_date=TODAY, quantity=D("10"), purchase_cost=D("150000"), shipping_cost=D("20000"),
            customs_cost=D("5000"), transport_cost=D("5000"), notes="",
        )
        self.assertEqual(lot.landed_cost, D("180000"))
        self.assertEqual(self.balance(), D("820000"))
        sale = services.save_inventory_sale(user=self.user, lot=lot, customer="Buyer", sale_date=TODAY, quantity=D("4"), sale_amount=D("92000"), notes="")
        self.assertEqual(sale.cost_of_goods_sold, D("72000.00"))
        self.assertEqual(sale.gross_profit, D("20000.00"))
        self.assertEqual(lot.quantity_remaining(), D("6.000"))
        services.save_sale_payment(user=self.user, sale=sale, account=self.account, amount=D("50000"), fee_amount=D("1000"), date=TODAY, notes="")
        self.assertEqual(sale.outstanding(), D("42000"))
        self.assertEqual(self.balance(), D("869000"))
        report = compute_monthly_report(self.user, TODAY.year, TODAY.month)
        self.assertEqual(report["business_revenue"], D("92000"))
        self.assertEqual(report["cost_of_goods_sold"], D("72000"))
        self.assertEqual(report["business_gross_profit"], D("20000"))
        self.assertEqual(report["total_income"], D("0"))
        self.assertEqual(report["total_expenses"], D("0"))

    def test_oversale_and_overpayment_are_rejected(self):
        lot = services.save_inventory_lot(user=self.user, product_name="x", supplier="", source="local", account=self.account, acquisition_date=TODAY, quantity=D("2"), purchase_cost=D("100"), shipping_cost=0, customs_cost=0, transport_cost=0, notes="")
        with self.assertRaises(ValidationError):
            services.save_inventory_sale(user=self.user, lot=lot, customer="", sale_date=TODAY, quantity=D("3"), sale_amount=D("200"), notes="")
        sale = services.save_inventory_sale(user=self.user, lot=lot, customer="", sale_date=TODAY, quantity=D("1"), sale_amount=D("100"), notes="")
        with self.assertRaises(ValidationError):
            services.save_sale_payment(user=self.user, sale=sale, account=self.account, amount=D("101"), fee_amount=0, date=TODAY, notes="")


class IntegrityAndInterfaceTests(AccountingWorkflowBase):
    def test_opening_balance_boundary_and_cross_user_account(self):
        future = FinancialAccount.objects.create(user=self.user, name="Future", opening_balance=0, opening_balance_date=TODAY.replace(day=28))
        with self.assertRaises(ValidationError):
            services.save_inventory_lot(user=self.user, product_name="x", supplier="", source="local", account=future, acquisition_date=TODAY, quantity=1, purchase_cost=1, shipping_cost=0, customs_cost=0, transport_cost=0, notes="")
        other = User.objects.create_user(email="other@example.com", password="StrongPass123!")
        foreign = FinancialAccount.objects.create(user=other, name="Other", opening_balance=0, opening_balance_date=START)
        with self.assertRaises(ValidationError):
            services.create_reimbursement(user=self.user, responsible_party="x", account=foreign, purchase_amount=1, fee_amount=0, fee_bearer="user", purchase_date=TODAY, description="x", receipt_reference="", notes="")

    def test_posting_failure_rolls_back_source_and_audit(self):
        fund = self.held_fund()
        with mock.patch("apps.accounting.services.sync_postings", side_effect=RuntimeError("ledger unavailable")):
            with self.assertRaises(RuntimeError):
                services.save_held_entry(user=self.user, fund=fund, kind="receipt", account=self.account, amount=D("100"), fee_amount=0, fee_bearer="owner", date=TODAY, description="", notes="")
        self.assertEqual(HeldFundEntry.objects.count(), 0)
        self.assertFalse(AuditEvent.objects.filter(object_type="HeldFundEntry").exists())

    def test_api_isolation_and_write_path(self):
        self.client.force_login(self.user)
        response = self.client.post("/api/v1/held-funds/", {"owner_name": "Club", "purpose": "Dues", "notes": ""}, content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content)
        fund_id = response.json()["id"]
        response = self.client.post("/api/v1/held-fund-entries/", {"fund": fund_id, "kind": "receipt", "account": self.account.pk, "amount": "1000", "fee_amount": "0", "fee_bearer": "owner", "date": TODAY.isoformat(), "description": "", "notes": ""}, content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content)
        other = User.objects.create_user(email="api-other@example.com", password="StrongPass123!")
        foreign = FinancialAccount.objects.create(user=other, name="Foreign", opening_balance=0, opening_balance_date=START)
        response = self.client.post("/api/v1/held-fund-entries/", {"fund": fund_id, "kind": "receipt", "account": foreign.pk, "amount": "1", "fee_amount": "0", "fee_bearer": "owner", "date": TODAY.isoformat()}, content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_api_operation_key_prevents_duplicate_financial_create(self):
        self.client.force_login(self.user)
        fund = self.client.post("/api/v1/held-funds/", {"owner_name": "Club"}, content_type="application/json").json()
        key = str(uuid.uuid4())
        payload = {"fund": fund["id"], "kind": "receipt", "account": self.account.pk, "amount": "1000", "fee_amount": "0", "fee_bearer": "owner", "date": TODAY.isoformat(), "operation_key": key}
        self.assertEqual(self.client.post("/api/v1/held-fund-entries/", payload, content_type="application/json").status_code, 201)
        self.assertEqual(self.client.post("/api/v1/held-fund-entries/", payload, content_type="application/json").status_code, 400)
        self.assertEqual(HeldFundEntry.objects.filter(fund_id=fund["id"]).count(), 1)
        self.assertEqual(AccountPosting.objects.filter(source_type="held_receive").count(), 1)

    def test_mobile_and_desktop_pages_render_shared_responsive_interface(self):
        self.client.force_login(self.user)
        for agent in ("Mozilla/5.0 (iPhone)", "Mozilla/5.0 (Windows NT 10.0)"):
            response = self.client.get("/accounting/", HTTP_USER_AGENT=agent)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Agency &amp; Resale")
            self.assertContains(response, "sm:grid-cols-3")
