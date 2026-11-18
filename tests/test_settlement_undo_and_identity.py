"""Undoing debt/loan offsets, and matching a person across Lenders/Borrowers
when contacts are missing, equal, conflicting or shared."""

import datetime
import uuid

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import AccountPosting
from apps.accounts.services import account_balance
from apps.debts import services as debt_services
from apps.debts import settlements
from apps.debts.models import (
    Borrowing,
    DebtSettlement,
    DebtSettlementLine,
    DebtSettlementReversal,
    Lending,
    PersonIdentityLink,
)
from apps.reports.services import outstanding_borrowing_as_of, outstanding_lending_as_of

from .test_debt_offsets_grouping_bill_fees import TODAY, D, lend, make_user, make_wallet, owe, settle


def undo(user, settlement, date=TODAY, notes=""):
    return settlements.reverse_settlement(user=user, settlement_id=settlement.pk, date=date, notes=notes)


class UndoSettlementTests(TestCase):
    def setUp(self):
        self.user = make_user("undo@example.com")
        self.wallet = make_wallet(self.user)
        self.owed = owe(self.user, self.wallet, "John", "500000")
        self.lent = lend(self.user, self.wallet, "John", "300000")
        self.balance = account_balance(self.wallet)
        self.postings = AccountPosting.objects.count()

    def refresh(self):
        self.owed.refresh_from_db()
        self.lent.refresh_from_db()

    def test_undo_restores_both_sides_and_keeps_the_original(self):
        original = settle(self.user, "John", "300000")
        self.refresh()
        self.assertEqual((self.owed.outstanding(), self.lent.status), (D("200000.00"), Lending.Status.SETTLED))
        reversal = undo(self.user, original, notes="entered by mistake")
        self.refresh()
        self.assertEqual(self.owed.outstanding(), D("500000.00"))
        self.assertEqual(self.lent.outstanding(), D("300000.00"))
        self.assertEqual((self.owed.status, self.lent.status), (Borrowing.Status.ACTIVE, Lending.Status.ACTIVE))
        # the settlement, its lines and the reversal all remain as history
        self.assertTrue(DebtSettlement.objects.filter(pk=original.pk).exists())
        self.assertEqual(DebtSettlementLine.objects.filter(settlement=original).count(), 2)
        self.assertEqual((reversal.settlement_id, reversal.notes), (original.pk, "entered by mistake"))

    def test_undo_creates_no_cash_movement(self):
        undo(self.user, settle(self.user, "John", "300000"))
        self.assertEqual(account_balance(self.wallet), self.balance)
        self.assertEqual(AccountPosting.objects.count(), self.postings)

    def test_partial_settlement_can_be_undone(self):
        original = settle(self.user, "John", "100000")
        undo(self.user, original)
        self.refresh()
        self.assertEqual((self.owed.outstanding(), self.lent.outstanding()), (D("500000.00"), D("300000.00")))

    def test_cannot_undo_twice(self):
        original = settle(self.user, "John", "100000")
        undo(self.user, original)
        with self.assertRaises(settlements.AlreadyReversed):
            undo(self.user, original)
        self.assertEqual(DebtSettlementReversal.objects.count(), 1)
        self.refresh()
        self.assertEqual(self.owed.outstanding(), D("500000.00"))

    def test_undo_date_cannot_precede_the_settlement(self):
        original = settle(self.user, "John", "100000", date=TODAY)
        with self.assertRaises(ValueError):
            undo(self.user, original, date=TODAY - datetime.timedelta(days=1))
        self.assertEqual(DebtSettlementReversal.objects.count(), 0)

    def test_offset_can_be_made_again_after_an_undo(self):
        undo(self.user, settle(self.user, "John", "300000"))
        settle(self.user, "John", "300000")
        self.refresh()
        self.assertEqual(self.owed.outstanding(), D("200000.00"))

    def test_undo_blocked_by_a_later_repayment_on_the_same_debt(self):
        original = settle(self.user, "John", "300000")
        debt_services.record_borrowing_repayment(
            user=self.user, borrowing=self.owed, amount=D("200000"), date=TODAY, paying_account=self.wallet, notes=""
        )
        self.assertTrue(settlements.reversal_blockers(original))
        with self.assertRaises(ValueError):
            undo(self.user, original)
        self.assertEqual(DebtSettlementReversal.objects.count(), 0)

    def test_undo_blocked_by_a_later_settlement_until_that_one_is_undone(self):
        first = settle(self.user, "John", "100000")
        second = settle(self.user, "John", "50000")
        with self.assertRaises(ValueError):
            undo(self.user, first)
        undo(self.user, second)
        undo(self.user, first)
        self.refresh()
        self.assertEqual((self.owed.outstanding(), self.lent.outstanding()), (D("500000.00"), D("300000.00")))

    def test_undo_is_scoped_to_the_owner(self):
        original = settle(self.user, "John", "100000")
        other = make_user("stranger@example.com")
        with self.assertRaises(DebtSettlement.DoesNotExist):
            undo(other, original)
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("debts:settlement_undo", args=[original.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("debts:settlement_undo", args=[original.pk]), {"date": TODAY.isoformat()}).status_code, 404)
        self.assertEqual(DebtSettlementReversal.objects.count(), 0)

    def test_offset_debts_stay_undeletable_after_an_undo(self):
        undo(self.user, settle(self.user, "John", "100000"))
        with self.assertRaises(ValueError):
            debt_services.delete_borrowing(self.owed)

    def test_as_of_reports_return_the_amount_from_the_undo_date(self):
        day = TODAY - datetime.timedelta(days=10)
        original = settle(self.user, "John", "300000", date=day)
        undo(self.user, original, date=TODAY - datetime.timedelta(days=3))
        middle = TODAY - datetime.timedelta(days=5)
        self.assertEqual(outstanding_borrowing_as_of(self.owed, middle), D("200000.00"))
        self.assertEqual(outstanding_lending_as_of(self.lent, middle), D("0.00"))
        self.assertEqual(outstanding_borrowing_as_of(self.owed, TODAY), D("500000.00"))
        self.assertEqual(outstanding_lending_as_of(self.lent, TODAY), D("300000.00"))

    def test_cash_repayment_cap_follows_the_restored_balance(self):
        undo(self.user, settle(self.user, "John", "300000"))
        debt_services.record_borrowing_repayment(
            user=self.user, borrowing=self.owed, amount=D("500000"), date=TODAY, paying_account=self.wallet, notes=""
        )
        self.owed.refresh_from_db()
        self.assertEqual(self.owed.status, Borrowing.Status.SETTLED)


