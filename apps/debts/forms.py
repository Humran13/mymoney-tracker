from decimal import Decimal

from django import forms
from django.db.models import Q

from apps.accounts.models import FinancialAccount

from . import people
from .models import Borrowing, BorrowingRepayment, Lending, LendingRepayment
from .settlements import clean_name, norm


def _selectable_accounts(user, instance, current_attr):
    """Active accounts, plus the account an existing record already uses (so
    editing never fails just because that account was archived since)."""
    current = getattr(instance, current_attr, None) if instance is not None else None
    query = Q(user=user, status=FinancialAccount.Status.ACTIVE)
    if current:
        query |= Q(user=user, pk=current)
    return FinancialAccount.objects.filter(query)


class PersonNameMixin:
    """Remembered-name handling shared by BorrowingForm and LendingForm. The
    Contact input is gone: a hidden `person_contact` (set when a suggestion is
    picked) plus the stored identities decide who the name refers to."""

    person_side = None
    person_field = None

    def _init_person(self, user):
        self.user = user
        self.fields["person_contact"] = forms.CharField(required=False, widget=forms.HiddenInput)
        self.fields[self.person_field].widget.attrs.update(
            {"autocomplete": "off", "data-name-autocomplete": "1", "autocapitalize": "words"}
        )

    def _resolve_person(self, cleaned):
        selected = cleaned.pop("person_contact", "") or ""
        name = cleaned.get(self.person_field)
        if not name:
            cleaned["contact"] = ""
            return
        try:
            cleaned[self.person_field], cleaned["contact"] = people.resolve_person(
                self.user, name, selected, side=self.person_side
            )
        except people.AmbiguousPerson as exc:
            cleaned["contact"] = ""
            self.add_error(self.person_field, str(exc))


