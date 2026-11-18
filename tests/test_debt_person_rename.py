"""Editing lender/borrower names: correct a typo, regroup, then offset safely."""

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import AccountPosting
from apps.accounts.services import account_balance
from apps.core.models import AuditEvent
from apps.debts import settlements
from apps.debts.models import Borrowing, DebtSettlement, Lending, PersonIdentityLink

from .test_debt_offsets_grouping_bill_fees import D, lend, make_user, make_wallet, owe, settle
from .test_settlement_undo_and_identity import undo


def rename(user, side, old, new, contact="", new_contact="", **kw):
    return settlements.rename_person(
        user=user, side=side, name=old, contact=contact, new_name=new, new_contact=new_contact, **kw
    )


def names(model, attr):
    return sorted(getattr(r, attr) for r in model.objects.all())


class RenameServiceTests(TestCase):
    def setUp(self):
        self.user = make_user("ren@example.com")
        self.wallet = make_wallet(self.user)

    def snapshot(self):
        return (
            [(b.pk, b.amount, b.status, b.outstanding(), b.date_borrowed) for b in Borrowing.objects.all()],
            [(x.pk, x.amount, x.status, x.outstanding(), x.date_lent) for x in Lending.objects.all()],
            AccountPosting.objects.count(),
            account_balance(self.wallet),
            DebtSettlement.objects.count(),
        )

    def test_renames_every_transaction_of_the_group_without_touching_money(self):
        owe(self.user, self.wallet, "Dakka", "12000")
        owe(self.user, self.wallet, " dakka ", "8000")
        before = self.snapshot()
        result = rename(self.user, "lender", "Dakka", "DAKA")
        self.assertEqual(result["count"], 2)
        self.assertEqual(names(Borrowing, "lender_name"), ["DAKA", "DAKA"])
        self.assertEqual(self.snapshot(), before)

    def test_borrower_side_rename(self):
        lend(self.user, self.wallet, "Mary  Ann", "5000")
        before = self.snapshot()
        rename(self.user, "borrower", "mary ann", "  Mary   Anne ")
        self.assertEqual(names(Lending, "borrower_name"), ["Mary Anne"])
        self.assertEqual(self.snapshot(), before)

    def test_same_name_different_contact_is_not_renamed(self):
        owe(self.user, self.wallet, "John", "1000", contact="0700")
        owe(self.user, self.wallet, "John", "2000", contact="0711")
        rename(self.user, "lender", "John", "Jon", contact="0700", new_contact="0700")
        got = {(b.lender_name, b.contact) for b in Borrowing.objects.all()}
        self.assertEqual(got, {("Jon", "0700"), ("John", "0711")})

    def test_single_transaction_rename_leaves_siblings(self):
        a = owe(self.user, self.wallet, "Dakka", "1000")
        b = owe(self.user, self.wallet, "Dakka", "2000")
        rename(self.user, "lender", "Dakka", "DAKA", pk=b.pk)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual((a.lender_name, b.lender_name), ("Dakka", "DAKA"))

    def test_other_side_is_not_renamed(self):
        owe(self.user, self.wallet, "Dakka", "1000")
        lend(self.user, self.wallet, "Dakka", "2000")
        rename(self.user, "lender", "Dakka", "DAKA")
        self.assertEqual(names(Lending, "borrower_name"), ["Dakka"])

    def test_settled_records_follow_the_person(self):
        old = owe(self.user, self.wallet, "Dakka", "1000")
        owe(self.user, self.wallet, "Dakka", "2000")
        Borrowing.objects.filter(pk=old.pk).update(status=Borrowing.Status.SETTLED)
        result = rename(self.user, "lender", "Dakka", "DAKA")
        self.assertEqual(result["count"], 2)
        self.assertEqual(names(Borrowing, "lender_name"), ["DAKA", "DAKA"])

    def test_empty_and_overlong_names_rejected(self):
        owe(self.user, self.wallet, "Dakka", "1000")
        for bad in ("   ", "x" * 151):
            with self.assertRaises(settlements.RenameError):
                rename(self.user, "lender", "Dakka", bad)
        with self.assertRaises(settlements.RenameError):
            rename(self.user, "lender", "Dakka", "DAKA", new_contact="9" * 61)
        self.assertEqual(names(Borrowing, "lender_name"), ["Dakka"])

    def test_stale_group_is_rejected(self):
        owe(self.user, self.wallet, "Dakka", "1000")
        owe(self.user, self.wallet, "Dakka", "1000")
        with self.assertRaises(settlements.RenameError):
            rename(self.user, "lender", "Dakka", "DAKA", expected_count=1)
        self.assertEqual(names(Borrowing, "lender_name"), ["Dakka", "Dakka"])

    def test_unknown_person_or_side(self):
        with self.assertRaises(settlements.RenameError):
            rename(self.user, "lender", "Nobody", "X")
        with self.assertRaises(settlements.RenameError):
            rename(self.user, "bogus", "A", "B")

    def test_other_users_records_are_never_touched(self):
        other = make_user("other@example.com")
        owe(other, make_wallet(other), "Dakka", "1000")
        owe(self.user, self.wallet, "Dakka", "1000")
        rename(self.user, "lender", "Dakka", "DAKA")
        self.assertEqual(Borrowing.objects.get(user=other).lender_name, "Dakka")
        with self.assertRaises(settlements.RenameError):
            rename(make_user("third@example.com"), "lender", "Dakka", "DAKA")


