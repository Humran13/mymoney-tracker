import datetime
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from apps.accounts.models import AccountPosting, FinancialAccount
from apps.accounts.services import account_balance
from apps.debts import services as ds
from apps.debts.models import Borrowing, Lending
from apps.users.models import User

T = Lending.DebtType
TODAY = datetime.date.today()


def make_user(email):
    return User.objects.create_user(email=email, password="StrongPass123!")


def make_acct(user, name="Cash", opening="1000000.00"):
    return FinancialAccount.objects.create(user=user, name=name, account_type="cash", opening_balance=Decimal(opening))


def repay(user, lending, amount, acct):
    return ds.record_lending_repayment(
        user=user, lending=lending, principal_amount=Decimal(amount),
        interest_amount=Decimal("0.00"), date=TODAY, receiving_account=acct, notes="",
    )


class LendingDebtTypeTests(TestCase):
    def setUp(self):
        self.user = make_user("l1@example.com")
        self.acct = make_acct(self.user)
        self.other = make_acct(self.user, "MTN", "0.00")

    def _lend(self, debt_type, account=None, amount="100000.00", name="Sam"):
        return ds.create_lending(
            user=self.user, debt_type=debt_type, borrower_name=name, amount=Decimal(amount),
            date_lent=TODAY, source_account=account, due_date=None, interest_amount=Decimal("0.00"), notes="",
        )

    def test_cash_loan_deducts(self):
        lending = self._lend(T.CASH_LOAN, self.acct)
        self.assertEqual(account_balance(self.acct), Decimal("900000.00"))
        self.assertEqual(lending.outstanding(), Decimal("100000.00"))

    def test_cash_loan_requires_account(self):
        with self.assertRaises(ValueError):
            self._lend(T.CASH_LOAN, None)
        self.assertEqual(Lending.objects.count(), 0)

    def test_noncash_types_never_touch_accounts(self):
        for dt in (T.GOODS_CREDIT, T.SERVICES_CREDIT, T.OTHER):
            lending = self._lend(dt, self.acct)  # a stray account is discarded
            self.assertIsNone(lending.source_account)
            self.assertEqual(lending.outstanding(), Decimal("100000.00"))
        self.assertEqual(account_balance(self.acct), Decimal("1000000.00"))
        self.assertFalse(AccountPosting.objects.filter(user=self.user).exists())

    def test_noncash_repayment_goes_into_account(self):
        lending = self._lend(T.GOODS_CREDIT)
        repay(self.user, lending, "40000.00", self.acct)
        self.assertEqual(account_balance(self.acct), Decimal("1040000.00"))
        self.assertEqual(lending.outstanding(), Decimal("60000.00"))
        repay(self.user, lending, "60000.00", self.acct)
        lending.refresh_from_db()
        self.assertEqual(lending.status, Lending.Status.SETTLED)

    def test_reclassify_cash_to_noncash_and_back(self):
        lending = self._lend(T.CASH_LOAN, self.acct)
        repay(self.user, lending, "30000.00", self.acct)
        self.assertEqual(ds.preview_reclassify_lending(lending, T.GOODS_CREDIT, None), {self.acct: Decimal("100000.00")})
        ds.reclassify_lending(lending=lending, debt_type=T.GOODS_CREDIT, source_account=None)
        self.assertEqual(account_balance(self.acct), Decimal("1030000.00"))  # send reversed, repayment kept
        self.assertEqual(lending.repayments.count(), 1)
        self.assertEqual(lending.outstanding(), Decimal("70000.00"))
        self.assertEqual(ds.preview_reclassify_lending(lending, T.CASH_LOAN, self.other), {self.other: Decimal("-100000.00")})
        ds.reclassify_lending(lending=lending, debt_type=T.CASH_LOAN, source_account=self.other)
        ds.reclassify_lending(lending=lending, debt_type=T.CASH_LOAN, source_account=self.other)  # idempotent
        self.assertEqual(account_balance(self.other), Decimal("-100000.00"))
        self.assertEqual(AccountPosting.objects.filter(source_type=AccountPosting.SourceType.LEND_SEND).count(), 1)

    def test_delete_noncash_lending(self):
        lending = self._lend(T.OTHER)
        ds.delete_lending(lending)
        self.assertEqual(Lending.objects.count(), 0)
        self.assertEqual(account_balance(self.acct), Decimal("1000000.00"))

    def test_existing_records_default_to_cash_loan(self):
        lending = Lending.objects.create(
            user=self.user, borrower_name="Old", amount=Decimal("5.00"), date_lent=TODAY, source_account=self.acct
        )
        self.assertEqual(lending.debt_type, "cash_loan")


