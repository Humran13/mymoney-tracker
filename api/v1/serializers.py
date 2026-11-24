from django.db.models import Q
from rest_framework import serializers

from apps.accounts.models import AccountPosting, FinancialAccount, Transfer
from apps.bills.models import BillPayment, RecurringBill
from apps.budgets.models import Budget
from apps.closing.models import DailyClosing
from apps.debts.models import Borrowing, BorrowingRepayment, Lending, LendingRepayment
from apps.goals.models import GoalAllocation, SavingsGoal
from apps.transactions.models import Category, Transaction, TransactionTemplate
from apps.users.models import User, UserProfile, UserSettings
from apps.accounting.models import HeldFund, HeldFundEntry, InventoryLot, InventorySale, InventorySalePayment, ReimbursementClaim, ReimbursementPayment


class ScopedFieldsMixin:
    """DRF's related fields validate against the model's *entire* table by
    default — without this, a user could reference another user's account,
    borrowing, goal etc. by guessing its id. Subclasses declare
    `scoped_fields = {"field_name": queryset_fn}` and this restricts each
    field's allowed choices to that user's own rows (or, for categories,
    their own rows plus the shared global defaults)."""

    scoped_fields: dict = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            for field_name, scope_fn in self.scoped_fields.items():
                if field_name in self.fields:
                    self.fields[field_name].queryset = scope_fn(user)


def _user_accounts(user):
    return FinancialAccount.objects.filter(user=user)


def _user_or_global_categories(user):
    return Category.objects.filter(Q(user=user) | Q(user__isnull=True))


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name", "date_joined"]
        read_only_fields = ["id", "date_joined"]


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = ["display_name", "currency_code", "timezone", "theme", "first_time_setup_complete"]


class UserSettingsSerializer(ScopedFieldsMixin, serializers.ModelSerializer):
    scoped_fields = {"default_account": _user_accounts}

    class Meta:
        model = UserSettings
        fields = [
            "budget_warning_threshold_1", "budget_warning_threshold_2",
            "bill_reminder_days_ahead", "notifications_enabled", "default_account",
        ]


class FinancialAccountSerializer(serializers.ModelSerializer):
    balance = serializers.SerializerMethodField()

    class Meta:
        model = FinancialAccount
        fields = ["id", "name", "account_type", "currency_code", "opening_balance", "description", "status", "balance"]
        read_only_fields = ["id"]

    def get_balance(self, obj):
        from apps.accounts.services import account_balance

        return account_balance(obj)

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class TransferSerializer(ScopedFieldsMixin, serializers.ModelSerializer):
    scoped_fields = {"from_account": _user_accounts, "to_account": _user_accounts}

    class Meta:
        model = Transfer
        fields = ["id", "from_account", "to_account", "amount", "fee_amount", "occurred_on", "description"]

    def validate(self, attrs):
        if attrs.get("from_account") == attrs.get("to_account"):
            raise serializers.ValidationError("from_account and to_account must differ.")
        return attrs

    def create(self, validated_data):
        from apps.accounts.services import create_transfer

        validated_data["user"] = self.context["request"].user
        return create_transfer(**validated_data)

    def update(self, instance, validated_data):
        from apps.accounts.services import update_transfer

        return update_transfer(instance, **validated_data)


class AccountPostingSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccountPosting
        fields = ["id", "account", "amount", "source_type", "occurred_on", "description"]
        read_only_fields = fields


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "kind", "icon", "user"]
        read_only_fields = ["id", "user"]

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class TransactionSerializer(ScopedFieldsMixin, serializers.ModelSerializer):
    scoped_fields = {"account": _user_accounts, "category": _user_or_global_categories}

    class Meta:
        model = Transaction
        fields = ["id", "account", "kind", "scope", "category", "amount", "fee_amount", "date", "description", "notes"]

    def create(self, validated_data):
        from apps.transactions.services import create_transaction

        validated_data["user"] = self.context["request"].user
        return create_transaction(**validated_data)

    def update(self, instance, validated_data):
        from apps.transactions.services import update_transaction

        return update_transaction(instance, **validated_data)