class UndoViewTests(TestCase):
    def setUp(self):
        self.user = make_user("undoview@example.com")
        self.wallet = make_wallet(self.user)
        owe(self.user, self.wallet, "John", "500000")
        lend(self.user, self.wallet, "John", "300000")
        self.original = settle(self.user, "John", "300000")
        self.client.force_login(self.user)
        self.url = reverse("debts:settlement_undo", args=[self.original.pk])

    def test_confirmation_page_explains_the_effect_and_changes_nothing(self):
        response = self.client.get(self.url)
        self.assertContains(response, "undo-effects")
        self.assertContains(response, "owed again")
        self.assertEqual(DebtSettlementReversal.objects.count(), 0)

    def test_double_submit_records_one_reversal(self):
        data = {"date": TODAY.isoformat(), "notes": "oops"}
        self.client.post(self.url, data)
        self.client.post(self.url, data)
        self.assertEqual(DebtSettlementReversal.objects.count(), 1)

    def test_history_keeps_both_entries_and_hides_undo_once_reversed(self):
        before = self.client.get(reverse("debts:dashboard"))
        self.assertContains(before, self.url)
        self.client.post(self.url, {"date": TODAY.isoformat()})
        after = self.client.get(reverse("debts:dashboard"))
        self.assertContains(after, "reversed-badge")
        self.assertContains(after, "reversal-entry")
        self.assertNotContains(after, self.url)
        self.assertEqual([g["total"] for g in after.context["lenders"]], [D("500000.00")])
        self.assertEqual([g["total"] for g in after.context["borrowers"]], [D("300000.00")])
        self.assertEqual(after.context["total_owed_by_me"], D("500000.00"))

    def test_blocked_undo_shows_why_and_does_nothing(self):
        borrowing = Borrowing.objects.get()
        debt_services.record_borrowing_repayment(
            user=self.user, borrowing=borrowing, amount=D("100000"), date=TODAY, paying_account=self.wallet, notes=""
        )
        self.assertContains(self.client.get(self.url), "undo-blocked")
        self.client.post(self.url, {"date": TODAY.isoformat()})
        self.assertEqual(DebtSettlementReversal.objects.count(), 0)

    def test_detail_page_marks_the_reversed_offset(self):
        self.client.post(self.url, {"date": TODAY.isoformat()})
        page = self.client.get(reverse("debts:borrowing_detail", args=[Borrowing.objects.get().pk]))
        self.assertContains(page, "owed again")


