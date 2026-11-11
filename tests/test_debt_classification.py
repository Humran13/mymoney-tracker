import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import FinancialAccount
from apps.accounts.services import account_balance
from apps.closing.services import balance_as_of
from apps.debts import services as debt_services
from apps.debts.models import Borrowing
from apps.users.models import User


class DebtTypeAccountingTests(TestCase):
    """Priority 1/2: noncash credit must never inflate cash; repayments
    must still behave correctly regardless of debt type."""

    def setUp(self):
        self.user = User.objects.create_user(email="credit@example.com", password="StrongPass123!")
        self.wallet = FinancialAccount.objects.create(
            user=self.user, name="Cash", account_type="cash", opening_balance=Decimal("0.00")
        )
        self.mtn = FinancialAccount.objects.create(
            user=self.user, name="MTN", account_type="mobile_money_mtn", opening_balance=Decimal("0.00")
        )
        self.bank = FinancialAccount.objects.create(
            user=self.user, name="Bank", account_type="bank", opening_balance=Decimal("0.00")
        )

    def _borrow(self, debt_type, amount="446000.00", receiving_account=None):
        return debt_services.create_borrowing(
            user=self.user, debt_type=debt_type, lender_name="Tasha Cargo", amount=Decimal(amount),
            date_borrowed=datetime.date.today(), receiving_account=receiving_account,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )

    def test_cash_loan_increases_cash_and_debt(self):
        b = self._borrow(Borrowing.DebtType.CASH_LOAN, receiving_account=self.wallet)
        self.assertEqual(account_balance(self.wallet), Decimal("446000.00"))
        self.assertEqual(b.outstanding(), Decimal("446000.00"))

    def test_goods_on_credit_increases_debt_only(self):
        b = self._borrow(Borrowing.DebtType.GOODS_CREDIT)
        self.assertEqual(account_balance(self.wallet), Decimal("0.00"))
        self.assertEqual(account_balance(self.mtn), Decimal("0.00"))
        self.assertEqual(b.outstanding(), Decimal("446000.00"))

    def test_services_on_credit_increases_debt_only(self):
        b = self._borrow(Borrowing.DebtType.SERVICES_CREDIT)
        self.assertEqual(account_balance(self.wallet), Decimal("0.00"))
        self.assertEqual(b.outstanding(), Decimal("446000.00"))

    def test_other_money_owed_does_not_create_cash(self):
        b = self._borrow(Borrowing.DebtType.OTHER)
        self.assertEqual(account_balance(self.wallet), Decimal("0.00"))
        self.assertEqual(b.outstanding(), Decimal("446000.00"))

    def test_no_artificial_zero_posting_for_noncash_debt(self):
        from apps.accounts.models import AccountPosting

        b = self._borrow(Borrowing.DebtType.GOODS_CREDIT)
        self.assertEqual(AccountPosting.objects.filter(object_id=b.pk).count(), 0)

    def test_existing_cash_loan_records_default_to_cash_loan_type(self):
        # Simulates a pre-Phase-12 record: debt_type not passed explicitly.
        b = debt_services.create_borrowing(
            user=self.user, lender_name="Old Lender", amount=Decimal("100000.00"),
            date_borrowed=datetime.date.today(), receiving_account=self.wallet,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        self.assertEqual(b.debt_type, Borrowing.DebtType.CASH_LOAN)
        self.assertEqual(account_balance(self.wallet), Decimal("100000.00"))

    def test_goods_credit_never_becomes_income_or_expense(self):
        from apps.transactions.models import Transaction

        self._borrow(Borrowing.DebtType.GOODS_CREDIT)
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 0)

    def test_partial_repayment_on_goods_credit_reduces_cash_and_debt(self):
        b = self._borrow(Borrowing.DebtType.GOODS_CREDIT)
        debt_services.record_borrowing_repayment(
            user=self.user, borrowing=b, amount=Decimal("100000.00"),
            date=datetime.date.today(), paying_account=self.wallet, notes="",
        )
        self.assertEqual(account_balance(self.wallet), Decimal("-100000.00"))
        self.assertEqual(b.outstanding(), Decimal("346000.00"))

    def test_repayments_from_different_accounts_settle_goods_credit(self):
        b = self._borrow(Borrowing.DebtType.GOODS_CREDIT)
        debt_services.record_borrowing_repayment(
            user=self.user, borrowing=b, amount=Decimal("100000.00"),
            date=datetime.date.today(), paying_account=self.wallet, notes="",
        )
        debt_services.record_borrowing_repayment(
            user=self.user, borrowing=b, amount=Decimal("200000.00"),
            date=datetime.date.today(), paying_account=self.mtn, notes="",
        )
        debt_services.record_borrowing_repayment(
            user=self.user, borrowing=b, amount=Decimal("146000.00"),
            date=datetime.date.today(), paying_account=self.bank, notes="",
        )
        b.refresh_from_db()
        self.assertEqual(b.outstanding(), Decimal("0.00"))
        self.assertEqual(b.status, Borrowing.Status.SETTLED)
        self.assertEqual(account_balance(self.wallet), Decimal("-100000.00"))
        self.assertEqual(account_balance(self.mtn), Decimal("-200000.00"))
        self.assertEqual(account_balance(self.bank), Decimal("-146000.00"))

    def test_overpayment_still_rejected_for_goods_credit(self):
        b = self._borrow(Borrowing.DebtType.GOODS_CREDIT, amount="100000.00")
        with self.assertRaises(ValueError):
            debt_services.record_borrowing_repayment(
                user=self.user, borrowing=b, amount=Decimal("999999.00"),
                date=datetime.date.today(), paying_account=self.wallet, notes="",
            )

    def test_repayment_does_not_create_duplicate_expense(self):
        from apps.transactions.models import Transaction

        b = self._borrow(Borrowing.DebtType.GOODS_CREDIT)
        debt_services.record_borrowing_repayment(
            user=self.user, borrowing=b, amount=Decimal("100000.00"),
            date=datetime.date.today(), paying_account=self.wallet, notes="",
        )
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 0)