class TransactionTemplateSerializer(ScopedFieldsMixin, serializers.ModelSerializer):
    scoped_fields = {"account": _user_accounts, "category": _user_or_global_categories}

    class Meta:
        model = TransactionTemplate
        fields = ["id", "name", "kind", "scope", "category", "account", "default_amount", "is_favorite", "use_count", "last_used_at"]
        read_only_fields = ["id", "use_count", "last_used_at"]

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


def _debt_edit(serializer, instance, side, validated_data):
    """Route a PUT/PATCH on a debt through the same validated, locked, ledger-syncing
    edit service the web editor uses. Anything unsafe becomes a 400, never a silent write."""
    from apps.core.audit import log_audit_event
    from apps.debts import editing

    user = serializer.context["request"].user
    try:
        plan = editing.apply_edit(user=user, side=side, pk=instance.pk, changes=dict(validated_data), allow_merge=False)
    except editing.EditError as exc:
        raise serializers.ValidationError(str(exc)) from exc
    if plan["diff"]:
        log_audit_event(user, "update", affected_user=user, obj=instance, description="Edited debt via API",
                        metadata=editing.audit_metadata(plan, side=side))
    instance.refresh_from_db()
    return instance


def _payment_edit(serializer, instance, side, parent_field, validated_data):
    from apps.core.audit import log_audit_event
    from apps.debts import editing

    data = dict(validated_data)
    parent = data.pop(parent_field, None)
    if parent is not None and parent.pk != getattr(instance, parent_field).pk:
        raise serializers.ValidationError({parent_field: "A payment cannot be moved to a different debt."})
    user = serializer.context["request"].user
    try:
        plan = editing.apply_payment_edit(user=user, side=side, pk=instance.pk, changes=data)
    except editing.EditError as exc:
        raise serializers.ValidationError(str(exc)) from exc
    if plan["diff"]:
        log_audit_event(user, "update", affected_user=user, obj=instance, description="Edited debt payment via API",
                        metadata=editing.audit_metadata(plan, side=side))
    instance.refresh_from_db()
    return instance


def _cash_validation(serializer, attrs, model, account_field, message):
    """Cash loans need an account; other types never have one. On a partial
    update the stored values fill in whatever the request omitted."""
    instance = serializer.instance
    debt_type = attrs.get("debt_type", instance.debt_type if instance else model.DebtType.CASH_LOAN)
    account = attrs.get(account_field, getattr(instance, account_field, None) if instance else None)
    if debt_type == model.DebtType.CASH_LOAN:
        if not account:
            raise serializers.ValidationError({account_field: message})
    else:
        attrs[account_field] = None
    return attrs


class BorrowingSerializer(ScopedFieldsMixin, serializers.ModelSerializer):
    scoped_fields = {"receiving_account": _user_accounts}
    outstanding = serializers.SerializerMethodField()

    class Meta:
        model = Borrowing
        fields = [
            "id", "debt_type", "lender_name", "contact", "amount", "date_borrowed", "receiving_account",
            "due_date", "interest_amount", "notes", "status", "outstanding",
        ]
        read_only_fields = ["id", "status"]

    def get_outstanding(self, obj):
        return obj.outstanding()

    def validate(self, attrs):
        return _cash_validation(
            self, attrs, Borrowing, "receiving_account", "A cash loan requires the account that received the money."
        )

    def update(self, instance, validated_data):
        return _debt_edit(self, instance, "lender", validated_data)

    def create(self, validated_data):
        from apps.debts.services import create_borrowing

        validated_data["user"] = self.context["request"].user
        return create_borrowing(**validated_data)


