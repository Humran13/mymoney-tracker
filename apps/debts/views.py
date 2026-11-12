from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.audit import log_audit_event

from . import services
from .forms import BorrowingForm, BorrowingReclassifyForm, BorrowingRepaymentForm, LendingForm, LendingRepaymentForm
from .models import Borrowing, BorrowingRepayment, Lending


@login_required
def dashboard(request):
    borrowings = Borrowing.objects.filter(user=request.user, status=Borrowing.Status.ACTIVE).select_related("receiving_account")
    lendings = Lending.objects.filter(user=request.user, status=Lending.Status.ACTIVE).select_related("source_account")

    total_owed_by_me = sum((b.outstanding() for b in borrowings), Decimal("0.00"))
    total_owed_to_me = sum((l.outstanding() for l in lendings), Decimal("0.00"))

    return render(
        request,
        "debts/dashboard.html",
        {
            "borrowings": borrowings,
            "lendings": lendings,
            "total_owed_by_me": total_owed_by_me,
            "total_owed_to_me": total_owed_to_me,
        },
    )


@login_required
def borrowing_create(request):
    if request.method == "POST":
        form = BorrowingForm(request.POST, user=request.user)
        if form.is_valid():
            borrowing = services.create_borrowing(user=request.user, **form.cleaned_data)
            log_audit_event(request.user, "create", obj=borrowing, description="Recorded borrowing")
            messages.success(request, "Borrowing recorded.")
            return redirect("debts:borrowing_detail", pk=borrowing.pk)
    else:
        form = BorrowingForm(user=request.user, initial={"date_borrowed": timezone.localdate()})
    return render(request, "debts/borrowing_form.html", {"form": form})


@login_required
def borrowing_detail(request, pk):
    borrowing = get_object_or_404(Borrowing, pk=pk, user=request.user)
    if request.method == "POST":
        form = BorrowingRepaymentForm(request.POST, user=request.user, borrowing=borrowing)
        if form.is_valid():
            services.record_borrowing_repayment(user=request.user, borrowing=borrowing, **form.cleaned_data)
            messages.success(request, "Repayment recorded.")
            return redirect("debts:borrowing_detail", pk=pk)
    else:
        form = BorrowingRepaymentForm(user=request.user, borrowing=borrowing, initial={"date": timezone.localdate()})
    return render(request, "debts/borrowing_detail.html", {"borrowing": borrowing, "form": form})


@login_required
def borrowing_reclassify(request, pk):
    borrowing = get_object_or_404(Borrowing, pk=pk, user=request.user)
    if request.method == "POST":
        form = BorrowingReclassifyForm(request.POST, user=request.user)
        if form.is_valid():
            debt_type = form.cleaned_data["debt_type"]
            receiving_account = form.cleaned_data.get("receiving_account")
            if request.POST.get("confirm") == "1":
                services.reclassify_borrowing(
                    borrowing=borrowing, debt_type=debt_type, receiving_account=receiving_account
                )
                log_audit_event(request.user, "update", obj=borrowing, description="Reclassified debt type")
                messages.success(request, "Debt type updated.")
                return redirect("debts:borrowing_detail", pk=pk)
            preview = services.preview_reclassify_borrowing(borrowing, debt_type, receiving_account)
            return render(
                request,
                "debts/borrowing_reclassify.html",
                {"borrowing": borrowing, "form": form, "preview": preview, "confirming": True},
            )
    else:
        form = BorrowingReclassifyForm(
            user=request.user,
            initial={"debt_type": borrowing.debt_type, "receiving_account": borrowing.receiving_account},
        )
    return render(request, "debts/borrowing_reclassify.html", {"borrowing": borrowing, "form": form, "confirming": False})


@login_required
def borrowing_delete(request, pk):
    borrowing = get_object_or_404(Borrowing, pk=pk, user=request.user)
    if request.method == "POST":
        services.delete_borrowing(borrowing)
        messages.success(request, "Borrowing record deleted.")
        return redirect("debts:dashboard")
    return render(request, "debts/borrowing_delete_confirm.html", {"borrowing": borrowing})


@login_required
def repayment_update(request, pk):
    """Edit a repayment. Both the principal posting and the optional
    transaction-fee posting are rebuilt from scratch by sync_postings, so
    editing can neither duplicate nor orphan a fee."""
    repayment = get_object_or_404(
        BorrowingRepayment.objects.select_related("borrowing", "paying_account"), pk=pk, user=request.user
    )
    borrowing = repayment.borrowing
    if request.method == "POST":
        form = BorrowingRepaymentForm(request.POST, user=request.user, borrowing=borrowing, instance=repayment)
        if form.is_valid():
            try:
                services.update_borrowing_repayment(repayment, **form.cleaned_data)
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                log_audit_event(request.user, "update", obj=repayment, description="Edited debt repayment")
                messages.success(request, "Repayment updated.")
                return redirect("debts:borrowing_detail", pk=borrowing.pk)
    else:
        form = BorrowingRepaymentForm(user=request.user, borrowing=borrowing, instance=repayment)
    return render(
        request,
        "debts/repayment_form.html",
        {"form": form, "borrowing": borrowing, "repayment": repayment, "mode": "edit"},
    )


@login_required
def repayment_delete(request, pk):
    repayment = get_object_or_404(BorrowingRepayment, pk=pk, user=request.user)
    borrowing = repayment.borrowing
    if request.method == "POST":
        services.delete_borrowing_repayment(repayment)
        log_audit_event(request.user, "delete", obj=borrowing, description="Deleted debt repayment")
        messages.success(request, "Repayment deleted and balances recalculated.")
        return redirect("debts:borrowing_detail", pk=borrowing.pk)
    return render(request, "debts/repayment_delete_confirm.html", {"repayment": repayment, "borrowing": borrowing})


@login_required
def lending_create(request):
    if request.method == "POST":
        form = LendingForm(request.POST, user=request.user)
        if form.is_valid():
            lending = services.create_lending(user=request.user, **form.cleaned_data)
            log_audit_event(request.user, "create", obj=lending, description="Recorded lending")
            messages.success(request, "Lending recorded.")
            return redirect("debts:lending_detail", pk=lending.pk)
    else:
        form = LendingForm(user=request.user, initial={"date_lent": timezone.localdate()})
    return render(request, "debts/lending_form.html", {"form": form})


@login_required
def lending_detail(request, pk):
    lending = get_object_or_404(Lending, pk=pk, user=request.user)
    if request.method == "POST":
        form = LendingRepaymentForm(request.POST, user=request.user, lending=lending)
        if form.is_valid():
            services.record_lending_repayment(user=request.user, lending=lending, **form.cleaned_data)
            messages.success(request, "Repayment received recorded.")
            return redirect("debts:lending_detail", pk=pk)
    else:
        form = LendingRepaymentForm(user=request.user, lending=lending, initial={"date": timezone.localdate()})
    return render(request, "debts/lending_detail.html", {"lending": lending, "form": form})


@login_required
def lending_delete(request, pk):
    lending = get_object_or_404(Lending, pk=pk, user=request.user)
    if request.method == "POST":
        services.delete_lending(lending)
        messages.success(request, "Lending record deleted.")
        return redirect("debts:dashboard")
    return render(request, "debts/lending_delete_confirm.html", {"lending": lending})
