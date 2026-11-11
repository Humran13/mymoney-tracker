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
        fields = ["id", "account", "kind", "category", "amount", "fee_amount", "date", "description", "notes"]

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
        fields = ["id", "name", "kind", "category", "account", "default_amount", "is_favorite", "use_count", "last_used_at"]
        read_only_fields = ["id", "use_count", "last_used_at"]

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class BorrowingSerializer(ScopedFieldsMixin, serializers.ModelSerializer):
    scoped_fields = {"receiving_account": _user_accounts}
    outstanding = serializers.SerializerMethodField()

    class Meta:
        model = Borrowing
        fields = [
            "id", "debt_type", "lender_name", "amount", "date_borrowed", "receiving_account",
            "due_date", "interest_amount", "notes", "status", "outstanding",
        ]
        read_only_fields = ["id", "status"]

    def get_outstanding(self, obj):
        return obj.outstanding()

    def validate(self, attrs):
        debt_type = attrs.get("debt_type", Borrowing.DebtType.CASH_LOAN)
        if debt_type == Borrowing.DebtType.CASH_LOAN and not attrs.get("receiving_account"):
            raise serializers.ValidationError(
                {"receiving_account": "A cash loan requires the account that received the money."}
            )
        if debt_type != Borrowing.DebtType.CASH_LOAN:
            attrs["receiving_account"] = None
        return attrs

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
        fields = ["id", "borrowing", "amount", "date", "paying_account", "notes"]

    def create(self, validated_data):
        from apps.debts.services import record_borrowing_repayment

        validated_data["user"] = self.context["request"].user
        borrowing = validated_data.pop("borrowing")
        try:
            return record_borrowing_repayment(borrowing=borrowing, **validated_data)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class LendingSerializer(ScopedFieldsMixin, serializers.ModelSerializer):
    scoped_fields = {"source_account": _user_accounts}
    outstanding = serializers.SerializerMethodField()

    class Meta:
        model = Lending
        fields = [
            "id", "borrower_name", "amount", "date_lent", "source_account",
            "due_date", "interest_amount", "notes", "status", "outstanding",
        ]
        read_only_fields = ["id", "status"]

    def get_outstanding(self, obj):
        return obj.outstanding()

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


class RecurringBillSerializer(ScopedFieldsMixin, serializers.ModelSerializer):
    scoped_fields = {"payment_account": _user_accounts, "category": _user_or_global_categories}
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = RecurringBill
        fields = ["id", "name", "amount", "category", "payment_account", "frequency", "next_due_date", "status", "notes", "is_overdue"]

    def get_is_overdue(self, obj):
        return obj.is_overdue()

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class BillPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillPayment
        fields = ["id", "bill", "due_date", "paid_date", "amount", "transaction"]
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
