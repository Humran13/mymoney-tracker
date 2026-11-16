"""Debt/loan offsets (the Pay button), one-entry-per-person grouping, and
recurring-bill transaction fees."""

import datetime
import uuid
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import AccountPosting, FinancialAccount
from apps.accounts.services import account_balance
from apps.bills import services as bill_services
from apps.bills.models import BillPayment, RecurringBill
from apps.debts import services as debt_services
from apps.debts import settlements
from apps.debts.models import Borrowing, BorrowingRepayment, DebtSettlement, DebtSettlementLine, Lending, LendingRepayment
from apps.reports.services import outstanding_borrowing_as_of, outstanding_lending_as_of
from apps.users.models import User

TODAY = datetime.date.today()
D = Decimal


def make_user(email):
    return User.objects.create_user(email=email, password="StrongPass123!")


def make_wallet(user, opening="1000000.00"):
    return FinancialAccount.objects.create(user=user, name="Cash", account_type="cash", opening_balance=D(opening))


def owe(user, wallet, name, amount, *, contact="", notes="", date=TODAY, debt_type="cash_loan"):
    return debt_services.create_borrowing(
        user=user, lender_name=name, contact=contact, amount=D(amount), date_borrowed=date, notes=notes,
        debt_type=debt_type, receiving_account=wallet if debt_type == "cash_loan" else None,
        due_date=None, interest_amount=D("0.00"),
    )


def lend(user, wallet, name, amount, *, contact="", notes="", date=TODAY):
    return debt_services.create_lending(
        user=user, borrower_name=name, contact=contact, amount=D(amount), date_lent=date, notes=notes,
        source_account=wallet, due_date=None, interest_amount=D("0.00"),
    )


def settle(user, name, amount, *, contact="", token=None, date=TODAY):
    return settlements.settle_with_person(
        user=user, name=name, contact=contact, amount=D(amount), date=date, token=token or uuid.uuid4()
    )


