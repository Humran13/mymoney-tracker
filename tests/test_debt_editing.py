"""Full debt-transaction editing, Add Another Transaction and payment editing."""

import datetime
import uuid
from decimal import Decimal
from unittest import mock

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import AccountPosting, FinancialAccount
from apps.accounts.services import account_balance
from apps.core.models import AuditEvent
from apps.debts import editing, payments, settlements
from apps.debts import services as ds
from apps.debts.models import Borrowing, DebtSettlement, Lending, LendingRepayment
from apps.users.models import User

D = Decimal
TODAY = datetime.date.today()
JAN, FEB, MAR = datetime.date(2025, 1, 10), datetime.date(2025, 2, 15), datetime.date(2025, 3, 20)
LENDER, BORROWER = settlements.LENDER, settlements.BORROWER


def postings_of(obj):
    ct = ContentType.objects.get_for_model(obj)
    return AccountPosting.objects.filter(content_type=ct, object_id=obj.pk)


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="edit@example.com", password="StrongPass123!")
        start = datetime.date(2020, 1, 1)
        self.cash = FinancialAccount.objects.create(
            user=self.user, name="Cash", account_type="cash", opening_balance=D("1000000"), opening_balance_date=start
        )
        self.airtel = FinancialAccount.objects.create(
            user=self.user, name="Airtel", account_type="mobile_money_airtel", opening_balance=D("0"),
            opening_balance_date=start,
        )
        # Explicit start date of today: anything dated earlier is history, not cash.
        self.fresh = FinancialAccount.objects.create(
            user=self.user, name="Fresh", account_type="cash", opening_balance=D("500000"), opening_balance_date=TODAY
        )
        self.client.force_login(self.user)

    def lend(self, name="DAKA", amount="40000", date=JAN, account=None, debt_type="cash_loan", contact="", **kw):
        return ds.create_lending(
            user=self.user, debt_type=debt_type, borrower_name=name, contact=contact, amount=D(amount), date_lent=date,
            source_account=account or self.cash, due_date=kw.get("due_date"), interest_amount=D(kw.get("interest", "0")),
            notes=kw.get("notes", ""),
        )

    def borrow(self, name="Bank", amount="100000", date=JAN, account=None, debt_type="cash_loan", contact="", **kw):
        return ds.create_borrowing(
            user=self.user, debt_type=debt_type, lender_name=name, contact=contact, amount=D(amount), date_borrowed=date,
            receiving_account=account or self.cash, due_date=kw.get("due_date"), interest_amount=D(kw.get("interest", "0")),
            notes=kw.get("notes", ""),
        )

    def repay_lending(self, lending, principal, interest="0", account=None, date=FEB):
        return ds.record_lending_repayment(
            user=self.user, lending=lending, principal_amount=D(principal), interest_amount=D(interest), date=date,
            receiving_account=account or self.airtel, notes="",
        )

    def bal(self, account):
        return account_balance(FinancialAccount.objects.get(pk=account.pk))

    def lend_data(self, rec, **over):
        data = {
            "debt_type": rec.debt_type, "borrower_name": rec.borrower_name, "amount": str(rec.amount),
            "date_lent": rec.date_lent.isoformat(), "source_account": rec.source_account_id or "",
            "due_date": rec.due_date.isoformat() if rec.due_date else "", "interest_amount": str(rec.interest_amount),
            "notes": rec.notes, "version": rec.updated_at.isoformat(),
        }
        data.update(over)
        return data

    def borrow_data(self, rec, **over):
        data = {
            "debt_type": rec.debt_type, "lender_name": rec.lender_name, "amount": str(rec.amount),
            "date_borrowed": rec.date_borrowed.isoformat(), "receiving_account": rec.receiving_account_id or "",
            "due_date": rec.due_date.isoformat() if rec.due_date else "", "interest_amount": str(rec.interest_amount),
            "notes": rec.notes, "version": rec.updated_at.isoformat(),
        }
        data.update(over)
        return data