class LendingDebtTypeWebTests(TestCase):
    def setUp(self):
        self.user = make_user("l2@example.com")
        self.acct = make_acct(self.user)
        self.stranger = make_user("l3@example.com")
        self.s_acct = make_acct(self.stranger, "Theirs")
        self.client.force_login(self.user)

    def _post(self, **extra):
        data = {"debt_type": "cash_loan", "borrower_name": "Ann", "contact": "", "amount": "50000",
                "date_lent": TODAY.isoformat(), "source_account": self.acct.pk, "due_date": "",
                "interest_amount": "0", "notes": ""}
        data.update(extra)
        return self.client.post(reverse("debts:lending_create"), data)

    def test_form_first_field_and_notice(self):
        html = self.client.get(reverse("debts:lending_create")).content.decode()
        self.assertLess(html.index('name="debt_type"'), html.index('name="borrower_name"'))
        self.assertIn("Goods on Credit", html)
        self.assertIn("noncashNotice", html)

    def test_create_cash_and_noncash(self):
        self.assertEqual(self._post().status_code, 302)
        self.assertEqual(account_balance(self.acct), Decimal("950000.00"))
        self.assertEqual(self._post(debt_type="goods_credit", source_account="").status_code, 302)
        self.assertEqual(account_balance(self.acct), Decimal("950000.00"))
        self.assertEqual(Lending.objects.filter(debt_type="goods_credit", source_account=None).count(), 1)

    def test_cash_without_account_rejected(self):
        self.assertEqual(self._post(source_account="").status_code, 200)
        self.assertEqual(Lending.objects.count(), 0)

    def test_other_users_account_rejected(self):
        self.assertEqual(self._post(source_account=self.s_acct.pk).status_code, 200)
        self.assertEqual(Lending.objects.count(), 0)

    def test_detail_shows_type_and_reclassify_flow(self):
        self._post()
        lending = Lending.objects.get()
        self.assertIn("Cash Loan", self.client.get(reverse("debts:lending_detail", args=[lending.pk])).content.decode())
        url = reverse("debts:lending_reclassify", args=[lending.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        resp = self.client.post(url, {"debt_type": "other", "source_account": ""})  # preview only
        self.assertContains(resp, "Preview of balance changes")
        lending.refresh_from_db()
        self.assertEqual(lending.debt_type, "cash_loan")
        self.assertEqual(account_balance(self.acct), Decimal("950000.00"))
        self.client.post(url, {"debt_type": "other", "source_account": "", "confirm": "1"})
        lending.refresh_from_db()
        self.assertEqual(lending.debt_type, "other")
        self.assertEqual(account_balance(self.acct), Decimal("1000000.00"))
        self.assertIn("Other Money Owed", self.client.get(reverse("debts:dashboard")).content.decode())

    def test_reclassify_rejects_foreign_account(self):
        self._post()
        lending = Lending.objects.get()
        url = reverse("debts:lending_reclassify", args=[lending.pk])
        self.client.post(url, {"debt_type": "cash_loan", "source_account": self.s_acct.pk, "confirm": "1"})
        lending.refresh_from_db()
        self.assertEqual(lending.source_account_id, self.acct.pk)

    def test_reclassify_isolated(self):
        theirs = ds.create_lending(
            user=self.stranger, debt_type="cash_loan", borrower_name="X", amount=Decimal("10.00"), date_lent=TODAY,
            source_account=self.s_acct, due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        url = reverse("debts:lending_reclassify", args=[theirs.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {"debt_type": "other", "confirm": "1"}).status_code, 404)

    def test_offset_and_undo_with_noncash_lending(self):
        import uuid
        from apps.debts import settlements
        lending = ds.create_lending(
            user=self.user, debt_type="services_credit", borrower_name="Pat", amount=Decimal("100000.00"),
            date_lent=TODAY, source_account=None, due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        Borrowing.objects.create(user=self.user, debt_type="goods_credit", lender_name="Pat",
                                 amount=Decimal("50000.00"), date_borrowed=TODAY)
        before, postings = account_balance(self.acct), AccountPosting.objects.count()
        st = settlements.settle_with_person(user=self.user, name="Pat", contact="", other_contact=None,
                                            amount=Decimal("50000.00"), date=TODAY, token=uuid.uuid4())
        self.assertEqual(lending.outstanding(), Decimal("50000.00"))
        settlements.reverse_settlement(user=self.user, settlement_id=st.pk, date=TODAY)
        self.assertEqual(lending.outstanding(), Decimal("100000.00"))
        self.assertEqual(account_balance(self.acct), before)
        self.assertEqual(AccountPosting.objects.count(), postings)

    def test_api_noncash_and_validation(self):
        from rest_framework.test import APIClient
        c = APIClient()
        c.force_authenticate(self.user)
        base = {"borrower_name": "Z", "amount": "1000.00", "date_lent": TODAY.isoformat()}
        self.assertEqual(c.post("/api/v1/lendings/", {**base, "source_account": self.acct.pk}, format="json").status_code, 201)
        self.assertEqual(c.post("/api/v1/lendings/", base, format="json").status_code, 400)
        r = c.post("/api/v1/lendings/", {**base, "debt_type": "services_credit"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(account_balance(self.acct), Decimal("999000.00"))


class LendingDebtTypeMigrationTests(TransactionTestCase):
    """Migration 0007 must default every existing loan to Cash Loan without
    touching postings or balances."""

    def migrate(self, targets):
        MigrationExecutor(connection).migrate(targets)

    def test_existing_lendings_become_cash_loans_unchanged(self):
        user = make_user("mig@example.com")
        acct = make_acct(user)
        loan = ds.create_lending(
            user=user, debt_type="cash_loan", borrower_name="Old", amount=Decimal("250000.00"), date_lent=TODAY,
            source_account=acct, due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        repay(user, loan, "50000.00", acct)
        postings = sorted(AccountPosting.objects.values_list("pk", "amount", "source_type"))
        balance = account_balance(acct)

        self.migrate([("debts", "0006_settlement_reversals_and_identity_links")])
        self.migrate([("debts", "0007_lending_debt_type")])

        loan = Lending.objects.get(pk=loan.pk)
        self.assertEqual(loan.debt_type, "cash_loan")
        self.assertEqual(loan.source_account_id, acct.pk)
        self.assertEqual(loan.outstanding(), Decimal("200000.00"))
        self.assertEqual(sorted(AccountPosting.objects.values_list("pk", "amount", "source_type")), postings)
        self.assertEqual(account_balance(acct), balance)
