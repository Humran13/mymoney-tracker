from django.db import transaction


def delete_user_and_all_data(user):
    """Deleting a User cascades to FinancialAccount, but several models
    reference FinancialAccount with on_delete=PROTECT (deliberately, so a
    single account can't be deleted out from under its transaction history
    via /admin/). Django's deletion collector enforces PROTECT the instant
    it finds a referencing row, even when that row is *also* scheduled for
    cascade-deletion in the very same operation — so a plain `user.delete()`
    raises ProtectedError for any user who has ever recorded a transaction,
    transfer, borrowing, or lending. Deleting those rows explicitly first
    (they're the entire set of models with a PROTECT reference to
    FinancialAccount) clears the block before the cascade reaches it."""
    from apps.accounts.models import Transfer
    from apps.debts.models import Borrowing, BorrowingRepayment, Lending, LendingRepayment
    from apps.transactions.models import Transaction

    with transaction.atomic():
        BorrowingRepayment.objects.filter(user=user).delete()
        LendingRepayment.objects.filter(user=user).delete()
        Transaction.objects.filter(user=user).delete()
        Transfer.objects.filter(user=user).delete()
        Borrowing.objects.filter(user=user).delete()
        Lending.objects.filter(user=user).delete()
        user.delete()
