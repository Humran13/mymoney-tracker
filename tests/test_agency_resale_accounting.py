from datetime import timedelta
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
from apps.transactions.services import create_transaction

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

    def posting_state(self):
        return list(
            AccountPosting.objects.filter(user=self.user)
            .order_by("pk")
            .values(
                "pk", "account_id", "amount", "source_type", "occurred_on",
                "content_type_id", "object_id",
            )
        )

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

    def test_backdated_distribution_and_fund_moves_preserve_chronological_liability(self):
        first = self.held_fund()
        second = services.create_held_fund(user=self.user, owner_name="Second owner")
        receipt = services.save_held_entry(
            user=self.user, fund=first, kind="receipt", account=self.account, amount=D("100"),
            fee_amount=0, fee_bearer="owner", date=TODAY - timedelta(days=1), description="", notes="",
        )
        distribution = services.save_held_entry(
            user=self.user, fund=first, kind="distribution", account=self.account, amount=D("80"),
            fee_amount=0, fee_bearer="owner", date=TODAY, description="", notes="",
        )
        second_receipt = services.save_held_entry(
            user=self.user, fund=second, kind="receipt", account=self.account, amount=D("20"),
            fee_amount=0, fee_bearer="owner", date=TODAY - timedelta(days=1), description="", notes="",
        )
        before_postings = self.posting_state()
        before_balance = self.balance()

        with self.assertRaises(ValidationError):
            services.save_held_entry(
                user=self.user, fund=first, kind="distribution", account=self.account, amount=D("10"),
                fee_amount=0, fee_bearer="owner", date=TODAY - timedelta(days=2), description="", notes="",
            )
        with self.assertRaises(ValidationError):
            services.save_held_entry(
                user=self.user, instance=receipt, fund=second, kind="receipt", account=self.account,
                amount=D("100"), fee_amount=0, fee_bearer="owner", date=receipt.date,
                description="", notes="",
            )
        with self.assertRaises(ValidationError):
            services.save_held_entry(
                user=self.user, instance=distribution, fund=second, kind="distribution", account=self.account,
                amount=D("80"), fee_amount=0, fee_bearer="owner", date=distribution.date,
                description="", notes="",
            )

        receipt.refresh_from_db()
        distribution.refresh_from_db()
        second_receipt.refresh_from_db()
        self.assertEqual(receipt.fund_id, first.id)
        self.assertEqual(distribution.fund_id, first.id)
        self.assertEqual(first.outstanding(), D("20"))
        self.assertEqual(second.outstanding(), D("20"))
        self.assertEqual(self.posting_state(), before_postings)
        self.assertEqual(self.balance(), before_balance)

    def test_receipt_reduction_and_reversal_are_rejected_by_web_and_api_atomically(self):
        fund = self.held_fund()
        receipt = services.save_held_entry(
            user=self.user, fund=fund, kind="receipt", account=self.account, amount=D("100"),
            fee_amount=0, fee_bearer="owner", date=TODAY - timedelta(days=1), description="", notes="",
        )
        services.save_held_entry(
            user=self.user, fund=fund, kind="distribution", account=self.account, amount=D("80"),
            fee_amount=0, fee_bearer="owner", date=TODAY, description="", notes="",
        )
        before_postings = self.posting_state()
        before_balance = self.balance()
        self.client.force_login(self.user)

        response = self.client.patch(
            f"/api/v1/held-fund-entries/{receipt.pk}/", {"amount": "70.00"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        response = self.client.post(
            f"/accounting/reverse/held-entry/{receipt.pk}/", {"reason": "Invalid reversal"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "negative at an earlier point")
        response = self.client.delete(f"/api/v1/held-fund-entries/{receipt.pk}/")
        self.assertEqual(response.status_code, 400, response.content)

        receipt.refresh_from_db()
        self.assertEqual(receipt.amount, D("100"))
        self.assertIsNone(receipt.reversed_at)
        self.assertEqual(fund.outstanding(), D("20"))
        self.assertEqual(self.posting_state(), before_postings)
        self.assertEqual(self.balance(), before_balance)


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

    def test_api_cannot_reassign_existing_payment_or_use_its_balance_on_another_claim(self):
        first = services.create_reimbursement(
            user=self.user, responsible_party="A", account=self.account, purchase_amount=D("100"),
            fee_amount=0, fee_bearer="user", purchase_date=TODAY, description="A", receipt_reference="", notes="",
        )
        second = services.create_reimbursement(
            user=self.user, responsible_party="B", account=self.account, purchase_amount=D("100"),
            fee_amount=0, fee_bearer="user", purchase_date=TODAY, description="B", receipt_reference="", notes="",
        )
        payment = services.save_reimbursement_payment(
            user=self.user, claim=first, account=self.account, amount=D("60"), unrecovered_amount=0,
            fee_amount=0, fee_bearer="user", date=TODAY, notes="",
        )
        before_postings = self.posting_state()
        before_balance = self.balance()
        self.client.force_login(self.user)

        response = self.client.patch(
            f"/api/v1/reimbursement-payments/{payment.pk}/",
            {"claim": second.pk, "amount": "120.00"}, content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)

        other = User.objects.create_user(email="claim-owner@example.com", password="StrongPass123!")
        other_account = FinancialAccount.objects.create(
            user=other, name="Other", opening_balance=0, opening_balance_date=START,
        )
        foreign = services.create_reimbursement(
            user=other, responsible_party="Foreign", account=other_account, purchase_amount=D("100"),
            fee_amount=0, fee_bearer="user", purchase_date=TODAY, description="Foreign",
            receipt_reference="", notes="",
        )
        response = self.client.patch(
            f"/api/v1/reimbursement-payments/{payment.pk}/", {"claim": foreign.pk},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)

        payment.refresh_from_db()
        self.assertEqual(payment.claim_id, first.pk)
        self.assertEqual(payment.amount, D("60"))
        self.assertEqual(first.outstanding(), D("40"))
        self.assertEqual(second.outstanding(), D("100"))
        self.assertEqual(self.posting_state(), before_postings)
        self.assertEqual(self.balance(), before_balance)

    def test_web_rejects_payment_and_claim_edits_that_make_outstanding_negative(self):
        claim = services.create_reimbursement(
            user=self.user, responsible_party="A", account=self.account, purchase_amount=D("100"),
            fee_amount=0, fee_bearer="user", purchase_date=TODAY, description="A", receipt_reference="", notes="",
        )
        payment = services.save_reimbursement_payment(
            user=self.user, claim=claim, account=self.account, amount=D("60"), unrecovered_amount=0,
            fee_amount=0, fee_bearer="user", date=TODAY, notes="",
        )
        before_postings = self.posting_state()
        before_balance = self.balance()
        self.client.force_login(self.user)

        response = self.client.post(
            f"/accounting/reimbursements/{claim.pk}/payment/{payment.pk}/edit/",
            {
                "account": self.account.pk, "amount": "101.00", "unrecovered_amount": "0.00",
                "fee_amount": "0.00", "fee_bearer": "user", "date": TODAY.isoformat(),
                "notes": "", "operation_key": str(uuid.uuid4()),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot exceed the outstanding receivable")
        response = self.client.post(
            f"/accounting/reimbursements/{claim.pk}/edit/",
            {
                "responsible_party": "A", "account": self.account.pk, "purchase_amount": "50.00",
                "fee_amount": "0.00", "fee_bearer": "user", "purchase_date": TODAY.isoformat(),
                "description": "A", "receipt_reference": "", "notes": "",
                "operation_key": str(uuid.uuid4()),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot be reduced below reimbursements")

        claim.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(claim.purchase_amount, D("100"))
        self.assertEqual(payment.amount, D("60"))
        self.assertEqual(claim.outstanding(), D("40"))
        self.assertEqual(self.posting_state(), before_postings)
        self.assertEqual(self.balance(), before_balance)


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

    def test_general_business_expense_is_separate_from_personal_expenses(self):
        create_transaction(
            user=self.user, account=self.account, kind="expense", scope="business", category=None,
            amount=D("25000"), fee_amount=D("500"), date=TODAY, description="Shop rent", notes="",
        )
        create_transaction(
            user=self.user, account=self.account, kind="income", scope="business", category=None,
            amount=D("40000"), fee_amount=D("0"), date=TODAY, description="Service revenue", notes="",
        )
        report = compute_monthly_report(self.user, TODAY.year, TODAY.month)
        self.assertEqual(report["total_income"], D("0"))
        self.assertEqual(report["total_expenses"], D("0"))
        self.assertEqual(report["business_revenue"], D("40000"))
        self.assertEqual(report["business_operating_expenses"], D("25500"))
        self.assertEqual(report["business_net_profit"], D("14500"))

    def test_oversale_and_overpayment_are_rejected(self):
        lot = services.save_inventory_lot(user=self.user, product_name="x", supplier="", source="local", account=self.account, acquisition_date=TODAY, quantity=D("2"), purchase_cost=D("100"), shipping_cost=0, customs_cost=0, transport_cost=0, notes="")
        with self.assertRaises(ValidationError):
            services.save_inventory_sale(user=self.user, lot=lot, customer="", sale_date=TODAY, quantity=D("3"), sale_amount=D("200"), notes="")
        sale = services.save_inventory_sale(user=self.user, lot=lot, customer="", sale_date=TODAY, quantity=D("1"), sale_amount=D("100"), notes="")
        with self.assertRaises(ValidationError):
            services.save_sale_payment(user=self.user, sale=sale, account=self.account, amount=D("101"), fee_amount=0, date=TODAY, notes="")

    def test_api_cannot_reassign_sale_or_customer_payment_parents(self):
        first_lot = services.save_inventory_lot(
            user=self.user, product_name="First", supplier="", source="local", account=self.account,
            acquisition_date=TODAY, quantity=D("2"), purchase_cost=D("100"), shipping_cost=0,
            customs_cost=0, transport_cost=0, notes="",
        )
        second_lot = services.save_inventory_lot(
            user=self.user, product_name="Second", supplier="", source="local", account=self.account,
            acquisition_date=TODAY, quantity=D("2"), purchase_cost=D("100"), shipping_cost=0,
            customs_cost=0, transport_cost=0, notes="",
        )
        first_sale = services.save_inventory_sale(
            user=self.user, lot=first_lot, customer="A", sale_date=TODAY,
            quantity=D("1"), sale_amount=D("100"), notes="",
        )
        second_sale = services.save_inventory_sale(
            user=self.user, lot=second_lot, customer="B", sale_date=TODAY,
            quantity=D("1"), sale_amount=D("100"), notes="",
        )
        payment = services.save_sale_payment(
            user=self.user, sale=first_sale, account=self.account, amount=D("60"),
            fee_amount=0, date=TODAY, notes="",
        )
        before_postings = self.posting_state()
        before_balance = self.balance()
        self.client.force_login(self.user)

        response = self.client.patch(
            f"/api/v1/inventory-sale-payments/{payment.pk}/",
            {"sale": second_sale.pk, "amount": "120.00"}, content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        response = self.client.patch(
            f"/api/v1/inventory-sales/{first_sale.pk}/", {"lot": second_lot.pk},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)

        other = User.objects.create_user(email="inventory-owner@example.com", password="StrongPass123!")
        other_account = FinancialAccount.objects.create(
            user=other, name="Other", opening_balance=0, opening_balance_date=START,
        )
        foreign_lot = services.save_inventory_lot(
            user=other, product_name="Foreign", supplier="", source="local", account=other_account,
            acquisition_date=TODAY, quantity=D("1"), purchase_cost=D("10"), shipping_cost=0,
            customs_cost=0, transport_cost=0, notes="",
        )
        response = self.client.patch(
            f"/api/v1/inventory-sales/{first_sale.pk}/", {"lot": foreign_lot.pk},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)

        first_sale.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(first_sale.lot_id, first_lot.pk)
        self.assertEqual(payment.sale_id, first_sale.pk)
        self.assertEqual(payment.amount, D("60"))
        self.assertEqual(first_sale.outstanding(), D("40"))
        self.assertEqual(first_lot.quantity_remaining(), D("1"))
        self.assertEqual(second_lot.quantity_remaining(), D("1"))
        self.assertEqual(self.posting_state(), before_postings)
        self.assertEqual(self.balance(), before_balance)

    def test_web_rejects_negative_sale_balance_and_invalid_inventory_quantity(self):
        lot = services.save_inventory_lot(
            user=self.user, product_name="Stock", supplier="", source="local", account=self.account,
            acquisition_date=TODAY, quantity=D("2"), purchase_cost=D("100"), shipping_cost=0,
            customs_cost=0, transport_cost=0, notes="",
        )
        sale = services.save_inventory_sale(
            user=self.user, lot=lot, customer="A", sale_date=TODAY,
            quantity=D("1"), sale_amount=D("100"), notes="",
        )
        services.save_sale_payment(
            user=self.user, sale=sale, account=self.account, amount=D("60"),
            fee_amount=0, date=TODAY, notes="",
        )
        before_postings = self.posting_state()
        before_balance = self.balance()
        self.client.force_login(self.user)

        response = self.client.post(
            f"/accounting/sales/{sale.pk}/edit/",
            {
                "customer": "A", "sale_date": TODAY.isoformat(), "quantity": "1.000",
                "sale_amount": "50.00", "notes": "", "operation_key": str(uuid.uuid4()),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot be reduced below payments")
        response = self.client.post(
            f"/accounting/sales/{sale.pk}/edit/",
            {
                "customer": "A", "sale_date": TODAY.isoformat(), "quantity": "3.000",
                "sale_amount": "100.00", "notes": "", "operation_key": str(uuid.uuid4()),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "exceeds inventory currently available")

        sale.refresh_from_db()
        self.assertEqual(sale.sale_amount, D("100"))
        self.assertEqual(sale.quantity, D("1"))
        self.assertEqual(sale.outstanding(), D("40"))
        self.assertEqual(lot.quantity_remaining(), D("1"))
        self.assertEqual(self.posting_state(), before_postings)
        self.assertEqual(self.balance(), before_balance)


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