class IdentityMatchingTests(TestCase):
    def setUp(self):
        self.user = make_user("identity@example.com")
        self.wallet = make_wallet(self.user)
        self.client.force_login(self.user)

    def pair(self, name="John", contact="", other_contact=""):
        found = settlements.find_pair(self.user, name, contact, other_contact)
        return found[2] if found else None

    def group(self, side, name="John", contact=""):
        key = settlements.person_key(name, contact)
        return next(g for g in settlements.debt_positions(self.user)[side] if g["key"] == key)

    def test_equal_contacts_are_recognised_automatically_ignoring_case_and_spaces(self):
        owe(self.user, self.wallet, "john  OKELLO", "1000", contact=" 0772 111 ")
        lend(self.user, self.wallet, "John Okello", "400", contact="0772 111")
        pair = self.pair("John Okello", "0772 111", "0772 111")
        self.assertEqual((pair["state"], pair["via"]), (settlements.CONFIRMED, "contact"))

    def test_missing_contact_on_one_side_needs_confirmation_before_pay(self):
        owe(self.user, self.wallet, "John", "500000")
        lend(self.user, self.wallet, "John", "300000", contact="0772")
        self.assertEqual(self.pair("John", "", "0772")["state"], settlements.NEEDS_CONFIRMATION)
        with self.assertRaises(settlements.IdentityError):
            settle(self.user, "John", "100", other_contact="0772")
        page = self.client.get(reverse("debts:dashboard"))
        self.assertNotContains(page, ">Pay</a>")
        self.assertContains(page, "confirm-identity")
        self.assertEqual(self.client.get(reverse("debts:settle"), {"name": "John", "contact": "", "other_contact": "0772"}).status_code, 302)

    def test_confirming_shows_pay_and_allows_the_offset(self):
        borrowing = owe(self.user, self.wallet, "John", "500000")
        lending = lend(self.user, self.wallet, "John", "300000", contact="0772")
        query = {"name": "John", "contact": "", "other_contact": "0772"}
        self.assertContains(self.client.get(reverse("debts:identity"), query), "Yes, same person")
        self.assertEqual(PersonIdentityLink.objects.count(), 0)
        self.client.post(reverse("debts:identity"), query)
        self.assertEqual(PersonIdentityLink.objects.count(), 1)
        page = self.client.get(reverse("debts:dashboard"))
        self.assertContains(page, ">Pay</a>")
        self.assertNotContains(page, "confirm-identity")
        settle(self.user, "John", "300000", other_contact="0772")
        borrowing.refresh_from_db()
        lending.refresh_from_db()
        self.assertEqual((borrowing.outstanding(), lending.status), (D("200000.00"), Lending.Status.SETTLED))

    def test_confirmation_works_from_either_direction_and_is_idempotent(self):
        owe(self.user, self.wallet, "John", "500", contact="0772")
        lend(self.user, self.wallet, "John", "300")
        for _ in range(2):
            settlements.confirm_identity(user=self.user, name="John", contact="0772", other_contact="")
        self.assertEqual(PersonIdentityLink.objects.count(), 1)
        self.assertEqual(self.pair("John", "0772", "")["state"], settlements.CONFIRMED)

    def test_conflicting_contacts_are_never_combined(self):
        owe(self.user, self.wallet, "John", "500", contact="0772")
        lend(self.user, self.wallet, "John", "300", contact="0701")
        self.assertIsNone(self.pair("John", "0772", "0701"))
        with self.assertRaises(settlements.IdentityError):
            settlements.confirm_identity(user=self.user, name="John", contact="0772", other_contact="0701")
        with self.assertRaises(settlements.IdentityError):
            settle(self.user, "John", "100", contact="0772", other_contact="0701")
        self.client.post(reverse("debts:identity"), {"name": "John", "contact": "0772", "other_contact": "0701"})
        self.assertEqual(PersonIdentityLink.objects.count(), 0)
        self.assertEqual(self.client.get(reverse("debts:settle"), {"name": "John", "contact": "0772", "other_contact": "0701"}).status_code, 302)

    def test_a_stale_link_cannot_override_a_contact_conflict(self):
        owe(self.user, self.wallet, "John", "500", contact="0772")
        lend(self.user, self.wallet, "John", "300", contact="0701")
        PersonIdentityLink.objects.create(user=self.user, name_key="john", contact_a="0701", contact_b="0772")
        self.assertIsNone(self.pair("John", "0772", "0701"))

    def test_several_people_share_the_name_so_each_candidate_needs_explicit_confirmation(self):
        owe(self.user, self.wallet, "John", "500")
        mine = lend(self.user, self.wallet, "John", "300", contact="0772")
        other = lend(self.user, self.wallet, "John", "200", contact="0701")
        page = self.client.get(reverse("debts:dashboard"))
        lender = next(g for g in page.context["lenders"] if g["name"] == "John")
        self.assertEqual(len(lender["pending_pairs"]), 2)
        self.assertEqual(lender["confirmed_pairs"], [])
        self.assertNotContains(page, ">Pay</a>")
        settlements.confirm_identity(user=self.user, name="John", contact="", other_contact="0772")
        lender = self.group("lenders")
        confirmed = [p["other"]["contact"] for p in lender["pairs"] if p["state"] == settlements.CONFIRMED]
        self.assertEqual(confirmed, ["0772"])
        settle(self.user, "John", "300", other_contact="0772")
        mine.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((mine.status, other.outstanding()), (Lending.Status.SETTLED, D("200.00")))
        with self.assertRaises(settlements.IdentityError):
            settle(self.user, "John", "50", other_contact="0701")

    def test_blank_to_blank_is_not_trusted_when_the_name_is_shared(self):
        owe(self.user, self.wallet, "John", "500")
        lend(self.user, self.wallet, "John", "300")
        lend(self.user, self.wallet, "John", "200", contact="0701")
        self.assertEqual(self.pair("John", "", "")["state"], settlements.NEEDS_CONFIRMATION)
        with self.assertRaises(settlements.IdentityError):
            settle(self.user, "John", "100")

    def test_unshared_blank_names_still_match_as_before(self):
        owe(self.user, self.wallet, "Mary", "500")
        lend(self.user, self.wallet, "mary ", "300")
        self.assertEqual(self.pair("Mary", "", "")["state"], settlements.CONFIRMED)
        settle(self.user, "Mary", "300")

    def test_unlink_returns_the_pair_to_needing_confirmation(self):
        owe(self.user, self.wallet, "John", "500")
        lend(self.user, self.wallet, "John", "300", contact="0772")
        settlements.confirm_identity(user=self.user, name="John", contact="", other_contact="0772")
        self.client.post(reverse("debts:identity_unlink"), {"name": "John", "contact": "", "other_contact": "0772"})
        self.assertEqual(PersonIdentityLink.objects.count(), 0)
        self.assertEqual(self.pair("John", "", "0772")["state"], settlements.NEEDS_CONFIRMATION)

    def test_links_are_private_to_each_user(self):
        other = make_user("elsewhere@example.com")
        wallet = make_wallet(other)
        owe(other, wallet, "John", "500")
        lend(other, wallet, "John", "300", contact="0772")
        owe(self.user, self.wallet, "John", "500")
        lend(self.user, self.wallet, "John", "300", contact="0772")
        settlements.confirm_identity(user=other, name="John", contact="", other_contact="0772")
        self.assertEqual(self.pair("John", "", "0772")["state"], settlements.NEEDS_CONFIRMATION)

    def test_existing_records_are_untouched_by_matching_and_confirming(self):
        owe(self.user, self.wallet, "John", "500")
        lend(self.user, self.wallet, "John", "300", contact="0772")
        before = (Borrowing.objects.count(), Lending.objects.count(), AccountPosting.objects.count(), account_balance(self.wallet))
        settlements.confirm_identity(user=self.user, name="John", contact="", other_contact="0772")
        after = (Borrowing.objects.count(), Lending.objects.count(), AccountPosting.objects.count(), account_balance(self.wallet))
        self.assertEqual(before, after)
        self.assertEqual(Borrowing.objects.get().contact, "")

    def test_settlement_token_still_prevents_duplicates_for_a_confirmed_pair(self):
        owe(self.user, self.wallet, "John", "500")
        lend(self.user, self.wallet, "John", "300", contact="0772")
        settlements.confirm_identity(user=self.user, name="John", contact="", other_contact="0772")
        token = uuid.uuid4()
        settle(self.user, "John", "100", other_contact="0772", token=token)
        with self.assertRaises(settlements.DuplicateSettlement):
            settle(self.user, "John", "100", other_contact="0772", token=token)
        self.assertEqual(DebtSettlement.objects.count(), 1)

    def test_full_pay_flow_through_the_views_for_a_confirmed_pair(self):
        owe(self.user, self.wallet, "John", "500000")
        lend(self.user, self.wallet, "John", "300000", contact="0772")
        self.client.post(reverse("debts:identity"), {"name": "John", "contact": "", "other_contact": "0772"})
        data = {
            "name": "John", "contact": "", "other_contact": "0772", "amount": "300000",
            "date": TODAY.isoformat(), "token": str(uuid.uuid4()),
        }
        self.assertContains(self.client.post(reverse("debts:settle"), data), "settle-confirmation")
        self.client.post(reverse("debts:settle"), {**data, "confirm": "1"})
        self.assertEqual(DebtSettlement.objects.count(), 1)
        self.assertEqual(self.client.get(reverse("debts:dashboard")).context["total_owed_to_me"], D("0.00"))
