"""Grouped person payments, the DAKA scenario, and the regressions behind them.

Root causes covered (see PR description):
- Bug 2: repayment forms are per transaction, but since PR #2 the dashboard
  shows one grouped balance per person (and offsets shrink per-record balances).
  Typing the grouped balance into one transaction's form was rejected by a
  *non-field* error that lending_detail.html never rendered, so nothing
  happened and no message appeared.
- Bug 1: PR #2 replaced one clickable card per transaction with a grouped card
  whose only links were inside a collapsed <details>.
"""

import datetime
import uuid
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import AccountPosting, FinancialAccount
from apps.accounts.services import account_balance
from apps.core.models import IdempotencyKey
from apps.debts import payments, settlements
from apps.debts import services as ds
from apps.debts.models import Borrowing, Lending, LendingRepayment
from apps.users.models import User

D = Decimal
TODAY = datetime.date.today()


def user_(email):
    return User.objects.create_user(email=email, password="StrongPass123!")


def acct(user, name, opening="0.00", kind="cash"):
    return FinancialAccount.objects.create(user=user, name=name, account_type=kind, opening_balance=D(opening))


def lend(user, wallet, name, amount, *, date=TODAY, contact=""):
    return ds.create_lending(
        user=user, borrower_name=name, contact=contact, amount=D(amount), date_lent=date,
        source_account=wallet, due_date=None, interest_amount=D("0.00"), notes="",
    )


def owe(user, wallet, name, amount, *, date=TODAY):
    return ds.create_borrowing(
        user=user, debt_type="cash_loan", lender_name=name, contact="", amount=D(amount), date_borrowed=date,
        receiving_account=wallet, due_date=None, interest_amount=D("0.00"), notes="",
    )


def pay(user, side, name, amount, account, *, interest="0", fee="0", token=None, contact=""):
    return payments.record_person_payment(
        user=user, side=side, name=name, contact=contact, principal=D(amount), interest=D(interest), fee=D(fee),
        date=TODAY, account=account, notes="", token=token or uuid.uuid4(),
    )