class BorrowingForm(PersonNameMixin, forms.ModelForm):
    class Meta:
        model = Borrowing
        fields = ["debt_type", "lender_name", "amount", "date_borrowed", "receiving_account", "due_date", "interest_amount", "notes"]
        widgets = {
            "date_borrowed": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    person_side = "lender"
    person_field = "lender_name"

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_person(user)
        self.fields["receiving_account"].required = False
        self.fields["receiving_account"].queryset = FinancialAccount.objects.filter(
            user=user, status=FinancialAccount.Status.ACTIVE
        )

    def clean_amount(self):
        value = self.cleaned_data["amount"]
        if value <= 0:
            raise forms.ValidationError("Amount borrowed must be greater than zero.")
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("debt_type") == Borrowing.DebtType.CASH_LOAN:
            if not cleaned.get("receiving_account"):
                self.add_error("receiving_account", "A cash loan requires the account that received the money.")
        else:
            cleaned["receiving_account"] = None
        self._resolve_person(cleaned)
        return cleaned


class BorrowingReclassifyForm(forms.Form):
    debt_type = forms.ChoiceField(choices=Borrowing.DebtType.choices)
    receiving_account = forms.ModelChoiceField(queryset=FinancialAccount.objects.none(), required=False)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["receiving_account"].queryset = FinancialAccount.objects.filter(
            user=user, status=FinancialAccount.Status.ACTIVE
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("debt_type") == Borrowing.DebtType.CASH_LOAN and not cleaned.get("receiving_account"):
            self.add_error("receiving_account", "A cash loan requires the account that received the money.")
        return cleaned


class BorrowingRepaymentForm(forms.ModelForm):
    class Meta:
        model = BorrowingRepayment
        fields = ["amount", "fee_amount", "date", "paying_account", "notes"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, user=None, borrowing=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.borrowing = borrowing
        self.fields["paying_account"].queryset = _selectable_accounts(user, self.instance, "paying_account_id")
        # Zero unless the payment channel actually charged something — a fee
        # must never be required, and must never be silently invented.
        self.fields["fee_amount"].required = False
        self.fields["fee_amount"].initial = self.instance.fee_amount if self.instance.pk else 0
        self.fields["fee_amount"].widget = forms.NumberInput(attrs={"step": "0.01", "min": "0"})

    def _available_principal(self):
        """Principal still repayable, adding back this form's own repayment
        when editing so a legitimate increase isn't rejected as overpayment."""
        if self.borrowing is None:
            return None
        available = self.borrowing.outstanding()
        if self.instance.pk:
            available += self.instance.amount
        return available

    def clean_amount(self):
        value = self.cleaned_data["amount"]
        if value <= 0:
            raise forms.ValidationError("Repayment amount must be greater than zero.")
        available = self._available_principal()
        if available is not None and value > available:
            raise forms.ValidationError(f"That's more than the {available} still outstanding on this borrowing.")
        return value

    def clean_fee_amount(self):
        value = self.cleaned_data.get("fee_amount") or Decimal("0.00")
        if value < 0:
            raise forms.ValidationError("Transaction fee cannot be negative.")
        return value


class LendingForm(PersonNameMixin, forms.ModelForm):
    class Meta:
        model = Lending
        fields = ["debt_type", "borrower_name", "amount", "date_lent", "source_account", "due_date", "interest_amount", "notes"]
        widgets = {
            "date_lent": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    person_side = "borrower"
    person_field = "borrower_name"

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_person(user)
        self.fields["source_account"].required = False
        # Omitting the type keeps the historical behaviour: a cash loan.
        self.fields["debt_type"].required = False
        self.fields["source_account"].queryset = FinancialAccount.objects.filter(
            user=user, status=FinancialAccount.Status.ACTIVE
        )

    def clean_debt_type(self):
        return self.cleaned_data.get("debt_type") or Lending.DebtType.CASH_LOAN

    def clean_amount(self):
        value = self.cleaned_data["amount"]
        if value <= 0:
            raise forms.ValidationError("Amount lent must be greater than zero.")
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("debt_type") == Lending.DebtType.CASH_LOAN:
            if not cleaned.get("source_account"):
                self.add_error("source_account", "A cash loan requires the account the money was sent from.")
        else:
            cleaned["source_account"] = None
        self._resolve_person(cleaned)
        return cleaned


class LendingReclassifyForm(forms.Form):
    debt_type = forms.ChoiceField(choices=Lending.DebtType.choices)
    source_account = forms.ModelChoiceField(queryset=FinancialAccount.objects.none(), required=False)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source_account"].queryset = FinancialAccount.objects.filter(
            user=user, status=FinancialAccount.Status.ACTIVE
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("debt_type") == Lending.DebtType.CASH_LOAN and not cleaned.get("source_account"):
            self.add_error("source_account", "A cash loan requires the account the money was sent from.")
        return cleaned


class LendingRepaymentForm(forms.ModelForm):
    class Meta:
        model = LendingRepayment
        fields = ["principal_amount", "interest_amount", "date", "receiving_account", "notes"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, user=None, lending=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.lending = lending
        self.fields["receiving_account"].queryset = _selectable_accounts(user, self.instance, "receiving_account_id")

    def clean(self):
        cleaned = super().clean()
        principal = cleaned.get("principal_amount") or 0
        interest = cleaned.get("interest_amount") or 0
        if principal <= 0 and interest <= 0:
            raise forms.ValidationError("Enter a principal and/or interest amount received.")
        if self.lending is not None:
            available = self.lending.outstanding()
            if self.instance.pk:  # editing: this payment's own principal is repayable again
                available += self.instance.principal_amount
            if principal > available:
                raise forms.ValidationError(f"That's more principal than the {available} still owed on this lending.")
        return cleaned


class EditPersonMixin:
    """Editing an existing transaction. Keeping the same person (ignoring case
    and spacing) keeps their stored identity untouched; typing a different name
    goes through the normal remembered-name resolution."""

    def _remember_original(self):
        self._orig_name = getattr(self.instance, self.person_field)
        self._orig_contact = self.instance.contact

    def _resolve_person(self, cleaned):
        name = cleaned.get(self.person_field)
        if name and norm(name) == norm(self._orig_name):
            cleaned.pop("person_contact", None)
            cleaned[self.person_field] = clean_name(name)
            cleaned["contact"] = self._orig_contact
            return
        super()._resolve_person(cleaned)


class BorrowingEditForm(EditPersonMixin, BorrowingForm):
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self._remember_original()
        self.fields["receiving_account"].queryset = _selectable_accounts(user, self.instance, "receiving_account_id")


class LendingEditForm(EditPersonMixin, LendingForm):
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self._remember_original()
        self.fields["source_account"].queryset = _selectable_accounts(user, self.instance, "source_account_id")


class FixedPersonMixin:
    """Add another transaction for a known person: the name and stored identity
    are fixed by the server (disabled fields ignore anything posted)."""

    def _fix_person(self, person):
        name, contact = person
        self.initial[self.person_field] = name
        self.initial["person_contact"] = contact
        for field in (self.person_field, "person_contact"):
            self.fields[field].disabled = True
        self.fields[self.person_field].widget.attrs.update({"readonly": "readonly"})


class BorrowingAddForm(FixedPersonMixin, BorrowingForm):
    def __init__(self, *args, user=None, person, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self._fix_person(person)


class LendingAddForm(FixedPersonMixin, LendingForm):
    def __init__(self, *args, user=None, person, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self._fix_person(person)


class SettlementForm(forms.Form):
    amount = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"))
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    notes = forms.CharField(max_length=255, required=False)
    token = forms.UUIDField(widget=forms.HiddenInput)


class UndoSettlementForm(forms.Form):
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="Undo date")
    notes = forms.CharField(max_length=255, required=False)


class PersonRenameForm(forms.Form):
    name = forms.CharField(max_length=150, label="Name")
    contact = forms.CharField(max_length=60, required=False, widget=forms.HiddenInput)
    expected_count = forms.IntegerField(widget=forms.HiddenInput, min_value=0)
    old_name = forms.CharField(widget=forms.HiddenInput)
    old_contact = forms.CharField(widget=forms.HiddenInput, required=False)


class PersonPaymentForm(forms.Form):
    """One real payment for all of a person's outstanding transactions.
    `side` decides labels/extra fields: money received (borrower side, with
    optional interest) or money paid out (lender side, with optional fee)."""

    amount = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.00"), label="Payment amount")
    interest_amount = forms.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0.00"), required=False, label="Interest received (optional)"
    )
    fee_amount = forms.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0.00"), required=False, label="Transaction fee (optional)"
    )
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    account = forms.ModelChoiceField(queryset=FinancialAccount.objects.none(), label="Account")
    notes = forms.CharField(max_length=255, required=False)
    token = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, user=None, side="borrower", **kwargs):
        super().__init__(*args, **kwargs)
        self.side = side
        self.fields["account"].queryset = FinancialAccount.objects.filter(
            user=user, status=FinancialAccount.Status.ACTIVE
        )
        if side == "borrower":
            self.fields["account"].label = "Receiving account"
            del self.fields["fee_amount"]
        else:
            self.fields["account"].label = "Paying account"
            self.fields["amount"].min_value = Decimal("0.01")
            del self.fields["interest_amount"]

    def clean(self):
        cleaned = super().clean()
        amount = cleaned.get("amount") or Decimal("0")
        interest = cleaned.get("interest_amount") or Decimal("0")
        if amount <= 0 and interest <= 0 and "amount" not in self.errors:
            raise forms.ValidationError("Enter an amount received.")
        return cleaned
