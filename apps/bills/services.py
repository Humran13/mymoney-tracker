from django.db import IntegrityError, transaction as db_transaction

from apps.transactions.services import create_transaction

from .models import BillPayment, RecurringBill


@db_transaction.atomic
def pay_bill(*, user, bill: RecurringBill, paid_date, account=None, amount=None, fee_amount=None, expected_due_date=None) -> BillPayment:
    """Pay the current cycle. The account is debited amount + fee through the
    ordinary transaction ledger (an expense posting plus a separate FEE
    posting). The bill row is locked first so two concurrent submits for the
    same cycle serialize and the second sees the first's BillPayment."""
    bill = RecurringBill.objects.select_for_update().get(pk=bill.pk, user=user)
    account = account or bill.payment_account
    amount = amount if amount is not None else bill.amount
    fee_amount = fee_amount if fee_amount is not None else bill.transaction_fee
    if amount <= 0:
        raise ValueError("Bill amount must be greater than zero.")
    if fee_amount < 0:
        raise ValueError("Transaction fee cannot be negative.")
    due_date = bill.next_due_date
    if expected_due_date is not None and expected_due_date != due_date:
        raise ValueError("This bill cycle has already been paid.")

    if BillPayment.objects.filter(bill=bill, due_date=due_date).exists():
        raise ValueError("This bill cycle has already been paid.")

    txn = create_transaction(
        user=user,
        account=account,
        kind="expense",
        category=bill.category,
        amount=amount,
        fee_amount=fee_amount,
        date=paid_date,
        description=f"Bill: {bill.name}",
    )
    try:
        payment = BillPayment.objects.create(
            user=user, bill=bill, due_date=due_date, paid_date=paid_date, amount=amount,
            fee_amount=fee_amount, transaction=txn
        )
    except IntegrityError:
        raise ValueError("This bill cycle has already been paid.")

    bill.advance_due_date()
    bill.save(update_fields=["next_due_date"])
    return payment