class LendingTransactionEditTests(Base):
    def edit(self, rec, confirm=True, **over):
        data = self.lend_data(rec, **over)
        if confirm:
            data["confirm"] = "1"
        return self.client.post(reverse("debts:lending_edit", args=[rec.pk]), data)

    def test_form_is_prepopulated_without_a_contact_input(self):
        rec = self.lend("DAKA", "40000", notes="goats", due_date=MAR)
        html = self.client.get(reverse("debts:lending_edit", args=[rec.pk])).content.decode()
        for expected in ("DAKA", "40000", "2025-01-10", "2025-03-20", "goats", "Add Another Transaction"):
            self.assertIn(expected, html)
        self.assertNotIn('name="contact"', html)
        self.assertIn("Save changes", html)

    def test_amount_increase_is_previewed_then_applied_once(self):
        rec = self.lend("DAKA", "40000")
        self.assertEqual(self.bal(self.cash), D("960000.00"))
        preview = self.edit(rec, confirm=False, amount="60000")
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "Account balances will change")
        rec.refresh_from_db()
        self.assertEqual(rec.amount, D("40000.00"))  # nothing written by the preview
        self.assertEqual(self.bal(self.cash), D("960000.00"))
        self.assertEqual(self.edit(rec, amount="60000").status_code, 302)
        rec.refresh_from_db()
        self.assertEqual(rec.amount, D("60000.00"))
        self.assertEqual(Lending.objects.count(), 1)
        self.assertEqual(postings_of(rec).count(), 1)
        self.assertEqual(self.bal(self.cash), D("940000.00"))

    def test_repeating_the_confirmed_post_does_not_double_post(self):
        rec = self.lend("DAKA", "40000")
        data = self.lend_data(rec, amount="60000", confirm="1")
        url = reverse("debts:lending_edit", args=[rec.pk])
        self.client.post(url, data)
        self.client.post(url, {**data, "version": ""})  # a second submit of the same new values
        self.assertEqual(self.bal(self.cash), D("940000.00"))
        self.assertEqual(postings_of(rec).count(), 1)

    def test_changing_account_moves_the_posting_without_duplicating(self):
        rec = self.lend("DAKA", "40000")
        self.edit(rec, source_account=self.airtel.pk)
        rec.refresh_from_db()
        self.assertEqual(rec.source_account_id, self.airtel.pk)
        self.assertEqual(self.bal(self.cash), D("1000000.00"))
        self.assertEqual(self.bal(self.airtel), D("-40000.00"))
        self.assertEqual(postings_of(rec).count(), 1)

    def test_changing_to_noncash_removes_the_cash_movement_and_back(self):
        rec = self.lend("DAKA", "40000")
        self.edit(rec, debt_type="goods_credit", source_account="")
        rec.refresh_from_db()
        self.assertIsNone(rec.source_account_id)
        self.assertEqual(postings_of(rec).count(), 0)
        self.assertEqual(self.bal(self.cash), D("1000000.00"))
        self.edit(rec, debt_type="cash_loan", source_account=self.cash.pk)
        self.assertEqual(self.bal(self.cash), D("960000.00"))
        self.assertEqual(postings_of(rec).count(), 1)

    def test_cash_loan_without_account_is_rejected_and_nothing_changes(self):
        rec = self.lend("DAKA", "40000")
        response = self.edit(rec, source_account="")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "requires the account")
        rec.refresh_from_db()
        self.assertEqual(rec.source_account_id, self.cash.pk)
        self.assertEqual(self.bal(self.cash), D("960000.00"))

    def test_noncash_edit_never_creates_cash_movement(self):
        rec = self.lend("Shop", "25000", debt_type="goods_credit")
        before = AccountPosting.objects.count()
        response = self.edit(rec, confirm=False, amount="30000")
        self.assertContains(response, "No account balance changes")
        self.edit(rec, amount="30000")
        rec.refresh_from_db()
        self.assertEqual(rec.amount, D("30000.00"))
        self.assertEqual(AccountPosting.objects.count(), before)
        self.assertEqual(self.bal(self.cash), D("1000000.00"))

    def test_date_change_respects_account_start_date(self):
        rec = self.lend("DAKA", "40000", date=TODAY, account=self.fresh)
        self.assertEqual(self.bal(self.fresh), D("460000.00"))
        plan = editing.plan_edit(self.user, BORROWER, rec.pk, {"date_lent": TODAY - datetime.timedelta(days=60)})
        # Dated before the account's start: kept as history, no longer inside the balance.
        self.assertTrue(plan["warnings"])
        self.assertEqual(plan["balance_changes"][0]["change"], D("40000.00"))
        self.edit(rec, date_lent=(TODAY - datetime.timedelta(days=60)).isoformat())
        self.assertEqual(self.bal(self.fresh), D("500000.00"))
        self.assertEqual(postings_of(rec).count(), 1)  # the history posting is kept

    def test_interest_due_date_and_notes_edit_without_balance_change(self):
        rec = self.lend("DAKA", "40000")
        response = self.edit(rec, confirm=False, notes="renegotiated", due_date="2025-06-01", interest_amount="5000")
        self.assertEqual(response.status_code, 200)  # interest is a financial field: confirm first
        self.edit(rec, notes="renegotiated", due_date="2025-06-01", interest_amount="5000")
        rec.refresh_from_db()
        self.assertEqual((rec.notes, rec.due_date, rec.interest_amount), ("renegotiated", datetime.date(2025, 6, 1), D("5000.00")))
        self.assertEqual(self.bal(self.cash), D("960000.00"))

    def test_notes_only_edit_saves_without_confirmation(self):
        rec = self.lend("DAKA", "40000")
        self.assertEqual(self.edit(rec, confirm=False, notes="just a note").status_code, 302)
        rec.refresh_from_db()
        self.assertEqual(rec.notes, "just a note")

    def test_no_change_is_reported_not_written(self):
        rec = self.lend("DAKA", "40000")
        before = rec.updated_at
        self.assertEqual(self.edit(rec, confirm=False).status_code, 302)
        rec.refresh_from_db()
        self.assertEqual(rec.updated_at, before)

    def test_amount_cannot_drop_below_what_was_already_received(self):
        rec = self.lend("DAKA", "40000")
        self.repay_lending(rec, "30000")
        response = self.edit(rec, amount="20000")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already been repaid or offset")
        rec.refresh_from_db()
        self.assertEqual(rec.amount, D("40000.00"))
        self.assertEqual(self.bal(self.cash), D("960000.00"))
        self.assertEqual(self.bal(self.airtel), D("30000.00"))

    def test_editing_with_repayments_preserves_them_and_recomputes_outstanding(self):
        rec = self.lend("DAKA", "40000")
        repayment = self.repay_lending(rec, "15000")
        self.edit(rec, amount="70000")
        rec.refresh_from_db()
        self.assertEqual(rec.outstanding(), D("55000.00"))
        self.assertEqual(rec.repayments.get().pk, repayment.pk)
        self.assertEqual(self.bal(self.airtel), D("15000.00"))
        self.assertEqual(self.bal(self.cash), D("930000.00"))

    def test_settled_transaction_edit_up_reactivates_and_down_is_refused(self):
        rec = self.lend("DAKA", "40000")
        self.repay_lending(rec, "40000")
        rec.refresh_from_db()
        self.assertEqual(rec.status, "settled")
        self.assertEqual(self.edit(rec, amount="30000").status_code, 200)  # refused
        rec.refresh_from_db()
        self.assertEqual(rec.amount, D("40000.00"))
        self.edit(rec, amount="50000")
        rec.refresh_from_db()
        self.assertEqual(rec.status, "active")
        self.assertEqual(rec.outstanding(), D("10000.00"))
        self.edit(rec, amount="40000")
        rec.refresh_from_db()
        self.assertEqual(rec.status, "settled")

    def test_date_cannot_move_after_an_existing_payment(self):
        rec = self.lend("DAKA", "40000", date=JAN)
        self.repay_lending(rec, "10000", date=FEB)
        response = self.edit(rec, date_lent=MAR.isoformat())
        self.assertContains(response, "date cannot be moved")
        rec.refresh_from_db()
        self.assertEqual(rec.date_lent, JAN)

    def test_failed_apply_rolls_back_record_and_postings(self):
        rec = self.lend("DAKA", "40000")
        with mock.patch("apps.debts.editing.sync_postings", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                editing.apply_edit(user=self.user, side=BORROWER, pk=rec.pk, changes={"amount": D("90000")})
        rec.refresh_from_db()
        self.assertEqual(rec.amount, D("40000.00"))
        self.assertEqual(self.bal(self.cash), D("960000.00"))
        self.assertEqual(postings_of(rec).count(), 1)

    def test_stale_version_is_refused(self):
        rec = self.lend("DAKA", "40000")
        stale = rec.updated_at.isoformat()
        self.edit(rec, notes="first change")
        response = self.client.post(
            reverse("debts:lending_edit", args=[rec.pk]), self.lend_data(rec, amount="99000", version=stale, confirm="1")
        )
        self.assertContains(response, "changed elsewhere")
        rec.refresh_from_db()
        self.assertEqual(rec.amount, D("40000.00"))

    def test_audit_trail_records_what_changed(self):
        rec = self.lend("DAKA", "40000")
        self.edit(rec, amount="60000", notes="more")
        event = AuditEvent.objects.filter(description="Edited debt transaction").get()
        labels = {c["label"]: (c["before"], c["after"]) for c in event.metadata["changes"]}
        self.assertEqual(labels["Amount"], ("40,000.00", "60,000.00"))
        self.assertEqual(event.metadata["balance_changes"][0]["account"], "Cash")
        self.assertEqual(event.object_id, str(rec.pk))

    def test_archived_account_does_not_block_an_unrelated_edit(self):
        rec = self.lend("DAKA", "40000")
        self.cash.status = FinancialAccount.Status.ARCHIVED
        self.cash.save()
        self.assertEqual(self.edit(rec, notes="still editable").status_code, 302)


class BorrowingTransactionEditTests(Base):
    def edit(self, rec, confirm=True, **over):
        data = self.borrow_data(rec, **over)
        if confirm:
            data["confirm"] = "1"
        return self.client.post(reverse("debts:borrowing_edit", args=[rec.pk]), data)

    def test_amount_account_and_type_edits_adjust_the_ledger(self):
        rec = self.borrow("Bank", "100000")
        self.assertEqual(self.bal(self.cash), D("1100000.00"))
        self.edit(rec, amount="80000")
        self.assertEqual(self.bal(self.cash), D("1080000.00"))
        rec.refresh_from_db()
        self.edit(rec, receiving_account=self.airtel.pk)
        self.assertEqual(self.bal(self.cash), D("1000000.00"))
        self.assertEqual(self.bal(self.airtel), D("80000.00"))
        rec.refresh_from_db()
        self.edit(rec, debt_type="goods_credit", receiving_account="")
        self.assertEqual(self.bal(self.airtel), D("0.00"))
        self.assertEqual(postings_of(rec).count(), 0)
        self.assertEqual(Borrowing.objects.count(), 1)

    def test_interest_edit_changes_total_owed_but_not_cash(self):
        rec = self.borrow("Bank", "100000")
        self.edit(rec, interest_amount="12000")
        rec.refresh_from_db()
        self.assertEqual(rec.outstanding(), D("112000.00"))
        self.assertEqual(self.bal(self.cash), D("1100000.00"))

    def test_total_cannot_drop_below_repaid(self):
        rec = self.borrow("Bank", "100000", interest="10000")
        ds.record_borrowing_repayment(
            user=self.user, borrowing=rec, amount=D("105000"), fee_amount=D("0"), date=FEB, paying_account=self.cash
        )
        response = self.edit(rec, interest_amount="0")
        self.assertContains(response, "already been repaid or offset")
        rec.refresh_from_db()
        self.assertEqual(rec.interest_amount, D("10000.00"))

    def test_date_before_account_start_is_history_not_cash(self):
        rec = self.borrow("Bank", "100000", date=TODAY, account=self.fresh)
        self.assertEqual(self.bal(self.fresh), D("600000.00"))
        self.edit(rec, date_borrowed=(TODAY - datetime.timedelta(days=90)).isoformat())
        self.assertEqual(self.bal(self.fresh), D("500000.00"))


class IdentityEditTests(Base):
    def test_case_change_keeps_identity_and_stored_contact(self):
        rec = self.lend("DAKA", "40000", contact="0700")
        plan = editing.plan_edit(self.user, BORROWER, rec.pk, {"borrower_name": "Daka", "contact": "0700"})
        self.assertFalse(plan["identity_changed"])
        self.client.post(
            reverse("debts:lending_edit", args=[rec.pk]), self.lend_data(rec, borrower_name="  daka ", confirm="1")
        )
        rec.refresh_from_db()
        self.assertEqual((rec.borrower_name, rec.contact), ("daka", "0700"))

    def test_renaming_one_transaction_changes_only_that_transaction(self):
        a, b = self.lend("DAKA", "10000"), self.lend("DAKA", "20000", date=FEB)
        response = self.client.post(
            reverse("debts:lending_edit", args=[b.pk]), self.lend_data(b, borrower_name="Kato", confirm="0")
        )
        self.assertContains(response, "moves only this transaction")  # confirmation required for identity change
        self.client.post(reverse("debts:lending_edit", args=[b.pk]), self.lend_data(b, borrower_name="Kato", confirm="1"))
        a.refresh_from_db(), b.refresh_from_db()
        self.assertEqual((a.borrower_name, b.borrower_name), ("DAKA", "Kato"))

    def test_moving_into_an_existing_person_is_flagged_never_silent(self):
        self.lend("DAKA", "10000", contact="0700")
        other = self.lend("Daniel", "20000", date=FEB)
        plan = editing.plan_edit(self.user, BORROWER, other.pk, {"borrower_name": "DAKA", "contact": "0700"})
        self.assertTrue(plan["identity_changed"] and plan["merges_into_existing"])
        self.assertTrue(plan["needs_confirmation"])

    def test_ambiguous_name_is_not_guessed(self):
        self.lend("Sam", "1000", contact="0700")
        self.lend("Sam", "1000", contact="0711")
        rec = self.lend("Someone", "5000", date=FEB)
        response = self.client.post(
            reverse("debts:lending_edit", args=[rec.pk]), self.lend_data(rec, borrower_name="Sam", confirm="1")
        )
        self.assertContains(response, "More than one person is named Sam")
        rec.refresh_from_db()
        self.assertEqual(rec.borrower_name, "Someone")

    def test_person_in_a_settlement_cannot_be_moved_to_someone_else(self):
        self.lend("DAKA", "104000")
        self.borrow("DAKA", "20000", debt_type="goods_credit", account=None)
        settlements.settle_with_person(
            user=self.user, name="DAKA", amount=D("20000"), date=TODAY, token=uuid.uuid4()
        )
        rec = Lending.objects.get()
        response = self.client.post(
            reverse("debts:lending_edit", args=[rec.pk]), self.lend_data(rec, borrower_name="Other", confirm="1")
        )
        self.assertContains(response, "took part in a noncash offset")
        rec.refresh_from_db()
        self.assertEqual(rec.borrower_name, "DAKA")

    def test_settlement_stays_valid_and_undo_still_works_after_editing(self):
        self.lend("DAKA", "104000")
        self.borrow("DAKA", "20000", debt_type="goods_credit", account=None)
        result = settlements.settle_with_person(
            user=self.user, name="DAKA", amount=D("20000"), date=TODAY, token=uuid.uuid4()
        )
        rec = Lending.objects.get()
        self.assertEqual(rec.outstanding(), D("84000.00"))
        # Cannot cut the loan below what the offset already covered.
        self.assertContains(
            self.client.post(
                reverse("debts:lending_edit", args=[rec.pk]), self.lend_data(rec, amount="10000", confirm="1")
            ),
            "already been repaid or offset",
        )
        self.client.post(reverse("debts:lending_edit", args=[rec.pk]), self.lend_data(rec, amount="110000", confirm="1"))
        rec.refresh_from_db()
        self.assertEqual(rec.outstanding(), D("90000.00"))
        settlements.reverse_settlement(user=self.user, settlement_id=result.pk, date=TODAY)
        rec.refresh_from_db()
        self.assertEqual(rec.outstanding(), D("110000.00"))
        self.assertEqual(DebtSettlement.objects.count(), 1)


class MultipleTransactionsTests(Base):
    def setUp(self):
        super().setUp()
        self.t1 = self.lend("DAKA", "500000", date=JAN, account=self.cash)
        self.t2 = self.lend("DAKA", "200000", date=FEB, account=self.airtel, debt_type="goods_credit")
        self.t3 = self.lend("DAKA", "300000", date=MAR, account=self.cash, interest="10000")

    def person_url(self, name="DAKA", contact=""):
        return reverse("debts:person_edit", args=[BORROWER]) + f"?name={name}&contact={contact}"

    def test_three_separate_transactions_with_independent_figures(self):
        self.assertEqual(Lending.objects.filter(borrower_name="DAKA").count(), 3)
        group = settlements.debt_positions(self.user)["borrowers"][0]
        self.assertEqual(group["total"], D("1000000.00"))
        self.assertEqual(len({self.t1.pk, self.t2.pk, self.t3.pk}), 3)
        self.assertEqual(self.t1.source_account, self.cash)
        self.assertEqual(self.t2.source_account, None)

    def test_edit_button_lists_each_transaction_with_payment_history(self):
        self.repay_lending(self.t1, "100000", account=self.airtel)
        html = self.client.get(self.person_url()).content.decode()
        self.assertEqual(html.count('data-testid="edit-transaction"'), 3)
        self.assertIn("Add Another Transaction", html)
        self.assertIn(reverse("debts:lending_repayment_update", args=[LendingRepayment.objects.get().pk]), html)

    def test_single_transaction_opens_straight_into_the_editor(self):
        solo = self.lend("Solo", "5000")
        response = self.client.get(self.person_url("Solo"))
        self.assertRedirects(response, reverse("debts:lending_edit", args=[solo.pk]))

    def test_unknown_person_is_handled(self):
        self.assertEqual(self.client.get(self.person_url("Nobody")).status_code, 302)

    def test_dashboard_edit_button_targets_the_new_editor(self):
        html = self.client.get(reverse("debts:dashboard")).content.decode()
        self.assertIn(reverse("debts:person_edit", args=[BORROWER]) + "?name=DAKA", html)
        self.assertNotIn("/rename/?name=DAKA", html)

    def test_editing_one_transaction_leaves_the_others_and_their_repayments_alone(self):
        r1 = self.repay_lending(self.t1, "100000")
        self.client.post(
            reverse("debts:lending_edit", args=[self.t2.pk]), self.lend_data(self.t2, amount="250000", confirm="1")
        )
        for rec in (self.t1, self.t2, self.t3):
            rec.refresh_from_db()
        self.assertEqual((self.t1.amount, self.t2.amount, self.t3.amount), (D("500000.00"), D("250000.00"), D("300000.00")))
        self.assertEqual(self.t1.repayments.get().pk, r1.pk)
        self.assertEqual(self.t1.outstanding(), D("400000.00"))

    def test_add_another_transaction_reuses_identity_and_totals_update(self):
        contact_person = self.lend("Ruth", "1000", contact="0700")
        add = reverse("debts:person_add", args=[BORROWER]) + "?name=Ruth&contact=0700"
        html = self.client.get(add).content.decode()
        self.assertIn("readonly", html)
        self.assertNotIn('name="contact"', html)
        response = self.client.post(
            add,
            {
                "debt_type": "services_credit", "borrower_name": "SOMEONE ELSE", "person_contact": "9999",
                "amount": "4000", "date_lent": MAR.isoformat(), "source_account": "", "interest_amount": "0",
                "notes": "second job",
            },
        )
        self.assertEqual(response.status_code, 302)
        new = Lending.objects.exclude(pk=contact_person.pk).get(borrower_name="Ruth")  # tampered name ignored
        self.assertEqual(new.contact, "0700")
        self.assertEqual(new.debt_type, "services_credit")
        self.assertEqual(new.date_lent, MAR)
        group = next(g for g in settlements.debt_positions(self.user)["borrowers"] if g["name"] == "Ruth")
        self.assertEqual(group["total"], D("5000.00"))
        self.assertEqual(len(group["items"]), 2)
        self.assertFalse(Lending.objects.filter(borrower_name="SOMEONE ELSE").exists())

    def test_fourth_transaction_for_daka_with_a_different_account(self):
        add = reverse("debts:person_add", args=[BORROWER]) + "?name=DAKA&contact="
        before_cash, before_airtel = self.bal(self.cash), self.bal(self.airtel)
        self.client.post(
            add,
            {
                "debt_type": "cash_loan", "amount": "50000", "date_lent": TODAY.isoformat(),
                "source_account": self.airtel.pk, "interest_amount": "0", "notes": "",
            },
        )
        self.assertEqual(Lending.objects.filter(borrower_name="DAKA").count(), 4)
        self.assertEqual(self.bal(self.airtel), before_airtel - D("50000"))
        self.assertEqual(self.bal(self.cash), before_cash)
        group = settlements.debt_positions(self.user)["borrowers"][0]
        self.assertEqual(group["total"], D("1050000.00"))

    def test_add_another_for_a_borrowing_person(self):
        self.borrow("Bank", "100000", date=JAN)
        self.borrow("Bank", "50000", date=FEB)
        add = reverse("debts:person_add", args=[LENDER]) + "?name=Bank&contact="
        self.client.post(
            add,
            {
                "debt_type": "cash_loan", "amount": "25000", "date_borrowed": MAR.isoformat(),
                "receiving_account": self.airtel.pk, "interest_amount": "0", "notes": "",
            },
        )
        self.assertEqual(Borrowing.objects.filter(lender_name="Bank").count(), 3)
        self.assertEqual(self.bal(self.airtel), D("25000.00"))
        self.assertEqual(settlements.debt_positions(self.user)["lenders"][0]["total"], D("175000.00"))

    def test_add_another_validates_like_the_create_form(self):
        add = reverse("debts:person_add", args=[BORROWER]) + "?name=DAKA&contact="
        response = self.client.post(
            add,
            {"debt_type": "cash_loan", "amount": "1000", "date_lent": TODAY.isoformat(), "source_account": "", "interest_amount": "0"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Lending.objects.count(), 3)

    def test_add_another_for_unknown_person_is_refused(self):
        self.assertEqual(self.client.get(reverse("debts:person_add", args=[BORROWER]) + "?name=Ghost").status_code, 302)

    def test_edit_screens_have_no_contact_input_and_keep_stored_contact(self):
        rec = self.lend("Ann", "100", contact="0700")
        page = self.client.get(reverse("debts:lending_edit", args=[rec.pk])).content.decode()
        self.assertNotIn('name="contact"', page)
        self.client.post(reverse("debts:lending_edit", args=[rec.pk]), self.lend_data(rec, amount="200", confirm="1"))
        rec.refresh_from_db()
        self.assertEqual(rec.contact, "0700")

    def test_remembered_names_still_suggest_after_edits(self):
        names = [r["name"] for r in self.client.get(reverse("debts:name_suggest"), {"q": "da"}).json()["results"]]
        self.assertEqual(names, ["DAKA"])


class PaymentEditTests(Base):
    def setUp(self):
        super().setUp()
        self.loan = self.lend("DAKA", "100000")
        self.rep = self.repay_lending(self.loan, "40000", interest="2000", account=self.airtel)

    def url(self):
        return reverse("debts:lending_repayment_update", args=[self.rep.pk])

    def data(self, **over):
        data = {
            "principal_amount": "40000", "interest_amount": "2000", "date": FEB.isoformat(),
            "receiving_account": self.airtel.pk, "notes": "",
        }
        data.update(over)
        return data

    def test_form_is_prepopulated(self):
        html = self.client.get(self.url()).content.decode()
        self.assertIn("40000", html)
        self.assertIn("2000", html)
        self.assertIn("2025-02-15", html)

    def test_edit_amount_previews_then_applies_and_recalculates_outstanding(self):
        self.assertEqual(self.bal(self.airtel), D("42000.00"))
        preview = self.client.post(self.url(), self.data(principal_amount="60000"))
        self.assertContains(preview, "Please confirm these changes")
        self.rep.refresh_from_db()
        self.assertEqual(self.rep.principal_amount, D("40000.00"))
        self.client.post(self.url(), self.data(principal_amount="60000", confirm="1"))
        self.rep.refresh_from_db()
        self.assertEqual(self.rep.principal_amount, D("60000.00"))
        self.assertEqual(self.loan.outstanding(), D("40000.00"))
        self.assertEqual(self.bal(self.airtel), D("62000.00"))
        self.assertEqual(postings_of(self.rep).count(), 2)  # principal + interest, never doubled

    def test_changing_account_moves_both_postings(self):
        self.client.post(self.url(), self.data(receiving_account=self.cash.pk, confirm="1"))
        self.assertEqual(self.bal(self.airtel), D("0.00"))
        self.assertEqual(self.bal(self.cash), D("942000.00"))
        self.assertEqual(postings_of(self.rep).count(), 2)

    def test_interest_edit_does_not_touch_principal(self):
        self.client.post(self.url(), self.data(interest_amount="500", confirm="1"))
        self.assertEqual(self.loan.outstanding(), D("60000.00"))
        self.assertEqual(self.bal(self.airtel), D("40500.00"))

    def test_overpayment_is_rejected_and_nothing_changes(self):
        response = self.client.post(self.url(), self.data(principal_amount="100001", confirm="1"))
        self.assertEqual(response.status_code, 200)
        self.rep.refresh_from_db()
        self.assertEqual(self.rep.principal_amount, D("40000.00"))
        self.assertEqual(self.bal(self.airtel), D("42000.00"))
        # exactly the outstanding balance is fine
        self.client.post(self.url(), self.data(principal_amount="100000", confirm="1"))
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, "settled")

    def test_overpayment_is_rejected_even_when_the_form_is_bypassed(self):
        with self.assertRaises(editing.EditError):
            editing.apply_payment_edit(
                user=self.user, side=BORROWER, pk=self.rep.pk, changes={"principal_amount": D("100001")}
            )

    def test_payment_cannot_be_dated_before_its_transaction(self):
        response = self.client.post(self.url(), self.data(date="2024-12-01", confirm="1"))
        self.assertContains(response, "cannot be dated before")

    def test_reducing_a_payment_reopens_a_settled_loan(self):
        self.client.post(self.url(), self.data(principal_amount="100000", confirm="1"))
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, "settled")
        self.client.post(self.url(), self.data(principal_amount="30000", confirm="1"))
        self.loan.refresh_from_db()
        self.assertEqual((self.loan.status, self.loan.outstanding()), ("active", D("70000.00")))

    def test_payment_edit_audit_and_rollback(self):
        self.client.post(self.url(), self.data(principal_amount="50000", confirm="1"))
        self.assertTrue(AuditEvent.objects.filter(description="Edited debt payment").exists())
        with mock.patch("apps.debts.services.sync_postings", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                editing.apply_payment_edit(user=self.user, side=BORROWER, pk=self.rep.pk, changes={"principal_amount": D("10000")})
        self.rep.refresh_from_db()
        self.assertEqual(self.rep.principal_amount, D("50000.00"))
        self.assertEqual(self.bal(self.airtel), D("52000.00"))

    def test_grouped_payment_siblings_are_untouched_by_editing_one_allocation(self):
        second = self.lend("DAKA", "70000", date=MAR)
        results = payments.record_person_payment(
            user=self.user, side=BORROWER, name="DAKA", principal=D("90000"), date=MAR, account=self.cash, token=uuid.uuid4()
        )
        by_record = {r.pk: rep for r, rep in results}
        before = {pk: (rep.principal_amount, postings_of(rep).count()) for pk, rep in by_record.items()}
        first_rep = by_record[self.loan.pk]
        self.client.post(
            reverse("debts:lending_repayment_update", args=[first_rep.pk]),
            {"principal_amount": "10000", "interest_amount": "0", "date": MAR.isoformat(), "receiving_account": self.cash.pk, "notes": "", "confirm": "1"},
        )
        other = by_record[second.pk]
        other.refresh_from_db()
        self.assertEqual(other.principal_amount, before[second.pk][0])
        self.assertEqual(postings_of(other).count(), before[second.pk][1])
        first_rep.refresh_from_db()
        self.assertEqual(first_rep.principal_amount, D("10000.00"))

    def test_notes_only_change_saves_without_confirmation(self):
        self.assertEqual(self.client.post(self.url(), self.data(notes="receipt 12")).status_code, 302)
        self.rep.refresh_from_db()
        self.assertEqual(self.rep.notes, "receipt 12")

    def test_lending_detail_links_to_payment_edit(self):
        html = self.client.get(reverse("debts:lending_detail", args=[self.loan.pk])).content.decode()
        self.assertIn(self.url(), html)


class BorrowingPaymentEditTests(Base):
    def setUp(self):
        super().setUp()
        self.debt = self.borrow("Bank", "200000")
        self.rep = ds.record_borrowing_repayment(
            user=self.user, borrowing=self.debt, amount=D("50000"), fee_amount=D("1500"), date=FEB, paying_account=self.cash
        )

    def data(self, **over):
        data = {"amount": "50000", "fee_amount": "1500", "date": FEB.isoformat(), "paying_account": self.cash.pk, "notes": ""}
        data.update(over)
        return data

    def post(self, **over):
        return self.client.post(reverse("debts:repayment_update", args=[self.rep.pk]), {**self.data(**over), "confirm": "1"})

    def test_fee_edit_never_changes_the_principal(self):
        before = self.debt.outstanding()
        self.post(fee_amount="3000")
        self.assertEqual(self.debt.outstanding(), before)
        self.assertEqual(self.bal(self.cash), D("1000000.00") + D("200000") - D("50000") - D("3000"))
        self.assertEqual(postings_of(self.rep).count(), 2)

    def test_removing_the_fee_removes_exactly_its_posting(self):
        self.post(fee_amount="0")
        self.assertEqual(postings_of(self.rep).count(), 1)

    def test_changing_paying_account(self):
        self.post(paying_account=self.airtel.pk)
        self.assertEqual(self.bal(self.airtel), D("-51500.00"))
        self.assertEqual(self.bal(self.cash), D("1200000.00"))

    def test_service_level_edit_cannot_overpay_even_with_a_stale_form_instance(self):
        response = self.post(amount="200001")
        self.assertEqual(response.status_code, 200)
        self.rep.refresh_from_db()
        self.assertEqual(self.rep.amount, D("50000.00"))

    def test_offsets_count_against_what_a_payment_may_be_raised_to(self):
        self.lend("Bank", "500000", debt_type="goods_credit")
        settlements.settle_with_person(user=self.user, name="Bank", amount=D("100000"), date=MAR, token=uuid.uuid4())
        # 200,000 owed - 100,000 offset leaves room for at most 100,000 in cash repayments
        self.assertEqual(self.post(amount="100001").status_code, 200)
        self.assertEqual(self.post(amount="100000").status_code, 302)
        self.debt.refresh_from_db()
        self.assertEqual((self.debt.status, self.debt.outstanding()), ("settled", D("0.00")))

    def test_detail_page_keeps_edit_link(self):
        html = self.client.get(reverse("debts:borrowing_detail", args=[self.debt.pk])).content.decode()
        self.assertIn(reverse("debts:repayment_update", args=[self.rep.pk]), html)
        self.assertIn(reverse("debts:borrowing_edit", args=[self.debt.pk]), html)


class EditIsolationTests(Base):
    def setUp(self):
        super().setUp()
        self.loan = self.lend("DAKA", "40000")
        self.rep = self.repay_lending(self.loan, "10000")
        self.debt = self.borrow("Bank", "1000")
        self.brep = ds.record_borrowing_repayment(
            user=self.user, borrowing=self.debt, amount=D("100"), fee_amount=D("0"), date=FEB, paying_account=self.cash
        )
        self.other = User.objects.create_user(email="intruder@example.com", password="StrongPass123!")
        self.other_wallet = FinancialAccount.objects.create(
            user=self.other, name="Theirs", account_type="cash", opening_balance=D("5")
        )
        self.client.force_login(self.other)

    def test_other_users_get_404_on_every_new_screen(self):
        urls = [
            reverse("debts:lending_edit", args=[self.loan.pk]),
            reverse("debts:borrowing_edit", args=[self.debt.pk]),
            reverse("debts:lending_repayment_update", args=[self.rep.pk]),
            reverse("debts:repayment_update", args=[self.brep.pk]),
        ]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 404, url)
            self.assertEqual(self.client.post(url, {"amount": "1", "confirm": "1"}).status_code, 404, url)

    def test_person_pages_never_expose_another_users_records(self):
        for name in ("edit", "add"):
            response = self.client.get(reverse(f"debts:person_{name}", args=[BORROWER]) + "?name=DAKA&contact=")
            self.assertEqual(response.status_code, 302)
        self.assertEqual(Lending.objects.get(pk=self.loan.pk).amount, D("40000.00"))

    def test_foreign_account_cannot_be_used_in_an_edit(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("debts:lending_edit", args=[self.loan.pk]),
            self.lend_data(self.loan, source_account=self.other_wallet.pk, confirm="1"),
        )
        self.assertEqual(response.status_code, 200)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.source_account_id, self.cash.pk)
        with self.assertRaises(editing.EditError):
            editing.plan_edit(self.user, BORROWER, self.loan.pk, {"source_account": self.other_wallet})

    def test_service_refuses_other_users_records(self):
        with self.assertRaises(editing.EditError):
            editing.plan_edit(self.other, BORROWER, self.loan.pk, {"amount": D("1")})
        with self.assertRaises(editing.EditError):
            editing.apply_payment_edit(user=self.other, side=BORROWER, pk=self.rep.pk, changes={"principal_amount": D("1")})

    def test_login_required(self):
        self.client.logout()
        for url in (
            reverse("debts:lending_edit", args=[self.loan.pk]),
            reverse("debts:person_edit", args=[BORROWER]),
            reverse("debts:person_add", args=[BORROWER]),
            reverse("debts:lending_repayment_update", args=[self.rep.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 302)