class ReclassifyBorrowingTests(TestCase):
    """Priority 5: changing debt type must preserve history and only adjust
    the posting, atomically, with no duplicate postings created."""

    def setUp(self):
        self.user = User.objects.create_user(email="reclass@example.com", password="StrongPass123!")
        self.wallet = FinancialAccount.objects.create(
            user=self.user, name="Cash", account_type="cash", opening_balance=Decimal("0.00")
        )
        self.bank = FinancialAccount.objects.create(
            user=self.user, name="Bank", account_type="bank", opening_balance=Decimal("0.00")
        )

    def test_reclassify_cash_loan_to_goods_credit_removes_cash_posting(self):
        b = debt_services.create_borrowing(
            user=self.user, debt_type=Borrowing.DebtType.CASH_LOAN, lender_name="Tasha Cargo",
            amount=Decimal("446000.00"), date_borrowed=datetime.date.today(), receiving_account=self.wallet,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        self.assertEqual(account_balance(self.wallet), Decimal("446000.00"))

        debt_services.reclassify_borrowing(borrowing=b, debt_type=Borrowing.DebtType.GOODS_CREDIT)
        b.refresh_from_db()
        self.assertEqual(b.debt_type, Borrowing.DebtType.GOODS_CREDIT)
        self.assertIsNone(b.receiving_account)
        self.assertEqual(account_balance(self.wallet), Decimal("0.00"))
        self.assertEqual(b.outstanding(), Decimal("446000.00"))
        self.assertEqual(b.lender_name, "Tasha Cargo")

    def test_reclassify_preserves_repayment_history(self):
        b = debt_services.create_borrowing(
            user=self.user, debt_type=Borrowing.DebtType.CASH_LOAN, lender_name="Tasha Cargo",
            amount=Decimal("446000.00"), date_borrowed=datetime.date.today(), receiving_account=self.wallet,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        debt_services.record_borrowing_repayment(
            user=self.user, borrowing=b, amount=Decimal("100000.00"),
            date=datetime.date.today(), paying_account=self.bank, notes="",
        )
        debt_services.reclassify_borrowing(borrowing=b, debt_type=Borrowing.DebtType.GOODS_CREDIT)
        b.refresh_from_db()
        self.assertEqual(b.repayments.count(), 1)
        self.assertEqual(b.total_repaid(), Decimal("100000.00"))
        self.assertEqual(b.outstanding(), Decimal("346000.00"))

    def test_reclassify_goods_credit_to_cash_loan_adds_cash_posting(self):
        b = debt_services.create_borrowing(
            user=self.user, debt_type=Borrowing.DebtType.GOODS_CREDIT, lender_name="Tasha Cargo",
            amount=Decimal("446000.00"), date_borrowed=datetime.date.today(), receiving_account=None,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        debt_services.reclassify_borrowing(borrowing=b, debt_type=Borrowing.DebtType.CASH_LOAN, receiving_account=self.wallet)
        b.refresh_from_db()
        self.assertEqual(account_balance(self.wallet), Decimal("446000.00"))
        self.assertEqual(b.receiving_account, self.wallet)

    def test_reclassify_does_not_duplicate_postings(self):
        from apps.accounts.models import AccountPosting

        b = debt_services.create_borrowing(
            user=self.user, debt_type=Borrowing.DebtType.CASH_LOAN, lender_name="X",
            amount=Decimal("50000.00"), date_borrowed=datetime.date.today(), receiving_account=self.wallet,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        debt_services.reclassify_borrowing(borrowing=b, debt_type=Borrowing.DebtType.CASH_LOAN, receiving_account=self.wallet)
        self.assertEqual(AccountPosting.objects.filter(object_id=b.pk).count(), 1)

    def test_preview_shows_expected_balance_change_without_writing(self):
        b = debt_services.create_borrowing(
            user=self.user, debt_type=Borrowing.DebtType.CASH_LOAN, lender_name="X",
            amount=Decimal("50000.00"), date_borrowed=datetime.date.today(), receiving_account=self.wallet,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        preview = debt_services.preview_reclassify_borrowing(b, Borrowing.DebtType.GOODS_CREDIT)
        self.assertEqual(preview[self.wallet], Decimal("-50000.00"))
        # Nothing was actually written.
        self.assertEqual(account_balance(self.wallet), Decimal("50000.00"))


class ReclassifyViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="reclassview@example.com", password="StrongPass123!")
        self.user.profile.first_time_setup_complete = True
        self.user.profile.save()
        self.wallet = FinancialAccount.objects.create(
            user=self.user, name="Cash", account_type="cash", opening_balance=Decimal("0.00")
        )
        self.borrowing = debt_services.create_borrowing(
            user=self.user, debt_type=Borrowing.DebtType.CASH_LOAN, lender_name="Tasha Cargo",
            amount=Decimal("446000.00"), date_borrowed=datetime.date.today(), receiving_account=self.wallet,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        self.client.login(email="reclassview@example.com", password="StrongPass123!")

    def test_preview_step_does_not_modify_anything(self):
        self.client.post(
            reverse("debts:borrowing_reclassify", args=[self.borrowing.pk]),
            {"debt_type": "goods_credit"},
        )
        self.assertEqual(account_balance(self.wallet), Decimal("446000.00"))

    def test_confirm_step_applies_change(self):
        self.client.post(
            reverse("debts:borrowing_reclassify", args=[self.borrowing.pk]),
            {"debt_type": "goods_credit", "confirm": "1"},
        )
        self.assertEqual(account_balance(self.wallet), Decimal("0.00"))

    def test_cannot_reclassify_another_users_borrowing(self):
        other = User.objects.create_user(email="otherreclass@example.com", password="StrongPass123!")
        self.client.logout()
        self.client.login(email="otherreclass@example.com", password="StrongPass123!")
        response = self.client.post(
            reverse("debts:borrowing_reclassify", args=[self.borrowing.pk]),
            {"debt_type": "goods_credit", "confirm": "1"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(account_balance(self.wallet), Decimal("446000.00"))


class BorrowingFormValidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="formval@example.com", password="StrongPass123!")
        self.user.profile.first_time_setup_complete = True
        self.user.profile.save()
        self.wallet = FinancialAccount.objects.create(
            user=self.user, name="Cash", account_type="cash", opening_balance=Decimal("0.00")
        )
        self.client.login(email="formval@example.com", password="StrongPass123!")

    def test_cash_loan_without_account_is_rejected(self):
        response = self.client.post(
            reverse("debts:borrowing_create"),
            {"debt_type": "cash_loan", "lender_name": "X", "amount": "1000", "date_borrowed": "2026-11-01",
             "receiving_account": "", "interest_amount": "0", "notes": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "requires the account")

    def test_goods_credit_without_account_succeeds(self):
        response = self.client.post(
            reverse("debts:borrowing_create"),
            {"debt_type": "goods_credit", "lender_name": "Tasha Cargo", "amount": "446000", "date_borrowed": "2026-11-01",
             "receiving_account": "", "interest_amount": "0", "notes": ""},
        )
        self.assertEqual(response.status_code, 302)
        b = Borrowing.objects.get(user=self.user, lender_name="Tasha Cargo")
        self.assertIsNone(b.receiving_account)


class OpeningBalanceCutoffHistoricalTests(TestCase):
    """Priority 3: balance_as_of() must respect each account's cutoff and
    refuse to invent a figure for dates before it."""

    def setUp(self):
        self.user = User.objects.create_user(email="cutoff@example.com", password="StrongPass123!")

    def test_balance_as_of_before_cutoff_is_none(self):
        account = FinancialAccount.objects.create(
            user=self.user, name="Wallet", account_type="cash",
            opening_balance=Decimal("631200.00"), opening_balance_date=datetime.date(2026, 11, 9),
        )
        self.assertIsNone(balance_as_of(self.user, datetime.date(2026, 11, 4)))
        self.assertIsNone(balance_as_of(self.user, datetime.date(2026, 11, 8)))

    def test_balance_as_of_on_and_after_cutoff_is_correct(self):
        account = FinancialAccount.objects.create(
            user=self.user, name="Wallet", account_type="cash",
            opening_balance=Decimal("631200.00"), opening_balance_date=datetime.date(2026, 11, 9),
        )
        self.assertEqual(balance_as_of(self.user, datetime.date(2026, 11, 9)), Decimal("631200.00"))

    def test_historical_postings_before_cutoff_never_change_declared_opening_balance(self):
        from apps.debts import services as debt_services

        account = FinancialAccount.objects.create(
            user=self.user, name="Wallet", account_type="cash",
            opening_balance=Decimal("631200.00"), opening_balance_date=datetime.date(2026, 11, 9),
        )
        # A historical cash loan dated before the cutoff — already baked
        # into the opening balance, must not be added a second time.
        debt_services.create_borrowing(
            user=self.user, debt_type=Borrowing.DebtType.CASH_LOAN, lender_name="Old Loan",
            amount=Decimal("850000.00"), date_borrowed=datetime.date(2026, 11, 4), receiving_account=account,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        self.assertEqual(account_balance(account), Decimal("631200.00"))
        self.assertEqual(balance_as_of(self.user, datetime.date(2026, 11, 9)), Decimal("631200.00"))

    def test_postings_on_and_after_cutoff_do_affect_balance(self):
        from apps.transactions.services import create_transaction

        account = FinancialAccount.objects.create(
            user=self.user, name="Wallet", account_type="cash",
            opening_balance=Decimal("631200.00"), opening_balance_date=datetime.date(2026, 11, 9),
        )
        create_transaction(
            user=self.user, account=account, kind="expense", category=None,
            amount=Decimal("20000.00"), fee_amount=Decimal("0"), date=datetime.date(2026, 11, 10), description="t",
        )
        self.assertEqual(account_balance(account), Decimal("611200.00"))
        self.assertEqual(balance_as_of(self.user, datetime.date(2026, 11, 10)), Decimal("611200.00"))

    def test_no_cutoff_account_behaves_as_before(self):
        account = FinancialAccount.objects.create(
            user=self.user, name="Bank", account_type="bank", opening_balance=Decimal("100000.00")
        )
        self.assertEqual(balance_as_of(self.user, datetime.date.today()), Decimal("100000.00"))

    def test_daily_closing_breakdown_marks_unavailable_before_cutoff(self):
        from apps.closing.services import day_breakdown

        FinancialAccount.objects.create(
            user=self.user, name="Wallet", account_type="cash",
            opening_balance=Decimal("631200.00"), opening_balance_date=datetime.date(2026, 11, 9),
        )
        breakdown = day_breakdown(self.user, datetime.date(2026, 11, 5))
        self.assertTrue(breakdown["balance_unavailable"])
        self.assertIsNone(breakdown["opening_balance"])


class DebtIsolationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="owner@example.com", password="StrongPass123!")
        self.owner.profile.first_time_setup_complete = True
        self.owner.profile.save()
        self.other = User.objects.create_user(email="other@example.com", password="StrongPass123!")
        self.other.profile.first_time_setup_complete = True
        self.other.profile.save()
        self.wallet = FinancialAccount.objects.create(
            user=self.owner, name="Cash", account_type="cash", opening_balance=Decimal("0.00")
        )
        self.borrowing = debt_services.create_borrowing(
            user=self.owner, debt_type=Borrowing.DebtType.GOODS_CREDIT, lender_name="Tasha Cargo",
            amount=Decimal("446000.00"), date_borrowed=datetime.date.today(), receiving_account=None,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )

    def test_other_user_cannot_view_borrowing_detail(self):
        self.client.login(email="other@example.com", password="StrongPass123!")
        response = self.client.get(reverse("debts:borrowing_detail", args=[self.borrowing.pk]))
        self.assertEqual(response.status_code, 404)

    def test_other_user_cannot_repay_owners_borrowing_via_forged_id(self):
        self.client.login(email="other@example.com", password="StrongPass123!")
        other_wallet = FinancialAccount.objects.create(
            user=self.other, name="Cash", account_type="cash", opening_balance=Decimal("1000000.00")
        )
        response = self.client.post(
            reverse("debts:borrowing_detail", args=[self.borrowing.pk]),
            {"amount": "1000", "date": datetime.date.today().isoformat(), "paying_account": other_wallet.pk, "notes": ""},
        )
        self.assertEqual(response.status_code, 404)