class BorrowingRepaymentSerializer(ScopedFieldsMixin, serializers.ModelSerializer):
    scoped_fields = {
        "paying_account": _user_accounts,
        "borrowing": lambda user: Borrowing.objects.filter(user=user),
    }

    class Meta:
        model = BorrowingRepayment
        fields = ["id", "borrowing", "amount", "fee_amount", "date", "paying_account", "notes"]

    def create(self, validated_data):
        from apps.debts.services import record_borrowing_repayment

        validated_data["user"] = self.context["request"].user
        borrowing = validated_data.pop("borrowing")
        try:
            return record_borrowing_repayment(borrowing=borrowing, **validated_data)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def update(self, instance, validated_data):
        return _payment_edit(self, instance, "lender", "borrowing", validated_data)


class LendingSerializer(ScopedFieldsMixin, serializers.ModelSerializer):
    scoped_fields = {"source_account": _user_accounts}
    outstanding = serializers.SerializerMethodField()

    class Meta:
        model = Lending
        fields = [
            "id", "debt_type", "borrower_name", "contact", "amount", "date_lent", "source_account",
            "due_date", "interest_amount", "notes", "status", "outstanding",
        ]
        read_only_fields = ["id", "status"]
        extra_kwargs = {"source_account": {"required": False, "allow_null": True}}

    def get_outstanding(self, obj):
        return obj.outstanding()

    def validate(self, attrs):
        return _cash_validation(
            self, attrs, Lending, "source_account", "A cash loan requires the account the money was sent from."
        )

    def update(self, instance, validated_data):
        return _debt_edit(self, instance, "borrower", validated_data)

    def create(self, validated_data):
        from apps.debts.services import create_lending

        validated_data["user"] = self.context["request"].user
        return create_lending(**validated_data)


class LendingRepaymentSerializer(ScopedFieldsMixin, serializers.ModelSerializer):
    scoped_fields = {
        "receiving_account": _user_accounts,
        "lending": lambda user: Lending.objects.filter(user=user),
    }

    class Meta:
        model = LendingRepayment
        fields = ["id", "lending", "principal_amount", "interest_amount", "date", "receiving_account", "notes"]

    def create(self, validated_data):
        from apps.debts.services import record_lending_repayment

        validated_data["user"] = self.context["request"].user
        lending = validated_data.pop("lending")
        try:
            return record_lending_repayment(lending=lending, **validated_data)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def update(self, instance, validated_data):
        return _payment_edit(self, instance, "borrower", "lending", validated_data)


class RecurringBillSerializer(ScopedFieldsMixin, serializers.ModelSerializer):
    scoped_fields = {"payment_account": _user_accounts, "category": _user_or_global_categories}
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = RecurringBill
        fields = ["id", "name", "amount", "transaction_fee", "category", "payment_account", "frequency", "next_due_date", "status", "notes", "is_overdue"]

    def get_is_overdue(self, obj):
        return obj.is_overdue()

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class BillPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillPayment
        fields = ["id", "bill", "due_date", "paid_date", "amount", "fee_amount", "transaction"]
        read_only_fields = fields


