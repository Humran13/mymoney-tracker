"""API writes on debts must keep the ledger in step (same rules as the web editor)."""

import datetime
import uuid
from decimal import Decimal
from unittest import mock

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from apps.accounts.models import AccountPosting, FinancialAccount
from apps.accounts.services import account_balance
from apps.debts import services as ds
from apps.debts import settlements
from apps.debts.models import Borrowing, BorrowingRepayment, Lending, LendingRepayment
from apps.users.models import User

D = Decimal
TODAY = datetime.date.today()
JAN, FEB = datetime.date(2025, 1, 10), datetime.date(2025, 2, 15)
START = datetime.date(2020, 1, 1)


def postings(obj):
    return AccountPosting.objects.filter(content_type=ContentType.objects.get_for_model(obj), object_id=obj.pk)


class ApiBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="api@example.com", password="StrongPass123!")
        mk = lambda name, bal, start=START: FinancialAccount.objects.create(  # noqa: E731
            user=self.user, name=name, account_type="cash", opening_balance=D(bal), opening_balance_date=start
        )
        self.cash, self.bank, self.fresh = mk("Cash", "1000000"), mk("Bank", "0"), mk("Fresh", "500000", TODAY)
        self.client.force_login(self.user)

    def bal(self, account):
        return account_balance(FinancialAccount.objects.get(pk=account.pk))

    def lend(self, amount="40000", name="DAKA", date=JAN, account=None, debt_type="cash_loan"):
        return ds.create_lending(
            user=self.user, debt_type=debt_type, borrower_name=name, contact="", amount=D(amount), date_lent=date,
            source_account=account or self.cash, due_date=None, interest_amount=D("0"), notes="",
        )

    def borrow(self, amount="100000", name="Bank", date=JAN, account=None, debt_type="cash_loan"):
        return ds.create_borrowing(
            user=self.user, debt_type=debt_type, lender_name=name, contact="", amount=D(amount), date_borrowed=date,
            receiving_account=account or self.cash, due_date=None, interest_amount=D("0"), notes="",
        )

    def patch(self, path, pk, data):
        return self.client.patch(f"/api/v1/{path}/{pk}/", data, content_type="application/json")


