import datetime
import uuid
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import AccountPosting, FinancialAccount
from apps.accounts.services import account_balance
from apps.debts import payments, people, settlements
from apps.debts import services as ds
from apps.debts.models import Borrowing, Lending
from apps.users.models import User

D = Decimal
TODAY = datetime.date.today()


def make(email="a@example.com"):
    user = User.objects.create_user(email=email, password="StrongPass123!")
    wallet = FinancialAccount.objects.create(user=user, name="Cash", account_type="cash", opening_balance=D("1000000"))
    return user, wallet


def owe(user, wallet, name, amount="1000", contact="", **kw):
    return ds.create_borrowing(
        user=user, debt_type="cash_loan", lender_name=name, contact=contact, amount=D(amount),
        date_borrowed=kw.get("date", TODAY), receiving_account=wallet, due_date=None, interest_amount=D("0"), notes="",
    )


def lend(user, wallet, name, amount="1000", contact="", **kw):
    return ds.create_lending(
        user=user, borrower_name=name, contact=contact, amount=D(amount), date_lent=kw.get("date", TODAY),
        source_account=wallet, due_date=None, interest_amount=D("0"), notes="",
    )


class SuggestTests(TestCase):
    def setUp(self):
        self.user, self.wallet = make()
        owe(self.user, self.wallet, "DAKA")
        lend(self.user, self.wallet, "  daka ")  # same person, different spelling/side
        lend(self.user, self.wallet, "David")
        owe(self.user, self.wallet, "Daniel")
        owe(self.user, self.wallet, "Brian")
        self.client.force_login(self.user)

    def names(self, q):
        resp = self.client.get(reverse("debts:name_suggest"), {"q": q})
        return [r["name"] for r in resp.json()["results"]]

    def test_one_letter_matches_prefix_only_case_insensitive(self):
        self.assertEqual(sorted(self.names("d")), ["DAKA", "Daniel", "David"])
        self.assertEqual(self.names("D"), self.names("d"))
        self.assertNotIn("Brian", self.names("d"))

    def test_narrows_and_ignores_surrounding_spaces(self):
        self.assertEqual(self.names("  DA "), ["DAKA", "Daniel", "David"][:3] if False else self.names("da"))
        self.assertEqual(sorted(self.names("DAK")), ["DAKA"])

    def test_no_duplicates_for_case_and_space_variants(self):
        self.assertEqual(self.names("daka"), ["DAKA"])  # borrowing + lending variants -> one entry

    def test_empty_and_unknown_queries(self):
        self.assertEqual(self.names(""), [])
        self.assertEqual(self.names("zzz"), [])

    def test_includes_settled_and_other_side_history(self):
        settled = lend(self.user, self.wallet, "Zed", "500")
        payments.record_person_payment(
            user=self.user, side="borrower", name="Zed", principal=D("500"), date=TODAY, account=self.wallet,
            token=uuid.uuid4(),
        )
        settled.refresh_from_db()
        self.assertEqual(settled.status, "settled")
        self.assertEqual(self.names("ze"), ["Zed"])

    def test_limit(self):
        for i in range(15):
            owe(self.user, self.wallet, f"Many{i:02d}")
        self.assertEqual(len(self.names("many")), people.SUGGESTION_LIMIT)

    def test_same_name_different_identities_are_labelled(self):
        owe(self.user, self.wallet, "Sam", contact="0700")
        lend(self.user, self.wallet, "Sam", contact="0711")
        results = self.client.get(reverse("debts:name_suggest"), {"q": "sa"}).json()["results"]
        self.assertEqual(sorted(r["label"] for r in results), ["Sam · 0700", "Sam · 0711"])

    def test_no_cross_user_leakage_and_login_required(self):
        other, other_wallet = make("b@example.com")
        owe(other, other_wallet, "Dorothy-Secret")
        self.assertNotIn("Dorothy-Secret", self.names("d"))
        self.client.logout()
        self.assertEqual(self.client.get(reverse("debts:name_suggest"), {"q": "d"}).status_code, 302)