class DakaScenario(TestCase):
    """DAKA owes 104,000 over two loans; 20,000 offset; 84,000 arrives by Airtel."""

    def setUp(self):
        self.user = user_("daka@example.com")
        self.cash = acct(self.user, "Cash", "1000000.00")
        self.airtel = acct(self.user, "Airtel Money", "0.00", "mobile_money_airtel")
        self.l1 = lend(self.user, self.cash, "DAKA", "60000", date=TODAY - datetime.timedelta(days=5))
        self.l2 = lend(self.user, self.cash, "DAKA", "44000", date=TODAY - datetime.timedelta(days=2))
        owe(self.user, self.cash, "DAKA", "20000")
        settlements.settle_with_person(
            user=self.user, name="DAKA", amount=D("20000"), date=TODAY, token=uuid.uuid4()
        )
        self.cash_after_setup = account_balance(self.cash)
        self.bystander = lend(self.user, self.cash, "Other", "10000")
        self.cash_before = account_balance(self.cash)

    def test_reproduces_bug_single_form_rejects_grouped_balance_silently(self):
        """The reported failure: 84,000 total, but the oldest loan has 40,000."""
        self.assertEqual(self.l1.outstanding(), D("40000.00"))
        self.client.force_login(self.user)
        resp = self.client.post(
            reverse("debts:lending_detail", args=[self.l1.pk]),
            {"principal_amount": "84000", "interest_amount": "0", "date": TODAY.isoformat(),
             "receiving_account": self.airtel.pk, "notes": ""},
        )
        self.assertEqual(resp.status_code, 200)  # not redirected: nothing recorded
        self.assertEqual(LendingRepayment.objects.count(), 0)
        # the fix: the reason is now visible and the grouped route is offered
        self.assertContains(resp, "more principal than")
        self.assertContains(resp, "Record one payment for all of their transactions")

    def test_full_grouped_repayment(self):
        results = pay(self.user, "borrower", "DAKA", "84000", self.airtel)
        self.assertEqual([part.principal_amount for _r, part in results], [D("40000.00"), D("44000.00")])
        self.assertEqual(account_balance(self.airtel), D("84000.00"))  # exactly once
        self.assertEqual(account_balance(self.cash), self.cash_before)
        for lending in (self.l1, self.l2):
            lending.refresh_from_db()
            self.assertEqual(lending.outstanding(), D("0.00"))
            self.assertEqual(lending.status, Lending.Status.SETTLED)
        self.assertEqual(
            AccountPosting.objects.filter(account=self.airtel).count(), 2
        )  # one LEND_REPAY posting per underlying transaction, summing to 84,000
        self.assertEqual(
            sum(p.amount for p in AccountPosting.objects.filter(account=self.airtel)), D("84000.00")
        )
        # offset is preserved, bystander untouched, DAKA gone from borrowers
        self.assertEqual(self.l1.total_offset(), D("20000.00"))
        self.bystander.refresh_from_db()
        self.assertEqual(self.bystander.outstanding(), D("10000.00"))
        names = [g["name"] for g in settlements.debt_positions(self.user)["borrowers"]]
        self.assertEqual(names, ["Other"])
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(reverse("debts:dashboard")), 'data-person="DAKA"')

    def test_partial_then_rest(self):
        pay(self.user, "borrower", "DAKA", "50000", self.airtel)
        self.l1.refresh_from_db(); self.l2.refresh_from_db()
        self.assertEqual((self.l1.outstanding(), self.l2.outstanding()), (D("0.00"), D("34000.00")))
        self.assertEqual(self.l1.status, Lending.Status.SETTLED)
        self.assertEqual(self.l2.status, Lending.Status.ACTIVE)
        pay(self.user, "borrower", "DAKA", "34000", self.airtel)
        self.assertEqual(account_balance(self.airtel), D("84000.00"))

    def test_cannot_overpay(self):
        with self.assertRaises(ValueError):
            pay(self.user, "borrower", "DAKA", "84000.01", self.airtel)
        self.assertEqual(account_balance(self.airtel), D("0.00"))
        self.assertEqual(LendingRepayment.objects.count(), 0)
        self.assertEqual(IdempotencyKey.objects.count(), 0)  # rolled back with the rest

    def test_duplicate_token_posts_once(self):
        token = uuid.uuid4()
        pay(self.user, "borrower", "DAKA", "30000", self.airtel, token=token)
        with self.assertRaises(payments.DuplicatePayment):
            pay(self.user, "borrower", "DAKA", "30000", self.airtel, token=token)
        self.assertEqual(account_balance(self.airtel), D("30000.00"))

    def test_atomic_rollback_on_failure(self):
        from unittest import mock
        real = ds.record_lending_repayment
        calls = {"n": 0}

        def flaky(**kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("boom")
            return real(**kw)

        with mock.patch.object(ds, "record_lending_repayment", flaky):
            with self.assertRaises(RuntimeError):
                pay(self.user, "borrower", "DAKA", "84000", self.airtel)
        self.assertEqual(LendingRepayment.objects.count(), 0)
        self.assertEqual(account_balance(self.airtel), D("0.00"))
        self.assertEqual(IdempotencyKey.objects.count(), 0)
        self.l1.refresh_from_db()
        self.assertEqual(self.l1.status, Lending.Status.ACTIVE)

    def test_interest_posted_separately_once(self):
        pay(self.user, "borrower", "DAKA", "84000", self.airtel, interest="5000")
        self.assertEqual(account_balance(self.airtel), D("89000.00"))
        self.assertEqual(
            AccountPosting.objects.filter(account=self.airtel, source_type=AccountPosting.SourceType.INCOME).count(), 1
        )

    def test_undo_offset_after_payment_blocked_or_safe(self):
        settlement = self.l1.offset_lines.get().settlement
        pay(self.user, "borrower", "DAKA", "84000", self.airtel)
        blockers = settlements.reversal_blockers(settlement)
        if not blockers:  # reversal would be allowed: it must not move any account
            before = account_balance(self.airtel)
            settlements.reverse_settlement(user=self.user, settlement_id=settlement.pk, date=TODAY)
            self.assertEqual(account_balance(self.airtel), before)

    def test_undo_offset_before_payment_still_works(self):
        settlement = self.l1.offset_lines.get().settlement
        settlements.reverse_settlement(user=self.user, settlement_id=settlement.pk, date=TODAY)
        self.assertEqual(payments.total_outstanding(payments.person_records(self.user, "borrower", "DAKA")), D("104000.00"))
        pay(self.user, "borrower", "DAKA", "104000", self.airtel)
        self.assertEqual(account_balance(self.airtel), D("104000.00"))

    def test_offset_after_repayment(self):
        pay(self.user, "borrower", "DAKA", "80000", self.airtel)  # 4,000 left, 20,000 owed by me, 20,000 offset used
        self.assertEqual(payments.total_outstanding(payments.person_records(self.user, "borrower", "DAKA")), D("4000.00"))
        owe(self.user, self.cash, "DAKA", "10000")
        settlements.settle_with_person(user=self.user, name="DAKA", amount=D("4000"), date=TODAY, token=uuid.uuid4())
        self.assertEqual(payments.person_records(self.user, "borrower", "DAKA"), [])
        self.assertEqual(account_balance(self.airtel), D("80000.00"))


class GroupedPaymentViews(TestCase):
    def setUp(self):
        self.user = user_("views@example.com")
        self.cash = acct(self.user, "Cash", "500000.00")
        self.airtel = acct(self.user, "Airtel", "0.00")
        self.l1 = lend(self.user, self.cash, "Kato", "30000", date=TODAY - datetime.timedelta(days=3))
        self.l2 = lend(self.user, self.cash, "Kato", "20000")
        self.solo = lend(self.user, self.cash, "Solo", "5000")
        self.client.force_login(self.user)
        self.url = reverse("debts:person_payment", args=["borrower"])

    def _data(self, **extra):
        data = {"name": "Kato", "contact": "", "amount": "35000", "interest_amount": "", "date": TODAY.isoformat(),
                "account": self.airtel.pk, "notes": "", "token": str(uuid.uuid4())}
        data.update(extra)
        return data

    def test_review_shows_allocation_and_changes_nothing(self):
        resp = self.client.post(self.url, self._data())
        self.assertContains(resp, "payment-confirmation")
        self.assertContains(resp, "oldest first")
        self.assertEqual(LendingRepayment.objects.count(), 0)

    def test_confirm_records_once_even_when_resubmitted(self):
        data = self._data(confirm="1")
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertEqual(self.client.post(self.url, data).status_code, 302)  # double click / refresh
        self.assertEqual(account_balance(self.airtel), D("35000.00"))
        self.assertEqual(LendingRepayment.objects.count(), 2)

    def test_over_amount_rejected_with_message(self):
        resp = self.client.post(self.url, self._data(amount="50000.01"))
        self.assertContains(resp, "more than the 50,000.00 still outstanding")
        self.assertEqual(LendingRepayment.objects.count(), 0)

    def test_foreign_account_rejected(self):
        other = user_("o@example.com")
        theirs = acct(other, "Theirs")
        resp = self.client.post(self.url, self._data(account=theirs.pk, confirm="1"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(LendingRepayment.objects.count(), 0)

    def test_other_users_person_not_visible(self):
        other = user_("o2@example.com")
        self.client.force_login(other)
        resp = self.client.post(self.url, self._data(confirm="1", account=acct(other, "X").pk))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(LendingRepayment.objects.count(), 0)

    def test_person_detail_and_dashboard_links(self):
        html = self.client.get(reverse("debts:dashboard")).content.decode()
        self.assertIn(reverse("debts:lending_detail", args=[self.solo.pk]), html)  # single txn: direct
        self.assertIn(reverse("debts:person_detail", args=["borrower"]) + "?name=Kato", html)  # multiple
        self.assertIn("record-payment", html)
        detail = self.client.get(reverse("debts:person_detail", args=["borrower"]), {"name": "Kato"})
        self.assertContains(detail, "50,000")
        self.assertContains(detail, "Record payment")

    def test_lender_side_grouped_payment_with_fee(self):
        o1 = owe(self.user, self.cash, "Bank Bob", "30000", date=TODAY - datetime.timedelta(days=4))
        o2 = owe(self.user, self.cash, "Bank Bob", "20000")
        before = account_balance(self.cash)
        pay(self.user, "lender", "Bank Bob", "40000", self.cash, fee="500")
        self.assertEqual(account_balance(self.cash), before - D("40500.00"))
        o1.refresh_from_db(); o2.refresh_from_db()
        self.assertEqual((o1.outstanding(), o2.outstanding()), (D("0.00"), D("10000.00")))
        with self.assertRaises(ValueError):
            pay(self.user, "lender", "Bank Bob", "10000.01", self.cash)
        self.assertEqual(Borrowing.objects.filter(user=self.user, status="settled").count(), 1)

    def test_single_transaction_repayment_still_works(self):
        resp = self.client.post(
            reverse("debts:lending_detail", args=[self.solo.pk]),
            {"principal_amount": "5000", "interest_amount": "0", "date": TODAY.isoformat(),
             "receiving_account": self.airtel.pk, "notes": ""},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(account_balance(self.airtel), D("5000.00"))