class BudgetSerializer(ScopedFieldsMixin, serializers.ModelSerializer):
    scoped_fields = {"category": _user_or_global_categories}
    spent = serializers.SerializerMethodField()

    class Meta:
        model = Budget
        fields = ["id", "category", "month", "amount", "spent"]

    def get_spent(self, obj):
        return obj.spent()

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class DailyClosingSerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyClosing
        fields = ["id", "date", "is_closed", "closed_at", "reopened_at", "reconciliation_notes"]

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class SavingsGoalSerializer(serializers.ModelSerializer):
    current_saved = serializers.SerializerMethodField()
    remaining = serializers.SerializerMethodField()

    class Meta:
        model = SavingsGoal
        fields = ["id", "name", "target_amount", "deadline", "description", "priority", "status", "current_saved", "remaining"]

    def get_current_saved(self, obj):
        return obj.current_saved()

    def get_remaining(self, obj):
        return obj.remaining()

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class GoalAllocationSerializer(ScopedFieldsMixin, serializers.ModelSerializer):
    scoped_fields = {"goal": lambda user: SavingsGoal.objects.filter(user=user)}

    class Meta:
        model = GoalAllocation
        fields = ["id", "goal", "amount", "date", "note"]

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class AccountingServiceSerializer(ScopedFieldsMixin, serializers.ModelSerializer):
    operation_key = serializers.UUIDField(write_only=True, required=False)
    create_service = None
    update_service = None

    def _call(self, service, **kwargs):
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            return service(**kwargs)
        except (DjangoValidationError, ValueError) as exc:
            detail = exc.messages if hasattr(exc, "messages") else str(exc)
            raise serializers.ValidationError(detail) from exc

    def create(self, validated_data):
        user = self.context["request"].user
        return self._call(type(self).create_service, user=user, actor=user, **validated_data)

    def update(self, instance, validated_data):
        user = self.context["request"].user
        return self._call(type(self).update_service, instance=instance, user=user, actor=user, **validated_data)


class HeldFundSerializer(AccountingServiceSerializer):
    outstanding = serializers.SerializerMethodField()

    class Meta:
        model = HeldFund
        fields = ["id", "owner_name", "purpose", "notes", "outstanding", "operation_key"]
        read_only_fields = ["id", "outstanding"]

    def get_outstanding(self, obj):
        return obj.outstanding()

    def create(self, validated_data):
        from apps.accounting.services import create_held_fund
        user = self.context["request"].user
        validated_data.pop("operation_key", None)
        return self._call(create_held_fund, user=user, actor=user, **validated_data)

    def update(self, instance, validated_data):
        from apps.accounting.services import update_held_fund
        validated_data.pop("operation_key", None)
        return self._call(update_held_fund, fund=instance, actor=self.context["request"].user, **validated_data)


class HeldFundEntrySerializer(AccountingServiceSerializer):
    scoped_fields = {"fund": lambda user: HeldFund.objects.filter(user=user), "account": _user_accounts}

    class Meta:
        model = HeldFundEntry
        fields = ["id", "fund", "kind", "account", "amount", "fee_amount", "fee_bearer", "date", "description", "notes", "reversed_at", "operation_key"]
        read_only_fields = ["id", "reversed_at"]

    def create(self, validated_data):
        from apps.accounting.services import save_held_entry
        user = self.context["request"].user
        return self._call(save_held_entry, user=user, actor=user, **validated_data)

    def update(self, instance, validated_data):
        from apps.accounting.services import save_held_entry
        user = self.context["request"].user
        return self._call(save_held_entry, user=user, actor=user, instance=instance, **validated_data)


class ReimbursementClaimSerializer(AccountingServiceSerializer):
    scoped_fields = {"account": _user_accounts}
    outstanding = serializers.SerializerMethodField()

    class Meta:
        model = ReimbursementClaim
        fields = ["id", "responsible_party", "account", "purchase_amount", "fee_amount", "fee_bearer", "purchase_date", "description", "receipt_reference", "notes", "outstanding", "reversed_at", "operation_key"]
        read_only_fields = ["id", "outstanding", "reversed_at"]

    def get_outstanding(self, obj):
        return obj.outstanding()

    def create(self, validated_data):
        from apps.accounting.services import create_reimbursement
        user = self.context["request"].user
        return self._call(create_reimbursement, user=user, actor=user, **validated_data)

    def update(self, instance, validated_data):
        from apps.accounting.services import update_reimbursement
        return self._call(update_reimbursement, claim=instance, actor=self.context["request"].user, **validated_data)