class FormIdentityTests(TestCase):
    def setUp(self):
        self.user, self.wallet = make()
        self.client.force_login(self.user)

    def post_lend(self, name, **extra):
        data = {"borrower_name": name, "amount": "1000", "date_lent": TODAY.isoformat(),
                "source_account": self.wallet.pk, "interest_amount": "0"}
        data.update(extra)
        return self.client.post(reverse("debts:lending_create"), data)

    def post_borrow(self, name, **extra):
        data = {"debt_type": "cash_loan", "lender_name": name, "amount": "1000", "date_borrowed": TODAY.isoformat(),
                "receiving_account": self.wallet.pk, "interest_amount": "0"}
        data.update(extra)
        return self.client.post(reverse("debts:borrowing_create"), data)

    def test_contact_field_not_rendered_anywhere(self):
        for name in ("debts:borrowing_create", "debts:lending_create"):
            html = self.client.get(reverse(name)).content.decode()
            self.assertNotIn('name="contact"', html)
            self.assertIn("data-name-autocomplete", html)
            self.assertIn("person_contact", html)
        rec = lend(self.user, self.wallet, "Ann", contact="0700")
        html = self.client.get(reverse("debts:lending_rename", args=[rec.pk])).content.decode()
        self.assertNotIn("Contact (optional)", html)

    def test_new_name_is_new_person_without_contact(self):
        self.assertEqual(self.post_lend("Brand New").status_code, 302)
        self.assertEqual(Lending.objects.get().contact, "")

    def test_name_known_from_other_side_reuses_identity_and_pay_works(self):
        lend(self.user, self.wallet, "DAKA", "104000")
        self.assertEqual(self.post_borrow("daka", amount="20000").status_code, 302)
        b = Borrowing.objects.get()
        self.assertEqual(b.lender_name, "DAKA")  # canonical display kept
        positions = settlements.debt_positions(self.user)
        pair = positions["lenders"][0]["pairs"][0]
        self.assertEqual(pair["state"], settlements.CONFIRMED)
        before = account_balance(self.wallet)
        settlements.settle_with_person(user=self.user, name="DAKA", amount=D("20000"), date=TODAY, token=uuid.uuid4())
        self.assertEqual(account_balance(self.wallet), before)

    def test_stored_contact_is_inherited_and_grouped(self):
        lend(self.user, self.wallet, "Ruth", "5000", contact="0700")
        self.post_lend("RUTH")
        groups = settlements.debt_positions(self.user)["borrowers"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["total"], D("6000.00"))
        self.assertEqual(Lending.objects.filter(contact="0700").count(), 2)

    def test_ambiguous_same_name_requires_a_choice_never_merges(self):
        lend(self.user, self.wallet, "Sam", contact="0700")
        lend(self.user, self.wallet, "Sam", contact="0711")
        resp = self.post_lend("Sam")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "More than one person is named Sam")
        self.assertEqual(Lending.objects.count(), 2)
        # picking a suggestion (its stored contact) resolves it to that person only
        self.assertEqual(self.post_lend("Sam", person_contact="0711").status_code, 302)
        self.assertEqual(Lending.objects.filter(contact="0711").count(), 2)
        self.assertEqual(Lending.objects.filter(contact="0700").count(), 1)

    def test_forged_contact_for_unknown_identity_is_ignored(self):
        lend(self.user, self.wallet, "Joy", contact="0700")
        other, ow = make("c@example.com")
        owe(other, ow, "Joy", contact="9999")
        self.post_lend("Joy", person_contact="9999")  # belongs to another user: must not be adopted
        self.assertEqual(Lending.objects.filter(user=self.user, contact="9999").count(), 0)

    def test_distinguishing_by_name_makes_a_new_person(self):
        lend(self.user, self.wallet, "Sam", contact="0700")
        lend(self.user, self.wallet, "Sam", contact="0711")
        self.assertEqual(self.post_lend("Sam Kato").status_code, 302)
        self.assertEqual(Lending.objects.get(borrower_name="Sam Kato").contact, "")

    def test_rename_preserves_stored_contact(self):
        rec = lend(self.user, self.wallet, "Ann", contact="0700")
        url = reverse("debts:lending_rename", args=[rec.pk])
        page = self.client.get(url).content.decode()
        self.assertIn('name="contact"', page)  # present only as a hidden carrier
        self.assertIn('type="hidden" name="contact" value="0700"', page.replace('"  ', '" '))
        data = {"name": "Anne", "contact": "0700", "expected_count": 1, "old_name": "Ann", "old_contact": "0700"}
        self.client.post(url, data)
        rec.refresh_from_db()
        self.assertEqual((rec.borrower_name, rec.contact), ("Anne", "0700"))

    def test_ledger_unchanged_by_name_resolution(self):
        before = AccountPosting.objects.count()
        self.post_lend("New Person")
        self.assertEqual(AccountPosting.objects.count(), before + 1)
        self.assertEqual(account_balance(self.wallet), D("999000.00"))

    def test_grouped_payment_after_remembered_name(self):
        lend(self.user, self.wallet, "Kato", "30000")
        self.post_lend("kato", amount="20000")
        records = payments.person_records(self.user, "borrower", "Kato")
        self.assertEqual(len(records), 2)
        payments.record_person_payment(
            user=self.user, side="borrower", name="Kato", principal=D("50000"), date=TODAY, account=self.wallet,
            token=uuid.uuid4(),
        )
        self.assertEqual(payments.person_records(self.user, "borrower", "Kato"), [])