class OffsetTests(TestCase):
    def setUp(self):
        self.user = make_user("offset@example.com")
        self.wallet = make_wallet(self.user)
        self.owed = owe(self.user, self.wallet, "John", "500000")
        self.lent = lend(self.user, self.wallet, "John", "300000")
        self.balance_before = account_balance(self.wallet)
        self.postings_before = AccountPosting.objects.count()

    def test_full_offset_of_the_smaller_side(self):
        settle(self.user, "John", "300000")
        self.owed.refresh_from_db()
        self.lent.refresh_from_db()
        self.assertEqual(self.lent.status, Lending.Status.SETTLED)
        self.assertEqual(self.lent.outstanding(), D("0.00"))
        self.assertEqual(self.owed.status, Borrowing.Status.ACTIVE)
        self.assertEqual(self.owed.outstanding(), D("200000.00"))

    def test_offset_never_touches_an_account(self):
        settle(self.user, "John", "300000")
        self.assertEqual(account_balance(self.wallet), self.balance_before)
        self.assertEqual(AccountPosting.objects.count(), self.postings_before)
        self.assertEqual(BorrowingRepayment.objects.count(), 0)
        self.assertEqual(LendingRepayment.objects.count(), 0)

    def test_partial_offset_keeps_both_sides_active_with_correct_balances(self):
        settle(self.user, "John", "100000")
        self.owed.refresh_from_db()
        self.lent.refresh_from_db()
        self.assertEqual(self.owed.outstanding(), D("400000.00"))
        self.assertEqual(self.lent.outstanding(), D("200000.00"))
        self.assertEqual(self.owed.status, Borrowing.Status.ACTIVE)
        self.assertEqual(self.lent.status, Lending.Status.ACTIVE)

    def test_history_records_date_person_amount_and_affected_debts(self):
        when = datetime.date(2026, 3, 4)
        result = settle(self.user, "John", "300000", date=when)
        self.assertEqual((result.person_name, result.amount, result.date), ("John", D("300000.00"), when))
        lines = {(line.borrowing_id, line.lending_id): line.amount for line in result.lines.all()}
        self.assertEqual(lines, {(self.owed.pk, None): D("300000.00"), (None, self.lent.pk): D("300000.00")})

    def test_cannot_offset_more_than_the_smaller_side(self):
        with self.assertRaises(ValueError):
            settle(self.user, "John", "300000.01")
        self.assertEqual(DebtSettlement.objects.count(), 0)

    def test_cannot_offset_zero_or_negative(self):
        for bad in ("0", "-5"):
            with self.assertRaises(ValueError):
                settle(self.user, "John", bad)

    def test_second_offset_is_limited_by_what_is_left(self):
        settle(self.user, "John", "250000")
        with self.assertRaises(ValueError):
            settle(self.user, "John", "100000")
        settle(self.user, "John", "50000")
        self.lent.refresh_from_db()
        self.assertEqual(self.lent.status, Lending.Status.SETTLED)

    def test_same_token_never_posts_twice(self):
        token = uuid.uuid4()
        settle(self.user, "John", "100000", token=token)
        with self.assertRaises(settlements.DuplicateSettlement):
            settle(self.user, "John", "100000", token=token)
        self.assertEqual(DebtSettlement.objects.count(), 1)
        self.owed.refresh_from_db()
        self.assertEqual(self.owed.outstanding(), D("400000.00"))

    def test_offset_spreads_across_records_oldest_first_and_settles_fully_covered_ones(self):
        older = owe(self.user, self.wallet, "Jane", "100000", date=TODAY - datetime.timedelta(days=5))
        newer = owe(self.user, self.wallet, "Jane", "200000")
        loan = lend(self.user, self.wallet, "Jane", "150000")
        settle(self.user, "Jane", "150000")
        for obj in (older, newer, loan):
            obj.refresh_from_db()
        self.assertEqual(older.status, Borrowing.Status.SETTLED)
        self.assertEqual(newer.outstanding(), D("150000.00"))
        self.assertEqual(loan.status, Lending.Status.SETTLED)

    def test_nothing_to_offset_without_both_sides(self):
        owe(self.user, self.wallet, "OnlyLender", "1000")
        with self.assertRaises(ValueError):
            settle(self.user, "OnlyLender", "100")

    def test_matching_ignores_case_and_extra_whitespace(self):
        owe(self.user, self.wallet, "Mary  Ann", "1000")
        lend(self.user, self.wallet, "  mary ann ", "400")
        self.assertEqual(settlements.settlement_summary(self.user, "MARY ANN")["maximum"], D("400.00"))

    def test_same_name_with_different_contacts_are_different_people(self):
        owe(self.user, self.wallet, "Sam", "1000", contact="0772")
        lend(self.user, self.wallet, "Sam", "400", contact="0701")
        self.assertEqual(settlements.settlement_summary(self.user, "Sam", "0772")["maximum"], D("0.00"))
        positions = settlements.debt_positions(self.user)
        self.assertEqual([g["offsettable"] for g in positions["borrowers"] if g["name"] == "Sam"], [D("0.00")])

    def test_other_users_records_are_never_offset(self):
        other = make_user("other@example.com")
        other_wallet = make_wallet(other)
        theirs_owed = owe(other, other_wallet, "John", "999")
        theirs_lent = lend(other, other_wallet, "John", "999")
        settle(self.user, "John", "300000")
        for obj in (theirs_owed, theirs_lent):
            obj.refresh_from_db()
        self.assertEqual(theirs_owed.outstanding(), D("999.00"))
        self.assertEqual(theirs_lent.outstanding(), D("999.00"))
        self.assertEqual(DebtSettlement.objects.filter(user=other).count(), 0)

    def test_cash_repayment_cannot_exceed_balance_left_after_an_offset(self):
        settle(self.user, "John", "300000")
        with self.assertRaises(ValueError):
            debt_services.record_borrowing_repayment(
                user=self.user, borrowing=self.owed, amount=D("250000"), date=TODAY, paying_account=self.wallet, notes=""
            )
        debt_services.record_borrowing_repayment(
            user=self.user, borrowing=self.owed, amount=D("200000"), date=TODAY, paying_account=self.wallet, notes=""
        )
        self.owed.refresh_from_db()
        self.assertEqual(self.owed.status, Borrowing.Status.SETTLED)

    def test_offset_debts_cannot_be_deleted(self):
        settle(self.user, "John", "100000")
        with self.assertRaises(ValueError):
            debt_services.delete_borrowing(self.owed)
        with self.assertRaises(ValueError):
            debt_services.delete_lending(self.lent)
        self.assertEqual(DebtSettlementLine.objects.count(), 2)

    def test_as_of_reports_honour_the_settlement_date(self):
        settle(self.user, "John", "300000", date=TODAY)
        yesterday = TODAY - datetime.timedelta(days=1)
        self.assertEqual(outstanding_borrowing_as_of(self.owed, yesterday), D("500000.00"))
        self.assertEqual(outstanding_borrowing_as_of(self.owed, TODAY), D("200000.00"))
        self.assertEqual(outstanding_lending_as_of(self.lent, TODAY), D("0.00"))


