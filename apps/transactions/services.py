from decimal import Decimal

from django.db import transaction as db_transaction
from django.db.models import Count, Max

from apps.accounts.models import AccountPosting
from apps.accounts.services import delete_postings, sync_postings

from .models import Transaction, TransactionTemplate


def _postings_for(txn: Transaction):
    sign = 1 if txn.kind == Transaction.Kind.INCOME else -1
    specs = [
        dict(
            account=txn.account,
            amount=sign * txn.amount,
            source_type=AccountPosting.SourceType.INCOME if sign > 0 else AccountPosting.SourceType.EXPENSE,
            occurred_on=txn.date,
            description=txn.description,
        )
    ]
    if txn.fee_amount and txn.fee_amount > 0:
        specs.append(
            dict(
                account=txn.account,
                amount=-txn.fee_amount,
                source_type=AccountPosting.SourceType.FEE,
                occurred_on=txn.date,
                description=f"Fee: {txn.description}".strip(": "),
            )
        )
    return specs


@db_transaction.atomic
def create_transaction(*, user, **fields) -> Transaction:
    txn = Transaction.objects.create(user=user, **fields)
    sync_postings(txn, _postings_for(txn), user=user)
    return txn


@db_transaction.atomic
def update_transaction(txn: Transaction, **fields) -> Transaction:
    for key, value in fields.items():
        setattr(txn, key, value)
    txn.full_clean(exclude=["user"])
    txn.save()
    sync_postings(txn, _postings_for(txn), user=txn.user)
    return txn


@db_transaction.atomic
def delete_transaction(txn: Transaction):
    delete_postings(txn)
    txn.delete()


def recent_transactions(user, kind=None, limit=8):
    qs = Transaction.objects.filter(user=user)
    if kind:
        qs = qs.filter(kind=kind)
    return qs.select_related("category", "account")[:limit]


def frequent_descriptions(user, kind=None, limit=8):
    qs = Transaction.objects.filter(user=user).exclude(description="")
    if kind:
        qs = qs.filter(kind=kind)
    return (
        qs.values("description", "category_id", "account_id", "kind")
        .annotate(frequency=Count("id"), last_used=Max("date"), avg_amount=Max("amount"))
        .order_by("-frequency", "-last_used")[:limit]
    )


def record_template_use(template: TransactionTemplate):
    from django.utils import timezone

    TransactionTemplate.objects.filter(pk=template.pk).update(
        use_count=template.use_count + 1, last_used_at=timezone.now()
    )