class RenameThenSettleTests(TestCase):
    """The reported case: lender Dakka 20,000 vs borrower DAKA 104,000."""

    def setUp(self):
        self.user = make_user("dakka@example.com")
        self.wallet = make_wallet(self.user)
        self.owed = owe(self.user, self.wallet, "Dakka", "20000")
        self.lent = lend(self.user, self.wallet, "DAKA", "104000")

    def test_names_match_case_insensitively_so_only_a_typo_blocks_pay(self):
        # "Dakka" vs "DAKA" differ by a letter: not offsettable yet.
        pos = settlements.debt_positions(self.user)
        self.assertEqual(pos["lenders"][0]["pairs"], [])
        self.assertEqual(pos["lenders"][0]["offsettable"], D("0.00"))

    def test_correcting_the_name_enables_pay_but_does_not_settle(self):
        balance = account_balance(self.wallet)
        rename(self.user, "lender", "Dakka", "DAKA")
        self.assertEqual(DebtSettlement.objects.count(), 0)  # nothing automatic
        pos = settlements.debt_positions(self.user)
        pair = pos["lenders"][0]["pairs"][0]
        self.assertEqual((pair["state"], pair["offsettable"]), (settlements.CONFIRMED, D("20000.00")))
        self.owed.refresh_from_db()
        self.assertEqual(self.owed.outstanding(), D("20000.00"))

        settle(self.user, "DAKA", "20000")
        pos = settlements.debt_positions(self.user)
        self.assertEqual(pos["lenders"], [])
        self.assertEqual([(g["name"], g["total"]) for g in pos["borrowers"]], [("DAKA", D("84000.00"))])
        self.owed.refresh_from_db()
        self.assertEqual(self.owed.status, Borrowing.Status.SETTLED)
        self.assertEqual(account_balance(self.wallet), balance)

    def test_partial_offset_after_rename(self):
        rename(self.user, "lender", "Dakka", "DAKA")
        settle(self.user, "DAKA", "5000")
        self.owed.refresh_from_db()
        self.lent.refresh_from_db()
        self.assertEqual((self.owed.outstanding(), self.lent.outstanding()), (D("15000.00"), D("99000.00")))

    def test_undo_after_rename_restores_balances(self):
        rename(self.user, "lender", "Dakka", "DAKA")
        s = settle(self.user, "DAKA", "20000")
        rename(self.user, "lender", "DAKA", "Daka Mukasa")  # renamed again after settling
        rename(self.user, "borrower", "DAKA", "Daka Mukasa")
        undo(self.user, s)
        self.owed.refresh_from_db()
        self.lent.refresh_from_db()
        self.assertEqual((self.owed.outstanding(), self.lent.outstanding()), (D("20000.00"), D("104000.00")))
        self.assertEqual((self.owed.status, self.lent.status), ("active", "active"))
        self.assertEqual(DebtSettlement.objects.get().person_name, "DAKA")  # history untouched
        pos = settlements.debt_positions(self.user)
        self.assertEqual(pos["lenders"][0]["pairs"][0]["offsettable"], D("20000.00"))

    def test_rename_after_partial_offset_keeps_repayment_history(self):
        s = None
        rename(self.user, "lender", "Dakka", "DAKA")
        s = settle(self.user, "DAKA", "5000")
        rename(self.user, "lender", "DAKA", "Dakka B")
        self.assertEqual(s.lines.count(), 2)
        self.owed.refresh_from_db()
        self.assertEqual(self.owed.total_offset(), D("5000.00"))

    def test_contact_mismatch_after_rename_stays_separate(self):
        Borrowing.objects.all().update(contact="0700")
        Lending.objects.all().update(contact="0711")
        rename(self.user, "lender", "Dakka", "DAKA", contact="0700", new_contact="0700")
        pos = settlements.debt_positions(self.user)
        self.assertEqual(pos["lenders"][0]["pairs"], [])
        self.assertEqual(pos["lenders"][0]["offsettable"], D("0.00"))

    def test_missing_contact_after_rename_needs_confirmation(self):
        Lending.objects.all().update(contact="0711")
        rename(self.user, "lender", "Dakka", "DAKA")
        pair = settlements.debt_positions(self.user)["lenders"][0]["pairs"][0]
        self.assertEqual(pair["state"], settlements.NEEDS_CONFIRMATION)
        with self.assertRaises(settlements.IdentityError):
            settle(self.user, "DAKA", "1000", other_contact="0711")

    def test_duplicate_names_with_different_contacts_need_explicit_choice(self):
        owe(self.user, self.wallet, "Daka", "7000", contact="0700")
        owe(self.user, self.wallet, "Daka", "9000", contact="0722")
        rename(self.user, "lender", "Dakka", "Daka")  # third "Daka", no contact
        lend(self.user, self.wallet, "Daka", "3000", contact="0700")
        pos = settlements.debt_positions(self.user)
        states = {
            (g["contact"], p["other"]["contact"], p["state"])
            for g in pos["lenders"]
            for p in g["pairs"]
        }
        self.assertIn(("0700", "0700", settlements.CONFIRMED), states)
        self.assertNotIn(("0722", "0700", settlements.CONFIRMED), states)
        # 0722 may only be *offered* a match with the contact-less entry, never auto-confirmed
        self.assertEqual({s[2] for s in states if s[0] == "0722"}, {settlements.NEEDS_CONFIRMATION})