class LendingApiTests(ApiBase):
    def test_amount_edit_resyncs_the_posting_once(self):
        loan = self.lend("40000")
        self.assertEqual(self.bal(self.cash), D("960000.00"))
        r = self.patch("lendings", loan.pk, {"amount": "60000"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.bal(self.cash), D("940000.00"))
        self.assertEqual(postings(loan).count(), 1)

    def test_account_change_moves_the_cash(self):
        loan = self.lend("40000")
        self.assertEqual(self.patch("lendings", loan.pk, {"source_account": self.bank.pk}).status_code, 200)
        self.assertEqual((self.bal(self.cash), self.bal(self.bank)), (D("1000000.00"), D("-40000.00")))
        self.assertEqual(postings(loan).count(), 1)

    def test_put_full_update_works_and_never_duplicates(self):
        loan = self.lend("40000")
        body = {"debt_type": "cash_loan", "borrower_name": "DAKA", "contact": "", "amount": "50000",
                "date_lent": JAN.isoformat(), "source_account": self.cash.pk, "interest_amount": "0", "notes": "x"}
        for _ in range(2):
            self.assertEqual(self.client.put(f"/api/v1/lendings/{loan.pk}/", body, content_type="application/json").status_code, 200)
        self.assertEqual(self.bal(self.cash), D("950000.00"))
        self.assertEqual(postings(loan).count(), 1)

    def test_date_before_account_start_is_history_not_cash(self):
        loan = self.lend("40000", date=TODAY, account=self.fresh)
        self.assertEqual(self.bal(self.fresh), D("460000.00"))
        old = (TODAY - datetime.timedelta(days=90)).isoformat()
        self.assertEqual(self.patch("lendings", loan.pk, {"date_lent": old}).status_code, 200)
        self.assertEqual(self.bal(self.fresh), D("500000.00"))
        self.assertEqual(postings(loan).count(), 1)

    def test_cash_noncash_conversion_both_ways(self):
        loan = self.lend("40000")
        self.assertEqual(self.patch("lendings", loan.pk, {"debt_type": "goods_credit"}).status_code, 200)
        loan.refresh_from_db()
        self.assertIsNone(loan.source_account_id)
        self.assertEqual((postings(loan).count(), self.bal(self.cash)), (0, D("1000000.00")))
        self.assertEqual(self.patch("lendings", loan.pk, {"debt_type": "cash_loan"}).status_code, 400)  # needs an account
        self.assertEqual(
            self.patch("lendings", loan.pk, {"debt_type": "cash_loan", "source_account": self.cash.pk}).status_code, 200
        )
        self.assertEqual(self.bal(self.cash), D("960000.00"))

    def test_noncash_partial_edit_does_not_demand_an_account(self):
        loan = self.lend("5000", debt_type="goods_credit")
        self.assertEqual(self.patch("lendings", loan.pk, {"notes": "n"}).status_code, 200)
        self.assertEqual(self.patch("lendings", loan.pk, {"amount": "7000"}).status_code, 200)
        self.assertEqual(AccountPosting.objects.count(), 0)

    def test_cannot_cut_below_what_was_received_or_offset(self):
        loan = self.lend("40000")
        ds.record_lending_repayment(user=self.user, lending=loan, principal_amount=D("30000"), interest_amount=D("0"),
                                    date=FEB, receiving_account=self.bank, notes="")
        r = self.patch("lendings", loan.pk, {"amount": "20000"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("already been repaid", str(r.json()))
        loan.refresh_from_db()
        self.assertEqual((loan.amount, self.bal(self.cash), self.bal(self.bank)), (D("40000.00"), D("960000.00"), D("30000.00")))

    def test_settlement_participant_cannot_be_moved_to_another_person(self):
        self.lend("104000")
        self.borrow("20000", name="DAKA", debt_type="goods_credit", account=None)
        settlements.settle_with_person(user=self.user, name="DAKA", amount=D("20000"), date=TODAY, token=uuid.uuid4())
        loan = Lending.objects.get()
        self.assertEqual(self.patch("lendings", loan.pk, {"borrower_name": "Other"}).status_code, 400)
        self.assertEqual(self.patch("lendings", loan.pk, {"amount": "10000"}).status_code, 400)
        loan.refresh_from_db()
        self.assertEqual((loan.borrower_name, loan.amount), ("DAKA", D("104000.00")))

    def test_atomic_rollback_when_ledger_sync_fails(self):
        loan = self.lend("40000")
        with mock.patch("apps.debts.editing.sync_postings", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.patch("lendings", loan.pk, {"amount": "90000"})
        loan.refresh_from_db()
        self.assertEqual((loan.amount, postings(loan).count(), self.bal(self.cash)), (D("40000.00"), 1, D("960000.00")))

    def test_delete_removes_record_and_postings(self):
        loan = self.lend("40000")
        rep = ds.record_lending_repayment(user=self.user, lending=loan, principal_amount=D("10000"),
                                          interest_amount=D("500"), date=FEB, receiving_account=self.bank, notes="")
        self.assertEqual(self.client.delete(f"/api/v1/lendings/{loan.pk}/").status_code, 204)
        self.assertFalse(Lending.objects.exists())
        self.assertEqual(AccountPosting.objects.count(), 0)
        self.assertEqual((self.bal(self.cash), self.bal(self.bank)), (D("1000000.00"), D("0.00")))
        self.assertFalse(LendingRepayment.objects.filter(pk=rep.pk).exists())

    def test_delete_of_settled_participant_is_refused(self):
        self.lend("104000")
        self.borrow("20000", name="DAKA", debt_type="goods_credit", account=None)
        settlements.settle_with_person(user=self.user, name="DAKA", amount=D("20000"), date=TODAY, token=uuid.uuid4())
        for path, obj in (("lendings", Lending.objects.get()), ("borrowings", Borrowing.objects.get())):
            self.assertEqual(self.client.delete(f"/api/v1/{path}/{obj.pk}/").status_code, 400)
        self.assertEqual((Lending.objects.count(), Borrowing.objects.count()), (1, 1))
        self.assertEqual(self.bal(self.cash), D("896000.00"))


class BorrowingApiTests(ApiBase):
    def test_amount_account_type_edits_keep_ledger(self):
        debt = self.borrow("100000")
        self.assertEqual(self.patch("borrowings", debt.pk, {"amount": "80000"}).status_code, 200)
        self.assertEqual(self.bal(self.cash), D("1080000.00"))
        self.assertEqual(self.patch("borrowings", debt.pk, {"receiving_account": self.bank.pk}).status_code, 200)
        self.assertEqual((self.bal(self.cash), self.bal(self.bank)), (D("1000000.00"), D("80000.00")))
        self.assertEqual(self.patch("borrowings", debt.pk, {"debt_type": "other"}).status_code, 200)
        self.assertEqual((postings(debt).count(), self.bal(self.bank)), (0, D("0.00")))

    def test_date_before_start_and_overpay_protection(self):
        debt = self.borrow("100000", date=TODAY, account=self.fresh)
        self.assertEqual(self.bal(self.fresh), D("600000.00"))
        self.patch("borrowings", debt.pk, {"date_borrowed": (TODAY - datetime.timedelta(days=60)).isoformat()})
        self.assertEqual(self.bal(self.fresh), D("500000.00"))
        ds.record_borrowing_repayment(user=self.user, borrowing=debt, amount=D("90000"), fee_amount=D("0"),
                                      date=TODAY, paying_account=self.cash)
        self.assertEqual(self.patch("borrowings", debt.pk, {"amount": "50000"}).status_code, 400)

    def test_delete_removes_repayment_postings_too(self):
        debt = self.borrow("100000")
        ds.record_borrowing_repayment(user=self.user, borrowing=debt, amount=D("20000"), fee_amount=D("500"),
                                      date=FEB, paying_account=self.bank)
        self.assertEqual(self.client.delete(f"/api/v1/borrowings/{debt.pk}/").status_code, 204)
        self.assertEqual(AccountPosting.objects.count(), 0)
        self.assertEqual((self.bal(self.cash), self.bal(self.bank)), (D("1000000.00"), D("0.00")))


class RepaymentApiTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.debt = self.borrow("100000")
        self.brep = ds.record_borrowing_repayment(user=self.user, borrowing=self.debt, amount=D("40000"),
                                                  fee_amount=D("1000"), date=FEB, paying_account=self.cash)
        self.loan = self.lend("50000")
        self.lrep = ds.record_lending_repayment(user=self.user, lending=self.loan, principal_amount=D("20000"),
                                                interest_amount=D("1000"), date=FEB, receiving_account=self.bank, notes="")

    def test_borrowing_repayment_edit(self):
        self.assertEqual(self.bal(self.cash), D("1000000") + D("100000") - D("50000") - D("40000") - D("1000"))
        r = self.patch("borrowing-repayments", self.brep.pk, {"amount": "50000", "fee_amount": "0", "paying_account": self.bank.pk})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(postings(self.brep).count(), 1)  # fee posting removed, principal moved
        self.assertEqual(self.bal(self.bank), D("21000") - D("50000"))  # 21,000 lending receipt, 50,000 repaid
        self.assertEqual(self.debt.outstanding(), D("50000.00"))

    def test_borrowing_repayment_overpay_rejected(self):
        r = self.patch("borrowing-repayments", self.brep.pk, {"amount": "100001"})
        self.assertEqual(r.status_code, 400)
        self.brep.refresh_from_db()
        self.assertEqual(self.brep.amount, D("40000.00"))

    def test_lending_repayment_edit_principal_interest_account(self):
        self.assertEqual(self.bal(self.bank), D("21000.00"))
        r = self.patch("lending-repayments", self.lrep.pk, {"principal_amount": "30000", "interest_amount": "0", "receiving_account": self.cash.pk})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.bal(self.bank), D("0.00"))
        self.assertEqual(postings(self.lrep).count(), 1)
        self.assertEqual(self.loan.outstanding(), D("20000.00"))

    def test_lending_repayment_overpay_and_reparenting_rejected(self):
        self.assertEqual(self.patch("lending-repayments", self.lrep.pk, {"principal_amount": "50001"}).status_code, 400)
        other = self.lend("1000", name="Other")
        self.assertEqual(self.patch("lending-repayments", self.lrep.pk, {"lending": other.pk}).status_code, 400)
        self.lrep.refresh_from_db()
        self.assertEqual((self.lrep.principal_amount, self.lrep.lending_id), (D("20000.00"), self.loan.pk))

    def test_repayment_delete_reconciles_postings_and_status(self):
        self.assertEqual(self.client.delete(f"/api/v1/lending-repayments/{self.lrep.pk}/").status_code, 204)
        self.assertEqual(self.bal(self.bank), D("0.00"))
        self.assertEqual(self.client.delete(f"/api/v1/borrowing-repayments/{self.brep.pk}/").status_code, 204)
        self.assertEqual(self.debt.outstanding(), D("100000.00"))
        self.assertEqual(AccountPosting.objects.filter(source_type__in=["borrow_repay", "fee", "lend_repay", "income"]).count(), 0)

    def test_repayment_edit_rollback(self):
        with mock.patch("apps.debts.services.sync_postings", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.patch("lending-repayments", self.lrep.pk, {"principal_amount": "10000"})
        self.lrep.refresh_from_db()
        self.assertEqual((self.lrep.principal_amount, postings(self.lrep).count()), (D("20000.00"), 2))

    def test_create_endpoints_still_work(self):
        r = self.client.post("/api/v1/lending-repayments/", {"lending": self.loan.pk, "principal_amount": "5000",
                             "interest_amount": "0", "date": FEB.isoformat(), "receiving_account": self.bank.pk},
                             content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.loan.outstanding(), D("25000.00"))


class ApiIsolationTests(ApiBase):
    def test_other_user_cannot_edit_delete_or_use_foreign_accounts(self):
        loan, debt = self.lend("40000"), self.borrow("1000")
        lrep = ds.record_lending_repayment(user=self.user, lending=loan, principal_amount=D("1000"), interest_amount=D("0"),
                                           date=FEB, receiving_account=self.bank, notes="")
        brep = ds.record_borrowing_repayment(user=self.user, borrowing=debt, amount=D("100"), fee_amount=D("0"),
                                             date=FEB, paying_account=self.cash)
        intruder = User.objects.create_user(email="x@example.com", password="StrongPass123!")
        theirs = FinancialAccount.objects.create(user=intruder, name="Theirs", account_type="cash", opening_balance=D("5"))
        self.client.force_login(intruder)
        for path, obj in (("lendings", loan), ("borrowings", debt), ("lending-repayments", lrep), ("borrowing-repayments", brep)):
            self.assertEqual(self.patch(path, obj.pk, {"notes": "x"}).status_code, 404)
            self.assertEqual(self.client.delete(f"/api/v1/{path}/{obj.pk}/").status_code, 404)
        self.client.force_login(self.user)
        self.assertEqual(self.patch("lendings", loan.pk, {"source_account": theirs.pk}).status_code, 400)
        self.assertEqual(self.patch("lending-repayments", lrep.pk, {"receiving_account": theirs.pk}).status_code, 400)
        loan.refresh_from_db()
        self.assertEqual(loan.source_account_id, self.cash.pk)
        self.assertEqual(self.bal(self.cash), D("1000000") - D("40000") + D("1000") - D("100"))
        self.assertEqual(BorrowingRepayment.objects.count() + LendingRepayment.objects.count(), 2)