class SettleViewTests(TestCase):
    def setUp(self):
        self.user = make_user("view@example.com")
        self.wallet = make_wallet(self.user)
        self.owed = owe(self.user, self.wallet, "John", "500000")
        self.lent = lend(self.user, self.wallet, "John", "300000")
        lend(self.user, self.wallet, "Alone", "1000")
        self.client.force_login(self.user)

    def test_pay_button_only_for_people_on_both_sides(self):
        html = self.client.get(reverse("debts:dashboard")).content.decode()
        self.assertEqual(html.count(">Pay</a>"), 2)  # John under Lenders and under Borrowers
        self.assertIn("name=John", html)
        self.assertNotIn("name=Alone", html)

    def test_proposal_defaults_to_the_maximum_offset(self):
        response = self.client.get(reverse("debts:settle"), {"name": "John"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial["amount"], D("300000.00"))

    def test_review_step_shows_confirmation_and_changes_nothing(self):
        token = uuid.uuid4()
        response = self.client.post(
            reverse("debts:settle"), {"name": "John", "contact": "", "amount": "300000", "date": TODAY.isoformat(), "token": token}
        )
        self.assertContains(response, "settle-confirmation")
        self.assertContains(response, "200,000")
        self.assertEqual(DebtSettlement.objects.count(), 0)

    def test_confirm_records_once_even_if_resubmitted(self):
        data = {
            "name": "John", "contact": "", "amount": "300000", "date": TODAY.isoformat(),
            "token": str(uuid.uuid4()), "confirm": "1",
        }
        self.client.post(reverse("debts:settle"), data)
        self.client.post(reverse("debts:settle"), data)
        self.assertEqual(DebtSettlement.objects.count(), 1)
        self.owed.refresh_from_db()
        self.assertEqual(self.owed.outstanding(), D("200000.00"))

    def test_over_maximum_is_rejected_on_the_form(self):
        data = {"name": "John", "contact": "", "amount": "300001", "date": TODAY.isoformat(), "token": str(uuid.uuid4()), "confirm": "1"}
        response = self.client.post(reverse("debts:settle"), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(DebtSettlement.objects.count(), 0)

    def test_settled_entry_leaves_active_lists_but_stays_in_history(self):
        data = {"name": "John", "contact": "", "amount": "300000", "date": TODAY.isoformat(), "token": str(uuid.uuid4()), "confirm": "1"}
        self.client.post(reverse("debts:settle"), data)
        response = self.client.get(reverse("debts:dashboard"))
        self.assertEqual([g["name"] for g in response.context["borrowers"]], ["Alone"])
        self.assertEqual([(g["name"], g["total"]) for g in response.context["lenders"]], [("John", D("200000.00"))])
        self.assertEqual(response.context["settlements"][0].amount, D("300000.00"))
        self.assertIn(self.lent, list(response.context["settled_lendings"]))
        self.assertEqual(response.context["total_owed_by_me"], D("200000.00"))
        self.assertEqual(response.context["total_owed_to_me"], D("1000.00"))
        self.assertEqual(Lending.objects.filter(pk=self.lent.pk).count(), 1)

    def test_other_users_person_cannot_be_settled_through_the_view(self):
        other = make_user("intruder@example.com")
        self.client.force_login(other)
        response = self.client.post(
            reverse("debts:settle"),
            {"name": "John", "contact": "", "amount": "100", "date": TODAY.isoformat(), "token": str(uuid.uuid4()), "confirm": "1"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DebtSettlement.objects.count(), 0)

    def test_deleting_an_offset_debt_shows_message_instead_of_crashing(self):
        settle(self.user, "John", "100")
        response = self.client.post(reverse("debts:borrowing_delete", args=[self.owed.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Borrowing.objects.filter(pk=self.owed.pk).exists())

    def test_detail_pages_show_the_offset(self):
        settle(self.user, "John", "100")
        self.assertContains(self.client.get(reverse("debts:borrowing_detail", args=[self.owed.pk])), "Noncash settlement")
        self.assertContains(self.client.get(reverse("debts:lending_detail", args=[self.lent.pk])), "Noncash settlement")


class PersonGroupingTests(TestCase):
    def setUp(self):
        self.user = make_user("group@example.com")
        self.wallet = make_wallet(self.user)
        self.client.force_login(self.user)

    def test_two_loans_to_the_same_person_show_as_one_entry_with_both_transactions(self):
        first = owe(self.user, self.wallet, "John", "100000", notes="transport", date=TODAY - datetime.timedelta(days=3))
        owe(self.user, self.wallet, "John", "50000", notes="school fees")
        debt_services.record_borrowing_repayment(
            user=self.user, borrowing=first, amount=D("20000"), date=TODAY, paying_account=self.wallet, notes=""
        )
        response = self.client.get(reverse("debts:dashboard"))
        groups = response.context["lenders"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["total"], D("130000.00"))
        self.assertEqual([(i.notes, i.total_owed(), i.total_repaid(), i.outstanding_now) for i in groups[0]["items"]], [
            ("transport", D("100000.00"), D("20000.00"), D("80000.00")),
            ("school fees", D("50000.00"), D("0.00"), D("50000.00")),
        ])
        html = response.content.decode()
        self.assertEqual(html.count('data-person="John"'), 1)
        self.assertIn("transport", html)
        self.assertIn("school fees", html)
        self.assertEqual(Borrowing.objects.filter(user=self.user).count(), 2)

    def test_borrowers_are_grouped_the_same_way(self):
        lend(self.user, self.wallet, "Amina", "70000")
        lend(self.user, self.wallet, "Amina", "30000")
        groups = self.client.get(reverse("debts:dashboard")).context["borrowers"]
        self.assertEqual([(g["name"], g["total"], len(g["items"])) for g in groups], [("Amina", D("100000.00"), 2)])

    def test_capitalization_and_whitespace_differences_merge(self):
        owe(self.user, self.wallet, "John Okello", "1000")
        owe(self.user, self.wallet, "  john   OKELLO ", "2000")
        groups = settlements.debt_positions(self.user)["lenders"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["total"], D("3000.00"))

    def test_same_name_with_different_contacts_stays_separate(self):
        owe(self.user, self.wallet, "John", "1000", contact="0772 111")
        owe(self.user, self.wallet, "John", "2000", contact="0701 222")
        self.assertEqual(len(settlements.debt_positions(self.user)["lenders"]), 2)

    def test_settled_records_are_hidden_from_active_groups(self):
        loan = owe(self.user, self.wallet, "Paid Up", "1000")
        debt_services.record_borrowing_repayment(
            user=self.user, borrowing=loan, amount=D("1000"), date=TODAY, paying_account=self.wallet, notes=""
        )
        owe(self.user, self.wallet, "Paid Up", "500")
        group = settlements.debt_positions(self.user)["lenders"][0]
        self.assertEqual((group["total"], len(group["items"])), (D("500.00"), 1))

    def test_groups_are_scoped_per_user(self):
        other = make_user("neighbour@example.com")
        owe(other, make_wallet(other), "John", "5000")
        owe(self.user, self.wallet, "John", "1000")
        self.assertEqual(settlements.debt_positions(self.user)["lenders"][0]["total"], D("1000.00"))

    def test_new_forms_accept_an_optional_contact(self):
        response = self.client.post(
            reverse("debts:lending_create"),
            {
                "borrower_name": "Ruth", "contact": "0700", "amount": "1000", "date_lent": TODAY.isoformat(),
                "source_account": self.wallet.pk, "interest_amount": "0",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Lending.objects.get().contact, "0700")


class BillFeeTests(TestCase):
    def setUp(self):
        self.user = make_user("fees@example.com")
        self.wallet = make_wallet(self.user, "1000000.00")
        self.bill = RecurringBill.objects.create(
            user=self.user, name="Rent", amount=D("100000.00"), transaction_fee=D("1000.00"),
            payment_account=self.wallet, next_due_date=TODAY,
        )

    def test_fee_defaults_to_zero_for_existing_and_new_bills(self):
        plain = RecurringBill.objects.create(user=self.user, name="Gym", amount=D("5000"), next_due_date=TODAY)
        self.assertEqual(plain.transaction_fee, D("0.00"))

    def test_payment_deducts_amount_plus_fee_and_records_them_separately(self):
        payment = bill_services.pay_bill(user=self.user, bill=self.bill, paid_date=TODAY, account=self.wallet)
        self.assertEqual(account_balance(self.wallet), D("899000.00"))
        self.assertEqual((payment.amount, payment.fee_amount, payment.total_paid()), (D("100000.00"), D("1000.00"), D("101000.00")))
        by_type = {p.source_type: p.amount for p in AccountPosting.objects.filter(account=self.wallet)}
        self.assertEqual(by_type[AccountPosting.SourceType.EXPENSE], D("-100000.00"))
        self.assertEqual(by_type[AccountPosting.SourceType.FEE], D("-1000.00"))

    def test_fee_can_be_adjusted_at_payment_time(self):
        payment = bill_services.pay_bill(user=self.user, bill=self.bill, paid_date=TODAY, account=self.wallet, fee_amount=D("2500"))
        self.assertEqual(payment.fee_amount, D("2500.00"))
        self.assertEqual(account_balance(self.wallet), D("897500.00"))

    def test_zero_fee_posts_no_fee_row(self):
        bill_services.pay_bill(user=self.user, bill=self.bill, paid_date=TODAY, account=self.wallet, fee_amount=D("0"))
        self.assertEqual(account_balance(self.wallet), D("900000.00"))
        self.assertFalse(AccountPosting.objects.filter(source_type=AccountPosting.SourceType.FEE).exists())

    def test_negative_fee_is_refused(self):
        with self.assertRaises(ValueError):
            bill_services.pay_bill(user=self.user, bill=self.bill, paid_date=TODAY, account=self.wallet, fee_amount=D("-1"))
        self.assertEqual(account_balance(self.wallet), D("1000000.00"))

    def test_resubmitting_the_same_cycle_never_deducts_twice(self):
        due = self.bill.next_due_date
        bill_services.pay_bill(user=self.user, bill=self.bill, paid_date=TODAY, account=self.wallet, expected_due_date=due)
        with self.assertRaises(ValueError):
            bill_services.pay_bill(user=self.user, bill=self.bill, paid_date=TODAY, account=self.wallet, expected_due_date=due)
        self.assertEqual(BillPayment.objects.count(), 1)
        self.assertEqual(account_balance(self.wallet), D("899000.00"))

    def test_legacy_bill_payment_without_fee_still_works(self):
        legacy = RecurringBill.objects.create(user=self.user, name="Water", amount=D("20000"), payment_account=self.wallet, next_due_date=TODAY)
        payment = bill_services.pay_bill(user=self.user, bill=legacy, paid_date=TODAY)
        self.assertEqual(payment.fee_amount, D("0.00"))
        self.assertEqual(account_balance(self.wallet), D("980000.00"))

    def test_web_flow_create_edit_pay_and_history(self):
        self.client.force_login(self.user)
        self.client.post(
            reverse("bills:create"),
            {"name": "Power", "amount": "50000", "transaction_fee": "", "payment_account": self.wallet.pk, "frequency": "monthly", "next_due_date": TODAY.isoformat()},
        )
        power = RecurringBill.objects.get(name="Power")
        self.assertEqual(power.transaction_fee, D("0.00"))
        self.client.post(
            reverse("bills:update", args=[power.pk]),
            {"name": "Power", "amount": "50000", "transaction_fee": "700", "payment_account": self.wallet.pk, "frequency": "monthly", "next_due_date": TODAY.isoformat()},
        )
        power.refresh_from_db()
        self.assertEqual(power.transaction_fee, D("700.00"))
        pay_page = self.client.get(reverse("bills:pay", args=[power.pk]))
        self.assertEqual(pay_page.context["form"].initial["fee_amount"], D("700.00"))
        data = {
            "paid_date": TODAY.isoformat(), "account": self.wallet.pk, "amount": "50000", "fee_amount": "900",
            "due_date": power.next_due_date.isoformat(),
        }
        self.client.post(reverse("bills:pay", args=[power.pk]), data)
        self.client.post(reverse("bills:pay", args=[power.pk]), data)  # double-submit
        self.assertEqual(BillPayment.objects.filter(bill=power).count(), 1)
        self.assertEqual(account_balance(self.wallet), D("949100.00"))
        listing = self.client.get(reverse("bills:list"))
        self.assertContains(listing, "bill-payment-history")
        self.assertContains(listing, "total deducted")

    def test_negative_fee_rejected_by_forms(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("bills:create"),
            {"name": "X", "amount": "1000", "transaction_fee": "-5", "frequency": "monthly", "next_due_date": TODAY.isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(RecurringBill.objects.filter(name="X").exists())

    def test_api_exposes_fee_fields(self):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(self.user)
        data = client.get("/api/v1/bills/").json()
        rows = data["results"] if isinstance(data, dict) else data
        self.assertEqual(rows[0]["transaction_fee"], "1000.00")