class IdentityLinkHandlingTests(TestCase):
    def setUp(self):
        self.user = make_user("links@example.com")
        self.wallet = make_wallet(self.user)
        owe(self.user, self.wallet, "John", "1000")
        lend(self.user, self.wallet, "John", "2000", contact="0700")
        settlements.confirm_identity(user=self.user, name="John", contact="", other_contact="0700")
        self.assertEqual(PersonIdentityLink.objects.count(), 1)

    def test_link_dropped_when_the_identity_it_confirmed_is_renamed_away(self):
        result = rename(self.user, "lender", "John", "Johnny")
        self.assertEqual(result["links_removed"], 1)
        self.assertEqual(PersonIdentityLink.objects.count(), 0)

    def test_renaming_back_does_not_resurrect_a_confirmation(self):
        rename(self.user, "lender", "John", "Johnny")
        rename(self.user, "lender", "Johnny", "John")
        pair = settlements.debt_positions(self.user)["lenders"][0]["pairs"][0]
        self.assertEqual(pair["state"], settlements.NEEDS_CONFIRMATION)

    def test_link_kept_while_the_identity_is_still_used(self):
        extra = owe(self.user, self.wallet, "John", "500")
        result = rename(self.user, "lender", "John", "Johnny", pk=extra.pk)
        self.assertEqual(result["links_removed"], 0)
        self.assertEqual(PersonIdentityLink.objects.count(), 1)

    def test_rename_never_creates_a_link(self):
        PersonIdentityLink.objects.all().delete()
        rename(self.user, "lender", "John", "John", new_contact="")  # no-op
        self.assertEqual(PersonIdentityLink.objects.count(), 0)


