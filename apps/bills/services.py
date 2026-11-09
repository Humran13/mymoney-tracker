from django.db import IntegrityError, transaction as db_transaction

from apps.transactions.services import create_transaction

from .models import BillPayment, RecurringBill


@db_transaction.atomic
def pay_bill(*, user, bill: RecurringBill, paid_date, account=None, amount=None) -> BillPayment:
    account = account or bill.payment_account
    amount = amount if amount is not None else bill.amount
    due_date = bill.next_due_date

    if BillPayment.objects.filter(bill=bill, due_date=due_date).exists():
        raise ValueError("This bill cycle has already been paid.")

    txn = create_transaction(
        user=user,
        account=account,
        kind="expense",
        category=bill.category,
        amount=amount,
        fee_amount=0,
        date=paid_date,
        description=f"Bill: {bill.name}",
    )
    try:
        payment = BillPayment.objects.create(
            user=user, bill=bill, due_date=due_date, paid_date=paid_date, amount=amount, transaction=txn
        )
    except IntegrityError:
        raise ValueError("This bill cycle has already been paid.")

    bill.advance_due_date()
    bill.save(update_fields=["next_due_date"])
    return payment
