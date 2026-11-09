from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.audit import log_audit_event

from . import services
from .forms import TransactionFilterForm, TransactionForm, TransactionTemplateForm
from .models import Transaction, TransactionTemplate


@login_required
def transaction_list(request):
    filter_form = TransactionFilterForm(request.GET or None, user=request.user)
    qs = Transaction.objects.filter(user=request.user).select_related("account", "category")

    if filter_form.is_valid():
        data = filter_form.cleaned_data
        if data.get("kind"):
            qs = qs.filter(kind=data["kind"])
        if data.get("account"):
            qs = qs.filter(account=data["account"])
        if data.get("category"):
            qs = qs.filter(category=data["category"])
        if data.get("date_from"):
            qs = qs.filter(date__gte=data["date_from"])
        if data.get("date_to"):
            qs = qs.filter(date__lte=data["date_to"])
        if data.get("q"):
            qs = qs.filter(description__icontains=data["q"])

    paginator = Paginator(qs, 30)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "transactions/transaction_list.html", {"page": page, "filter_form": filter_form})


@login_required
def transaction_create(request):
    initial = {"date": timezone.localdate(), "kind": request.GET.get("kind", "expense")}
    if request.method == "POST":
        form = TransactionForm(request.POST, user=request.user)
        if form.is_valid():
            txn = services.create_transaction(user=request.user, **form.cleaned_data)
            log_audit_event(request.user, "create", obj=txn, description="Recorded transaction")
            messages.success(request, "Transaction recorded.")
            return redirect("transactions:list")
    else:
        form = TransactionForm(user=request.user, initial=initial)

    recent = services.recent_transactions(request.user, limit=6)
    frequent = services.frequent_descriptions(request.user, limit=6)
    return render(
        request,
        "transactions/transaction_form.html",
        {"form": form, "mode": "create", "recent": recent, "frequent": frequent},
    )


@login_required
def transaction_update(request, pk):
    txn = get_object_or_404(Transaction, pk=pk, user=request.user)
    if request.method == "POST":
        form = TransactionForm(request.POST, user=request.user, instance=txn)
        if form.is_valid():
            services.update_transaction(txn, **form.cleaned_data)
            log_audit_event(request.user, "update", obj=txn, description="Edited transaction")
            messages.success(request, "Transaction updated.")
            return redirect("transactions:list")
    else:
        form = TransactionForm(user=request.user, instance=txn)
    return render(request, "transactions/transaction_form.html", {"form": form, "mode": "edit"})


@login_required
def transaction_delete(request, pk):
    txn = get_object_or_404(Transaction, pk=pk, user=request.user)
    if request.method == "POST":
        services.delete_transaction(txn)
        messages.success(request, "Transaction deleted and balances recalculated.")
        return redirect("transactions:list")
    return render(request, "transactions/transaction_delete_confirm.html", {"txn": txn})


@login_required
def template_list(request):
    templates = TransactionTemplate.objects.filter(user=request.user)
    return render(request, "transactions/template_list.html", {"templates": templates})


@login_required
def template_create(request):
    if request.method == "POST":
        form = TransactionTemplateForm(request.POST, user=request.user)
        if form.is_valid():
            template = form.save(commit=False)
            template.user = request.user
            template.save()
            messages.success(request, "Saved as a favorite.")
            return redirect("transactions:template_list")
    else:
        form = TransactionTemplateForm(user=request.user)
    return render(request, "transactions/template_form.html", {"form": form})


@login_required
def template_delete(request, pk):
    template = get_object_or_404(TransactionTemplate, pk=pk, user=request.user)
    if request.method == "POST":
        template.delete()
        messages.success(request, "Favorite removed.")
    return redirect("transactions:template_list")


@login_required
def template_use(request, pk):
    template = get_object_or_404(TransactionTemplate, pk=pk, user=request.user)
    if request.method == "POST" and template.account and template.default_amount:
        txn = services.create_transaction(
            user=request.user,
            account=template.account,
            kind=template.kind,
            category=template.category,
            amount=template.default_amount,
            fee_amount=0,
            date=timezone.localdate(),
            description=template.name,
        )
        services.record_template_use(template)
        messages.success(request, f"Logged '{template.name}'. Edit it if details changed today.")
        return redirect("transactions:update", pk=txn.pk)
    messages.error(request, "This favorite needs a default account and amount set before it can be reused.")
    return redirect("transactions:template_list")
