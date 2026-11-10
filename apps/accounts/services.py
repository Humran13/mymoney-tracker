from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction as db_transaction
from django.db.models import Sum

from .models import AccountPosting, FinancialAccount, Transfer


def sync_postings(source_obj, specs, *, user):
    """Replace every AccountPosting tied to source_obj with the given specs,
    atomically. This is the only path that writes postings, so re-saving an
    edited transaction/transfer/repayment can never leave duplicate or
    stale postings behind — the old set is always deleted first."""
    content_type = ContentType.objects.get_for_model(source_obj)
    with db_transaction.atomic():
        AccountPosting.objects.filter(content_type=content_type, object_id=source_obj.pk).delete()
        AccountPosting.objects.bulk_create(
            [
                AccountPosting(
                    user=user,
                    content_type=content_type,
                    object_id=source_obj.pk,
                    **spec,
                )
                for spec in specs
            ]
        )


def delete_postings(source_obj):
    content_type = ContentType.objects.get_for_model(source_obj)
    AccountPosting.objects.filter(content_type=content_type, object_id=source_obj.pk).delete()


def account_balance(account: FinancialAccount) -> Decimal:
    postings = account.postings.all()
    if account.opening_balance_date is not None:
        postings = postings.filter(occurred_on__gte=account.opening_balance_date)
    total = postings.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    return account.opening_balance + total


def bulk_account_balances(user, accounts=None) -> dict:
    accounts = accounts if accounts is not None else FinancialAccount.objects.filter(user=user)
    accounts = list(accounts)
    # opening_balance_date differs per account, so the date cutoff can't be
    # applied as a single shared queryset filter — split into accounts with
    # a cutoff (one query each, cheap: there are only ever a handful of
    # accounts per user) and accounts with none (one shared query).
    totals = {}
    no_cutoff_ids = [a.id for a in accounts if a.opening_balance_date is None]
    if no_cutoff_ids:
        totals.update(
            AccountPosting.objects.filter(user=user, account_id__in=no_cutoff_ids)
            .values_list("account_id")
            .annotate(total=Sum("amount"))
        )
    for account in accounts:
        if account.opening_balance_date is not None:
            totals[account.id] = (
                AccountPosting.objects.filter(
                    user=user, account=account, occurred_on__gte=account.opening_balance_date
                ).aggregate(total=Sum("amount"))["total"]
                or Decimal("0.00")
            )
    return {acc.id: acc.opening_balance + (totals.get(acc.id) or Decimal("0.00")) for acc in accounts}


def total_cash_on_hand(user) -> Decimal:
    balances = bulk_account_balances(user, FinancialAccount.objects.filter(user=user, status=FinancialAccount.Status.ACTIVE))
    return sum(balances.values(), Decimal("0.00"))


@db_transaction.atomic
def create_transfer(*, user, from_account, to_account, amount, fee_amount, occurred_on, description=""):
    if from_account.id == to_account.id:
        raise ValueError("Cannot transfer to the same account.")
    transfer = Transfer.objects.create(
        user=user,
        from_account=from_account,
        to_account=to_account,
        amount=amount,
        fee_amount=fee_amount or Decimal("0.00"),
        occurred_on=occurred_on,
        description=description,
    )
    _post_transfer(transfer)
    return transfer


@db_transaction.atomic
def update_transfer(transfer: Transfer, **fields):
    for key, value in fields.items():
        setattr(transfer, key, value)
    transfer.full_clean()
    transfer.save()
    _post_transfer(transfer)
    return transfer


def _post_transfer(transfer: Transfer):
    specs = [
        dict(
            account=transfer.from_account,
            amount=-transfer.amount,
            source_type=AccountPosting.SourceType.TRANSFER_OUT,
            occurred_on=transfer.occurred_on,
            description=transfer.description or f"Transfer to {transfer.to_account.name}",
        ),
        dict(
            account=transfer.to_account,
            amount=transfer.amount,
            source_type=AccountPosting.SourceType.TRANSFER_IN,
            occurred_on=transfer.occurred_on,
            description=transfer.description or f"Transfer from {transfer.from_account.name}",
        ),
    ]
    if transfer.fee_amount and transfer.fee_amount > 0:
        specs.append(
            dict(
                account=transfer.from_account,
                amount=-transfer.fee_amount,
                source_type=AccountPosting.SourceType.FEE,
                occurred_on=transfer.occurred_on,
                description=f"Transfer fee: {transfer.description}".strip(": "),
            )
        )
    sync_postings(transfer, specs, user=transfer.user)


@db_transaction.atomic
def delete_transfer(transfer: Transfer):
    delete_postings(transfer)
    transfer.delete()