class ReimbursementPaymentSerializer(AccountingServiceSerializer):
    scoped_fields = {"claim": lambda user: ReimbursementClaim.objects.filter(user=user, reversed_at__isnull=True), "account": _user_accounts}

    class Meta:
        model = ReimbursementPayment
        fields = ["id", "claim", "account", "amount", "unrecovered_amount", "fee_amount", "fee_bearer", "date", "notes", "reversed_at", "operation_key"]
        read_only_fields = ["id", "reversed_at"]

    def create(self, validated_data):
        from apps.accounting.services import save_reimbursement_payment
        user = self.context["request"].user
        return self._call(save_reimbursement_payment, user=user, actor=user, **validated_data)

    def update(self, instance, validated_data):
        from apps.accounting.services import save_reimbursement_payment
        user = self.context["request"].user
        return self._call(save_reimbursement_payment, user=user, actor=user, instance=instance, **validated_data)


class InventoryLotSerializer(AccountingServiceSerializer):
    scoped_fields = {"account": _user_accounts}
    landed_cost = serializers.ReadOnlyField()
    unit_cost = serializers.ReadOnlyField()
    quantity_remaining = serializers.SerializerMethodField()

    class Meta:
        model = InventoryLot
        fields = ["id", "product_name", "supplier", "source", "account", "acquisition_date", "quantity", "purchase_cost", "shipping_cost", "customs_cost", "transport_cost", "notes", "landed_cost", "unit_cost", "quantity_remaining", "reversed_at", "operation_key"]
        read_only_fields = ["id", "landed_cost", "unit_cost", "quantity_remaining", "reversed_at"]

    def get_quantity_remaining(self, obj):
        return obj.quantity_remaining()

    def create(self, validated_data):
        from apps.accounting.services import save_inventory_lot
        user = self.context["request"].user
        return self._call(save_inventory_lot, user=user, actor=user, **validated_data)

    def update(self, instance, validated_data):
        from apps.accounting.services import save_inventory_lot
        user = self.context["request"].user
        return self._call(save_inventory_lot, user=user, actor=user, instance=instance, **validated_data)


class InventorySaleSerializer(AccountingServiceSerializer):
    scoped_fields = {"lot": lambda user: InventoryLot.objects.filter(user=user, reversed_at__isnull=True)}
    cost_of_goods_sold = serializers.ReadOnlyField()
    gross_profit = serializers.ReadOnlyField()
    outstanding = serializers.SerializerMethodField()

    class Meta:
        model = InventorySale
        fields = ["id", "lot", "customer", "sale_date", "quantity", "sale_amount", "notes", "cost_of_goods_sold", "gross_profit", "outstanding", "reversed_at", "operation_key"]
        read_only_fields = ["id", "cost_of_goods_sold", "gross_profit", "outstanding", "reversed_at"]

    def get_outstanding(self, obj):
        return obj.outstanding()

    def create(self, validated_data):
        from apps.accounting.services import save_inventory_sale
        user = self.context["request"].user
        return self._call(save_inventory_sale, user=user, actor=user, **validated_data)

    def update(self, instance, validated_data):
        from apps.accounting.services import save_inventory_sale
        user = self.context["request"].user
        return self._call(save_inventory_sale, user=user, actor=user, instance=instance, **validated_data)


class InventorySalePaymentSerializer(AccountingServiceSerializer):
    scoped_fields = {"sale": lambda user: InventorySale.objects.filter(user=user, reversed_at__isnull=True), "account": _user_accounts}

    class Meta:
        model = InventorySalePayment
        fields = ["id", "sale", "account", "amount", "fee_amount", "date", "notes", "reversed_at", "operation_key"]
        read_only_fields = ["id", "reversed_at"]

    def create(self, validated_data):
        from apps.accounting.services import save_sale_payment
        user = self.context["request"].user
        return self._call(save_sale_payment, user=user, actor=user, **validated_data)

    def update(self, instance, validated_data):
        from apps.accounting.services import save_sale_payment
        user = self.context["request"].user
        return self._call(save_sale_payment, user=user, actor=user, instance=instance, **validated_data)
