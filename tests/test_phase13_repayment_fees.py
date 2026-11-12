"""Phase 13, improvement 5 — transaction fees on debt repayments.

A repayment of 100,000 with a 1,500 fee must:
  * reduce the paying account by exactly 101,500
  * reduce the outstanding debt by exactly 100,000
  * add exactly one 1,500 fee expense (and never a second one)
"""

import datetime
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import AccountPosting, FinancialAccount
from apps.accounts.services import account_balance
from apps.debts.forms import BorrowingRepaymentForm
from apps.debts.models import BorrowingRepayment
from apps.debts.services import (
    create_borrowing,
    delete_borrowing,
    delete_borrowing_repayment,
    record_borrowing_repayment,
    update_borrowing_repayment,
)
from apps.reports.services import compute_monthly_report
from apps.users.models import User

DAY = datetime.date.today()


def postings_of(obj):
    """AccountPosting is linked through a GenericForeignKey, so there is no
    reverse manager on the source object — query it the same way services do."""
    return AccountPosting.objects.filter(
        content_type=ContentType.objects.get_for_model(obj), object_id=obj.pk
    )


class RepaymentFeeAccountingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="fees@example.com", password="StrongPass123!")
        self.accounts = {}
        for key, label, account_type in [
            ("cash", "Cash Wallet", "cash"),
            ("mtn", "MTN Mobile Money", "mobile_money_mtn"),
            ("airtel", "Airtel Money", "mobile_money_airtel"),
            ("bank", "Bank Account", "bank"),
        ]:
            self.accounts[key] = FinancialAccount.objects.create(
                user=self.user, name=label, account_type=account_type,
                opening_balance=Decimal("500000.00"),
            )
        # The loan money landed in its own account, so each payment account
        # below starts at exactly 500,000.00 and the arithmetic stays obvious.
        self.source = FinancialAccount.objects.create(
            user=self.user, name="Loan Proceeds", account_type="cash", opening_balance=Decimal("0.00")
        )
        self.borrowing = create_borrowing(
            user=self.user,
            lender_name="Supplier",
            amount=Decimal("400000.00"),
            date_borrowed=DAY - datetime.timedelta(days=30),
            receiving_account=self.source,
        )

    def _repay(self, account_key="airtel", amount="100000.00", fee="1500.00"):
        return record_borrowing_repayment(
            user=self.user,
            borrowing=self.borrowing,
            amount=Decimal(amount),
            fee_amount=Decimal(fee),
            date=DAY,
            paying_account=self.accounts[account_key],
        )

    def test_repayment_without_fee_reduces_cash_by_principal_only(self):
        repayment = self._repay(account_key="cash", fee="0.00")
        self.assertEqual(account_balance(self.accounts["cash"]), Decimal("400000.00"))
        self.assertEqual(self.borrowing.outstanding(), Decimal("300000.00"))
        self.assertEqual(repayment.fee_amount, Decimal("0.00"))
        self.assertEqual(repayment.total_cash_out(), Decimal("100000.00"))
        self.assertEqual(postings_of(repayment).count(), 1)

    def test_repayment_with_fee_splits_principal_and_charge(self):
        repayment = self._repay(account_key="airtel", amount="100000.00", fee="1500.00")

        # 100,000 principal + 1,500 fee actually left the account.
        self.assertEqual(account_balance(self.accounts["airtel"]), Decimal("398500.00"))
        # ...but only the principal reduced the debt.
        self.assertEqual(self.borrowing.outstanding(), Decimal("300000.00"))
        self.assertEqual(repayment.total_cash_out(), Decimal("101500.00"))

        principal = postings_of(repayment).get(source_type=AccountPosting.SourceType.BORROW_REPAY)
        fee = postings_of(repayment).get(source_type=AccountPosting.SourceType.FEE)
        self.assertEqual(principal.amount, Decimal("-100000.00"))
        self.assertEqual(fee.amount, Decimal("-1500.00"))
        self.assertEqual(postings_of(repayment).count(), 2)

    def test_fee_works_for_every_supported_payment_channel(self):
        for key in (("mtn", "airtel", "bank", "cash")):
            borrowing = create_borrowing(
                user=self.user,
                lender_name=f"Lender {key}",
                amount=Decimal("200000.00"),
                date_borrowed=DAY - datetime.timedelta(days=10),
                receiving_account=self.source,
            )
            record_borrowing_repayment(
                user=self.user,
                borrowing=borrowing,
                amount=Decimal("50000.00"),
                fee_amount=Decimal("700.00"),
                date=DAY,
                paying_account=self.accounts[key],
            )
            expected = Decimal("449300.00")  # 500,000 - 50,000 principal - 700 fee
            self.assertEqual(account_balance(self.accounts[key]), expected, key)
            self.assertEqual(borrowing.outstanding(), Decimal("150000.00"), key)

    def test_partial_and_full_repayments_with_fees(self):
        first = self._repay(amount="100000.00", fee="1500.00")
        self.assertEqual(self.borrowing.outstanding(), Decimal("300000.00"))
        second = self._repay(amount="200000.00", fee="2000.00")
        self.assertEqual(self.borrowing.outstanding(), Decimal("100000.00"))
        final = self._repay(amount="100000.00", fee="1000.00")
        self.assertEqual(self.borrowing.outstanding(), Decimal("0.00"))
        self.borrowing.refresh_from_db()
        self.assertEqual(self.borrowing.status, "settled")
        # Three repayments => exactly one fee posting each, never more.
        self.assertEqual(AccountPosting.objects.filter(source_type=AccountPosting.SourceType.FEE).count(), 3)
        self.assertEqual(account_balance(self.accounts["airtel"]), Decimal("95500.00"))
        self.assertEqual(
            [first.amount, second.amount, final.amount],
            [Decimal("100000.00"), Decimal("200000.00"), Decimal("100000.00")],
        )

    def test_negative_fee_is_rejected(self):
        with self.assertRaises(ValueError):
            self._repay(amount="100000.00", fee="-1.00")
        self.assertEqual(self.borrowing.repayments.count(), 0)
        self.assertEqual(account_balance(self.accounts["airtel"]), Decimal("500000.00"))

    def test_overpayment_is_rejected(self):
        with self.assertRaises(ValueError):
            self._repay(amount="400001.00", fee="0.00")
        self.assertEqual(self.borrowing.repayments.count(), 0)

    def test_editing_a_repayment_replaces_postings_without_duplicating(self):
        repayment = self._repay(amount="100000.00", fee="1500.00")
        update_borrowing_repayment(repayment, amount=Decimal("150000.00"), fee_amount=Decimal("2500.00"))

        self.assertEqual(postings_of(repayment).count(), 2)
        self.assertEqual(account_balance(self.accounts["airtel"]), Decimal("347500.00"))
        self.assertEqual(self.borrowing.outstanding(), Decimal("250000.00"))

        # Editing again must not accumulate: still exactly two postings.
        update_borrowing_repayment(repayment, amount=Decimal("150000.00"), fee_amount=Decimal("2500.00"))
        self.assertEqual(postings_of(repayment).count(), 2)
        self.assertEqual(account_balance(self.accounts["airtel"]), Decimal("347500.00"))

    def test_editing_a_repayment_can_remove_the_fee(self):
        repayment = self._repay(amount="100000.00", fee="1500.00")
        update_borrowing_repayment(repayment, amount=Decimal("100000.00"), fee_amount=Decimal("0.00"))
        self.assertEqual(postings_of(repayment).count(), 1)
        self.assertEqual(AccountPosting.objects.filter(source_type=AccountPosting.SourceType.FEE).count(), 0)
        self.assertEqual(account_balance(self.accounts["airtel"]), Decimal("400000.00"))

    def test_editing_upward_is_allowed_within_the_outstanding_amount(self):
        repayment = self._repay(amount="300000.00", fee="0.00")
        self.assertEqual(self.borrowing.outstanding(), Decimal("100000.00"))
        update_borrowing_repayment(repayment, amount=Decimal("400000.00"), fee_amount=Decimal("0.00"))
        self.assertEqual(repayment.amount, Decimal("400000.00"))
        self.assertEqual(self.borrowing.outstanding(), Decimal("0.00"))

    def test_deleting_a_repayment_removes_its_fee_posting(self):
        repayment = self._repay(amount="100000.00", fee="1500.00")
        delete_borrowing_repayment(repayment)
        self.assertEqual(AccountPosting.objects.filter(source_type=AccountPosting.SourceType.FEE).count(), 0)
        self.assertEqual(account_balance(self.accounts["airtel"]), Decimal("500000.00"))
        self.assertEqual(self.borrowing.outstanding(), Decimal("400000.00"))
        self.assertEqual(postings_of(repayment).count(), 0)

    def test_deleting_the_borrowing_removes_repayment_fees(self):
        self._repay(amount="100000.00", fee="1500.00")
        delete_borrowing(self.borrowing)
        self.assertEqual(AccountPosting.objects.filter(source_type=AccountPosting.SourceType.FEE).count(), 0)
        self.assertEqual(account_balance(self.accounts["airtel"]), Decimal("500000.00"))
        self.assertEqual(BorrowingRepayment.objects.filter(user=self.user).count(), 0)


class RepaymentFeeReportingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="feereport@example.com", password="StrongPass123!")
        self.wallet = FinancialAccount.objects.create(
            user=self.user, name="Wallet", account_type="cash", opening_balance=Decimal("1000000.00")
        )
        self.borrowing = create_borrowing(
            user=self.user, lender_name="Bank", amount=Decimal("400000.00"),
            date_borrowed=DAY - datetime.timedelta(days=20), receiving_account=self.wallet,
        )

    def test_repayment_fee_is_counted_exactly_once_as_an_expense(self):
        from apps.transactions.services import create_transaction

        create_transaction(
            user=self.user, account=self.wallet, kind="expense", category=None,
            amount=Decimal("50000.00"), fee_amount=Decimal("1000.00"), date=DAY, description="Shopping",
        )
        record_borrowing_repayment(
            user=self.user, borrowing=self.borrowing, amount=Decimal("100000.00"),
            fee_amount=Decimal("1500.00"), date=DAY, paying_account=self.wallet,
        )
        report = compute_monthly_report(self.user, DAY.year, DAY.month)

        self.assertEqual(report["repayment_fees"], Decimal("1500.00"))
        self.assertEqual(report["transaction_fees"], Decimal("1000.00"))
        # 50,000 purchase + 1,000 transaction fee + 1,500 repayment fee.
        self.assertEqual(report["total_expenses"], Decimal("52500.00"))
        # The 100,000 principal is a liability settlement, never an expense.
        self.assertEqual(report["loan_repayments_paid"], Decimal("100000.00"))
        self.assertEqual(report["total_income"], Decimal("0.00"))

    def test_existing_repayments_without_fees_do_not_change_report_totals(self):
        record_borrowing_repayment(
            user=self.user, borrowing=self.borrowing, amount=Decimal("100000.00"),
            date=DAY, paying_account=self.wallet,
        )
        report = compute_monthly_report(self.user, DAY.year, DAY.month)
        self.assertEqual(report["total_expenses"], Decimal("0.00"))
        self.assertEqual(report["repayment_fees"], Decimal("0.00"))


class RepaymentFeeFormTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="feeform@example.com", password="StrongPass123!")
        self.wallet = FinancialAccount.objects.create(
            user=self.user, name="Wallet", account_type="cash", opening_balance=Decimal("500000.00")
        )
        self.borrowing = create_borrowing(
            user=self.user, lender_name="Form Lender", amount=Decimal("200000.00"),
            date_borrowed=DAY - datetime.timedelta(days=5), receiving_account=self.wallet,
        )

    def _data(self, **overrides):
        data = {
            "amount": "100000.00",
            "fee_amount": "0",
            "date": DAY.isoformat(),
            "paying_account": self.wallet.pk,
            "notes": "",
        }
        data.update(overrides)
        return data

    def test_fee_defaults_to_zero(self):
        form = BorrowingRepaymentForm(user=self.user, borrowing=self.borrowing)
        self.assertEqual(form.fields["fee_amount"].initial, 0)
        self.assertFalse(form.fields["fee_amount"].required)

    def test_form_rejects_negative_fee(self):
        form = BorrowingRepaymentForm(self._data(fee_amount="-5"), user=self.user, borrowing=self.borrowing)
        self.assertFalse(form.is_valid())
        self.assertIn("fee_amount", form.errors)

    def test_form_rejects_overpayment(self):
        form = BorrowingRepaymentForm(self._data(amount="250000.00"), user=self.user, borrowing=self.borrowing)
        self.assertFalse(form.is_valid())
        self.assertIn("amount", form.errors)

    def test_form_allows_editing_upward_on_an_existing_repayment(self):
        repayment = record_borrowing_repayment(
            user=self.user, borrowing=self.borrowing, amount=Decimal("100000.00"),
            date=DAY, paying_account=self.wallet,
        )
        # 100,000 repaid leaves 100,000 outstanding; raising it back to 200,000
        # is legitimate and must not be read as an overpayment.
        form = BorrowingRepaymentForm(
            self._data(amount="200000.00"), user=self.user, borrowing=self.borrowing, instance=repayment
        )
        self.assertTrue(form.is_valid(), form.errors)


class RepaymentFeeWebAndApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="feeweb@example.com", password="StrongPass123!")
        self.user.profile.first_time_setup_complete = True
        self.user.profile.save()
        self.wallet = FinancialAccount.objects.create(
            user=self.user, name="Wallet", account_type="cash", opening_balance=Decimal("500000.00")
        )
        # Money landed elsewhere, so `wallet` stays at exactly 500,000.00.
        self.proceeds = FinancialAccount.objects.create(
            user=self.user, name="Loan Proceeds", account_type="bank", opening_balance=Decimal("0.00")
        )
        self.borrowing = create_borrowing(
            user=self.user, lender_name="Web Lender", amount=Decimal("300000.00"),
            date_borrowed=DAY - datetime.timedelta(days=7), receiving_account=self.proceeds,
        )
        self.client.login(email="feeweb@example.com", password="StrongPass123!")

    def _payload(self, **overrides):
        data = {
            "amount": "100000.00",
            "fee_amount": "1500.00",
            "date": DAY.isoformat(),
            "paying_account": self.wallet.pk,
            "notes": "",
        }
        data.update(overrides)
        return data

    def test_repayment_detail_page_shows_the_fee_field(self):
        response = self.client.get(reverse("debts:borrowing_detail", args=[self.borrowing.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="fee_amount"')

    def test_recording_a_repayment_with_a_fee_through_the_web_form(self):
        response = self.client.post(
            reverse("debts:borrowing_detail", args=[self.borrowing.pk]), self._payload()
        )
        self.assertEqual(response.status_code, 302)
        repayment = BorrowingRepayment.objects.get(borrowing=self.borrowing)
        self.assertEqual(repayment.fee_amount, Decimal("1500.00"))
        self.assertEqual(account_balance(self.wallet), Decimal("398500.00"))
        self.assertEqual(self.borrowing.outstanding(), Decimal("200000.00"))

    def test_editing_a_repayment_through_the_web_form(self):
        repayment = record_borrowing_repayment(
            user=self.user, borrowing=self.borrowing, amount=Decimal("100000.00"),
            fee_amount=Decimal("1500.00"), date=DAY, paying_account=self.wallet,
        )
        response = self.client.post(
            reverse("debts:repayment_update", args=[repayment.pk]),
            self._payload(amount="150000.00", fee_amount="2000.00"),
        )
        self.assertEqual(response.status_code, 302)
        repayment.refresh_from_db()
        self.assertEqual(repayment.amount, Decimal("150000.00"))
        self.assertEqual(repayment.fee_amount, Decimal("2000.00"))
        self.assertEqual(postings_of(repayment).count(), 2)
        self.assertEqual(account_balance(self.wallet), Decimal("348000.00"))

    def test_deleting_a_repayment_through_the_web_form(self):
        repayment = record_borrowing_repayment(
            user=self.user, borrowing=self.borrowing, amount=Decimal("100000.00"),
            fee_amount=Decimal("1500.00"), date=DAY, paying_account=self.wallet,
        )
        response = self.client.post(reverse("debts:repayment_delete", args=[repayment.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(BorrowingRepayment.objects.filter(pk=repayment.pk).exists())
        self.assertEqual(account_balance(self.wallet), Decimal("500000.00"))

    def test_another_user_cannot_edit_or_delete_this_repayment(self):
        repayment = record_borrowing_repayment(
            user=self.user, borrowing=self.borrowing, amount=Decimal("100000.00"),
            fee_amount=Decimal("1500.00"), date=DAY, paying_account=self.wallet,
        )
        other = User.objects.create_user(email="intruder@example.com", password="StrongPass123!")
        other_wallet = FinancialAccount.objects.create(
            user=other, name="Their Wallet", account_type="cash", opening_balance=Decimal("1000.00")
        )
        self.client.logout()
        self.client.login(email="intruder@example.com", password="StrongPass123!")
        self.assertEqual(self.client.get(reverse("debts:repayment_update", args=[repayment.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("debts:repayment_delete", args=[repayment.pk])).status_code, 404)
        post_response = self.client.post(
            reverse("debts:repayment_update", args=[repayment.pk]),
            self._payload(amount="1000.00", paying_account=other_wallet.pk),
        )
        self.assertEqual(post_response.status_code, 404)
        repayment.refresh_from_db()
        self.assertEqual(repayment.amount, Decimal("100000.00"))

    def test_api_accepts_and_exposes_the_fee(self):
        response = self.client.post(
            "/api/v1/borrowing-repayments/",
            {
                "borrowing": self.borrowing.pk,
                "amount": "100000.00",
                "fee_amount": "1500.00",
                "date": DAY.isoformat(),
                "paying_account": self.wallet.pk,
            },
            content_type="application/json",
        )
        self.assertIn(response.status_code, (200, 201), response.content)
        self.assertEqual(Decimal(str(response.json()["fee_amount"])), Decimal("1500.00"))
        self.assertEqual(account_balance(self.wallet), Decimal("398500.00"))
