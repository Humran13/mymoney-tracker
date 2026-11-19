"""PR #4 (lending debt types) combined with grouped payments (#5) and remembered names (#6)."""

import datetime
import uuid
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import AccountPosting, FinancialAccount
from apps.accounts.services import account_balance
from apps.debts import payments, settlements
from apps.debts import services as ds
from apps.debts.models import Borrowing, Lending
from apps.users.models import User

D = Decimal
TODAY = datetime.date.today()


class CombinedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="combo@example.com", password="StrongPass123!")
        self.cash = FinancialAccount.objects.create(user=self.user, name="Cash", account_type="cash", opening_balance=D("1000000"))
        self.airtel = FinancialAccount.objects.create(user=self.user, name="Airtel", account_type="mobile_money_airtel", opening_balance=D("0"))
        self.client.force_login(self.user)

    def _post(self, name, debt_type="cash_loan", amount="40000", **extra):
        data = {"debt_type": debt_type, "borrower_name": name, "amount": amount, "date_lent": TODAY.isoformat(),
                "source_account": self.cash.pk if debt_type == "cash_loan" else "", "interest_amount": "0"}
        data.update(extra)
        return self.client.post(reverse("debts:lending_create"), data)

    def test_form_has_type_names_and_no_contact(self):
        html = self.client.get(reverse("debts:lending_create")).content.decode()
        self.assertIn('name="debt_type"', html)
        self.assertIn("data-name-autocomplete", html)
        self.assertNotIn('name="contact"', html)
        self.assertIn("noncashNotice", html)

    def test_mixed_types_group_one_person_and_pay_once(self):
        self._post("DAKA", "cash_loan", "60000")  # -60,000 cash
        self._post("daka", "goods_credit", "44000")  # no cash movement, name resolved to DAKA
        self.assertEqual(account_balance(self.cash), D("940000.00"))
        self.assertEqual(AccountPosting.objects.filter(source_type="lend_send").count(), 1)
        groups = settlements.debt_positions(self.user)["borrowers"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["total"], D("104000.00"))
        ds.create_borrowing(
            user=self.user, debt_type="goods_credit", lender_name="DAKA", contact="", amount=D("20000"),
            date_borrowed=TODAY, receiving_account=None, due_date=None, interest_amount=D("0"), notes="",
        )
        settlements.settle_with_person(user=self.user, name="DAKA", amount=D("20000"), date=TODAY, token=uuid.uuid4())
        cash_before = account_balance(self.cash)
        payments.record_person_payment(
            user=self.user, side="borrower", name="DAKA", principal=D("84000"), date=TODAY, account=self.airtel,
            token=uuid.uuid4(),
        )
        self.assertEqual(account_balance(self.airtel), D("84000.00"))
        self.assertEqual(account_balance(self.cash), cash_before)
        self.assertEqual(payments.person_records(self.user, "borrower", "DAKA"), [])

    def test_reclassify_then_grouped_payment_and_undo(self):
        self._post("Kato", "cash_loan", "30000")
        self._post("Kato", "other", "20000")
        cash_loan = Lending.objects.get(debt_type="cash_loan")
        resp = self.client.post(
            reverse("debts:lending_reclassify", args=[cash_loan.pk]),
            {"debt_type": "services_credit", "source_account": "", "confirm": "1"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(account_balance(self.cash), D("1000000.00"))  # send reversed
        payments.record_person_payment(
            user=self.user, side="borrower", name="Kato", principal=D("50000"), date=TODAY, account=self.airtel,
            token=uuid.uuid4(),
        )
        self.assertEqual(account_balance(self.airtel), D("50000.00"))
        self.assertEqual(Lending.objects.filter(status="settled").count(), 2)
        self.assertEqual(AccountPosting.objects.filter(account=self.airtel).count(), 2)

    def test_dashboard_cards_link_and_show_type(self):
        self._post("Solo", "services_credit", "1000")
        html = self.client.get(reverse("debts:dashboard")).content.decode()
        self.assertIn(reverse("debts:lending_detail", args=[Lending.objects.get().pk]), html)
        self.assertIn("Services on Credit", html)
        self.assertIn("record-payment", html)

    def test_borrowing_side_unaffected(self):
        b = ds.create_borrowing(
            user=self.user, debt_type="cash_loan", lender_name="Bank", contact="", amount=D("1000"),
            date_borrowed=TODAY, receiving_account=self.cash, due_date=None, interest_amount=D("0"), notes="",
        )
        self.assertEqual(account_balance(self.cash), D("1001000.00"))
        self.assertEqual(Borrowing.objects.get().pk, b.pk)