class RenameViewTests(TestCase):
    def setUp(self):
        self.user = make_user("view@example.com")
        self.wallet = make_wallet(self.user)
        self.b1 = owe(self.user, self.wallet, "Dakka", "12000")
        self.b2 = owe(self.user, self.wallet, "Dakka", "8000")
        self.lent = lend(self.user, self.wallet, "DAKA", "104000")
        self.client.force_login(self.user)

    def url(self, side="lender", name="Dakka", contact=""):
        return reverse("debts:person_rename", args=[side]) + f"?name={name}&contact={contact}"

    def post(self, **extra):
        data = {
            "old_name": "Dakka", "old_contact": "", "expected_count": 2, "name": "DAKA", "contact": "", **extra
        }
        return self.client.post(reverse("debts:person_rename", args=["lender"]), data)

    def test_dashboard_shows_edit_buttons_for_both_lists(self):
        html = self.client.get(reverse("debts:dashboard")).content.decode()
        self.assertEqual(html.count('data-testid="edit-person"'), 2)

    def test_form_explains_how_many_transactions(self):
        html = self.client.get(self.url()).content.decode()
        self.assertIn("2 transactions", html)
        self.assertIn('value="Dakka"', html)

    def test_post_renames_group_and_shows_pay(self):
        response = self.post()
        self.assertRedirects(response, reverse("debts:dashboard"))
        self.assertEqual(names(Borrowing, "lender_name"), ["DAKA", "DAKA"])
        html = self.client.get(reverse("debts:dashboard")).content.decode()
        self.assertIn(">Pay</a>", html)
        self.assertEqual(DebtSettlement.objects.count(), 0)

    def test_audit_event_recorded(self):
        self.post()
        event = AuditEvent.objects.filter(description__startswith="Renamed").get()
        self.assertEqual(event.actor, self.user)
        self.assertEqual(event.metadata["old_name"], "Dakka")
        self.assertEqual(event.metadata["new_name"], "DAKA")
        self.assertEqual(len(event.metadata["records"]), 2)

    def test_no_change_writes_no_audit_event(self):
        self.post(name="Dakka")
        self.assertFalse(AuditEvent.objects.filter(description__startswith="Renamed").exists())

    def test_invalid_name_redisplays_form(self):
        response = self.post(name="  ")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(names(Borrowing, "lender_name"), ["Dakka", "Dakka"])

    def test_stale_count_shows_error_and_changes_nothing(self):
        owe(self.user, self.wallet, "Dakka", "1")
        response = self.post()
        self.assertContains(response, "now has 3 transactions")
        self.assertEqual(names(Borrowing, "lender_name"), ["Dakka"] * 3)

    def test_merge_note_when_name_already_exists(self):
        owe(self.user, self.wallet, "DAKA", "10")
        response = self.client.post(
            reverse("debts:person_rename", args=["lender"]),
            {"old_name": "Dakka", "old_contact": "", "expected_count": 2, "name": "daka", "contact": ""},
        )
        self.assertEqual(response.status_code, 302)  # allowed; same person by definition
        self.assertEqual(len(settlements.debt_positions(self.user)["lenders"]), 1)

    def test_single_transaction_view(self):
        response = self.client.post(
            reverse("debts:borrowing_rename", args=[self.b2.pk]),
            {"old_name": "Dakka", "old_contact": "", "expected_count": 1, "name": "DAKA", "contact": ""},
        )
        self.assertEqual(response.status_code, 302)
        self.b1.refresh_from_db()
        self.b2.refresh_from_db()
        self.assertEqual((self.b1.lender_name, self.b2.lender_name), ("Dakka", "DAKA"))
        self.assertContains(self.client.get(reverse("debts:borrowing_rename", args=[self.b2.pk])), "Only this one transaction")
        self.assertContains(self.client.get(reverse("debts:lending_rename", args=[self.lent.pk])), "Only this one transaction")

    def test_detail_pages_link_to_rename(self):
        self.assertContains(self.client.get(reverse("debts:borrowing_detail", args=[self.b1.pk])), "Edit name")
        self.assertContains(self.client.get(reverse("debts:lending_detail", args=[self.lent.pk])), "Edit name")

    def test_bad_side_404_and_login_required(self):
        self.assertEqual(self.client.get(reverse("debts:person_rename", args=["x"])).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url()).status_code, 302)

    def test_cannot_rename_another_users_records(self):
        other = make_user("mallory@example.com")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("debts:borrowing_rename", args=[self.b1.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("debts:lending_rename", args=[self.lent.pk]), {}).status_code, 404)
        response = self.post()
        self.assertEqual(response.status_code, 302)  # redirected: nothing found for this user
        self.assertEqual(names(Borrowing, "lender_name"), ["Dakka", "Dakka"])
        self.assertEqual(AuditEvent.objects.filter(description__startswith="Renamed").count(), 0)
