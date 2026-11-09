from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.audit import log_audit_event

from . import services
from .forms import PayBillForm, RecurringBillForm
from .models import RecurringBill


@login_required
def bill_list(request):
    bills = RecurringBill.objects.filter(user=request.user).select_related("category", "payment_account")
    overdue = [b for b in bills if b.is_overdue()]
    due_soon = [b for b in bills if not b.is_overdue() and b.is_due_soon(request.user.settings.bill_reminder_days_ahead)]
    return render(request, "bills/bill_list.html", {"bills": bills, "overdue": overdue, "due_soon": due_soon})


@login_required
def bill_create(request):
    if request.method == "POST":
        form = RecurringBillForm(request.POST, user=request.user)
        if form.is_valid():
            bill = form.save(commit=False)
            bill.user = request.user
            bill.save()
            log_audit_event(request.user, "create", obj=bill, description="Added recurring bill")
            messages.success(request, "Recurring bill added.")
            return redirect("bills:list")
    else:
        form = RecurringBillForm(user=request.user, initial={"next_due_date": timezone.localdate()})
    return render(request, "bills/bill_form.html", {"form": form, "mode": "create"})


@login_required
def bill_update(request, pk):
    bill = get_object_or_404(RecurringBill, pk=pk, user=request.user)
    if request.method == "POST":
        form = RecurringBillForm(request.POST, user=request.user, instance=bill)
        if form.is_valid():
            form.save()
            messages.success(request, "Bill updated.")
            return redirect("bills:list")
    else:
        form = RecurringBillForm(user=request.user, instance=bill)
    return render(request, "bills/bill_form.html", {"form": form, "mode": "edit"})


@login_required
def bill_pause(request, pk):
    bill = get_object_or_404(RecurringBill, pk=pk, user=request.user)
    if request.method == "POST":
        bill.status = RecurringBill.Status.PAUSED if bill.status == RecurringBill.Status.ACTIVE else RecurringBill.Status.ACTIVE
        bill.save(update_fields=["status"])
    return redirect("bills:list")


@login_required
def bill_delete(request, pk):
    bill = get_object_or_404(RecurringBill, pk=pk, user=request.user)
    if request.method == "POST":
        bill.delete()
        messages.success(request, "Recurring bill deleted.")
        return redirect("bills:list")
    return render(request, "bills/bill_delete_confirm.html", {"bill": bill})


@login_required
def bill_pay(request, pk):
    bill = get_object_or_404(RecurringBill, pk=pk, user=request.user)
    if request.method == "POST":
        form = PayBillForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                services.pay_bill(
                    user=request.user,
                    bill=bill,
                    paid_date=form.cleaned_data["paid_date"],
                    account=form.cleaned_data["account"],
                    amount=form.cleaned_data["amount"],
                )
                messages.success(request, f"Payment for '{bill.name}' recorded.")
            except ValueError as exc:
                messages.error(request, str(exc))
            return redirect("bills:list")
    else:
        form = PayBillForm(
            user=request.user,
            initial={"paid_date": timezone.localdate(), "account": bill.payment_account, "amount": bill.amount},
        )
    return render(request, "bills/bill_pay.html", {"form": form, "bill": bill})
