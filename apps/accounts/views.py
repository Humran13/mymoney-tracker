from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.forms import modelformset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.audit import log_audit_event

from . import services
from .forms import FinancialAccountForm, TransferForm
from .models import FinancialAccount, Transfer

AccountFormSet = modelformset_factory(FinancialAccount, form=FinancialAccountForm, extra=1, can_delete=False)


@login_required
def onboarding(request):
    profile = request.user.profile
    if profile.first_time_setup_complete:
        return redirect("dashboard:home")

    existing = FinancialAccount.objects.filter(user=request.user)
    formset = AccountFormSet(queryset=existing, prefix="acct")

    if request.method == "POST":
        formset = AccountFormSet(request.POST, queryset=existing, prefix="acct")
        if formset.is_valid():
            instances = formset.save(commit=False)
            for instance in instances:
                instance.user = request.user
                instance.save()
            if FinancialAccount.objects.filter(user=request.user).exists():
                profile.first_time_setup_complete = True
                profile.save(update_fields=["first_time_setup_complete"])
                log_audit_event(request.user, "update", obj=profile, description="Completed first-time setup")
                messages.success(request, "Your accounts are set up. Welcome to MyMoney Tracker!")
                return redirect("dashboard:home")
            messages.error(request, "Add at least one account to continue.")

    return render(request, "accounts/onboarding.html", {"formset": formset})


@login_required
def account_list(request):
    accounts = FinancialAccount.objects.filter(user=request.user).order_by("status", "name")
    balances = services.bulk_account_balances(request.user, accounts)
    for account in accounts:
        account.balance = balances.get(account.id)
    active_total = sum(
        (balances[a.id] for a in accounts if a.status == FinancialAccount.Status.ACTIVE), 0
    )
    return render(request, "accounts/account_list.html", {"accounts": accounts, "total": active_total})


@login_required
def account_create(request):
    if request.method == "POST":
        form = FinancialAccountForm(request.POST)
        if form.is_valid():
            account = form.save(commit=False)
            account.user = request.user
            account.save()
            log_audit_event(request.user, "create", obj=account, description=f"Created account {account.name}")
            messages.success(request, f"Account '{account.name}' created.")
            return redirect("accounts:list")
    else:
        form = FinancialAccountForm(initial={"opening_balance_date": timezone.localdate()})
    return render(request, "accounts/account_form.html", {"form": form, "mode": "create"})


@login_required
def account_update(request, pk):
    account = get_object_or_404(FinancialAccount, pk=pk, user=request.user)
    if request.method == "POST":
        form = FinancialAccountForm(request.POST, instance=account)
        if form.is_valid():
            form.save()
            log_audit_event(request.user, "update", obj=account, description=f"Updated account {account.name}")
            messages.success(request, "Account updated.")
            return redirect("accounts:list")
    else:
        form = FinancialAccountForm(instance=account)
    return render(request, "accounts/account_form.html", {"form": form, "mode": "edit", "account": account})


@login_required
def account_archive(request, pk):
    account = get_object_or_404(FinancialAccount, pk=pk, user=request.user)
    if request.method == "POST":
        account.status = FinancialAccount.Status.ARCHIVED
        account.save(update_fields=["status"])
        log_audit_event(request.user, "update", obj=account, description=f"Archived account {account.name}")
        messages.success(request, f"Account '{account.name}' archived.")
    return redirect("accounts:list")


@login_required
def account_reactivate(request, pk):
    account = get_object_or_404(FinancialAccount, pk=pk, user=request.user)
    if request.method == "POST":
        account.status = FinancialAccount.Status.ACTIVE
        account.save(update_fields=["status"])
        messages.success(request, f"Account '{account.name}' reactivated.")
    return redirect("accounts:list")


@login_required
def transfer_list(request):
    transfers = Transfer.objects.filter(user=request.user).select_related("from_account", "to_account")[:100]
    return render(request, "accounts/transfer_list.html", {"transfers": transfers})


@login_required
def transfer_create(request):
    if request.method == "POST":
        form = TransferForm(request.POST, user=request.user)
        if form.is_valid():
            transfer = services.create_transfer(
                user=request.user,
                from_account=form.cleaned_data["from_account"],
                to_account=form.cleaned_data["to_account"],
                amount=form.cleaned_data["amount"],
                fee_amount=form.cleaned_data["fee_amount"] or 0,
                occurred_on=form.cleaned_data["occurred_on"],
                description=form.cleaned_data["description"],
            )
            log_audit_event(request.user, "create", obj=transfer, description="Created transfer")
            messages.success(request, "Transfer recorded.")
            return redirect("accounts:transfer_list")
    else:
        form = TransferForm(user=request.user, initial={"occurred_on": timezone.localdate()})
    return render(request, "accounts/transfer_form.html", {"form": form})


@login_required
def transfer_delete(request, pk):
    transfer = get_object_or_404(Transfer, pk=pk, user=request.user)
    if request.method == "POST":
        services.delete_transfer(transfer)
        messages.success(request, "Transfer removed and balances recalculated.")
        return redirect("accounts:transfer_list")
    return render(request, "accounts/transfer_delete_confirm.html", {"transfer": transfer})
