from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse

from . import services
from .forms import (
    HeldFundEntryForm,
    HeldFundForm,
    InventoryLotForm,
    InventorySaleForm,
    InventorySalePaymentForm,
    ReimbursementClaimForm,
    ReimbursementPaymentForm,
    ReversalForm,
)
from .models import HeldFund, HeldFundEntry, InventoryLot, InventorySale, InventorySalePayment, ReimbursementClaim, ReimbursementPayment


def _form_errors(request, exc):
    messages.error(request, "; ".join(exc.messages if hasattr(exc, "messages") else [str(exc)]))


@login_required
def hub(request):
    funds = list(HeldFund.objects.filter(user=request.user).prefetch_related("entries"))
    claims = list(ReimbursementClaim.objects.filter(user=request.user, reversed_at__isnull=True).prefetch_related("payments"))
    lots = list(InventoryLot.objects.filter(user=request.user, reversed_at__isnull=True).prefetch_related("sales__payments"))
    return render(request, "accounting/hub.html", {
        "funds": funds,
        "claims": claims,
        "lots": lots,
        "held_total": sum((f.outstanding() for f in funds), 0),
        "reimbursement_total": sum((c.outstanding() for c in claims), 0),
        "inventory_cost": sum((l.unit_cost * l.quantity_remaining() for l in lots), 0),
    })


def _edit(request, form_class, instance, title, cancel_url, save, initial=None):
    def destination(value):
        target = cancel_url(value)
        return reverse(target[0], args=target[1:]) if isinstance(target, tuple) else reverse(target)

    if request.method == "POST":
        form = form_class(request.POST, user=request.user, instance=instance)
        if form.is_valid():
            try:
                obj = save(form.cleaned_data)
                messages.success(request, f"{title} saved.")
                return redirect(destination(obj))
            except ValidationError as exc:
                _form_errors(request, exc)
    else:
        form = form_class(user=request.user, instance=instance, initial=initial)
    return render(request, "accounting/form.html", {"form": form, "title": title, "cancel_url": destination(instance)})


@login_required
def fund_create(request):
    return _edit(request, HeldFundForm, None, "Funds held for someone else", lambda obj: obj and ("accounting:fund_detail", obj.pk) or "accounting:hub",
                 lambda data: services.create_held_fund(user=request.user, actor=request.user, **data))


@login_required
def fund_edit(request, pk):
    fund = get_object_or_404(HeldFund, pk=pk, user=request.user)
    return _edit(request, HeldFundForm, fund, "Edit held-funds record", lambda obj: ("accounting:fund_detail", fund.pk),
                 lambda data: services.update_held_fund(fund, actor=request.user, **data))


@login_required
def fund_detail(request, pk):
    fund = get_object_or_404(HeldFund.objects.prefetch_related("entries__account"), pk=pk, user=request.user)
    return render(request, "accounting/fund_detail.html", {"fund": fund, "outstanding": fund.outstanding()})


@login_required
def fund_entry(request, pk, entry_pk=None):
    fund = get_object_or_404(HeldFund, pk=pk, user=request.user)
    entry = get_object_or_404(HeldFundEntry, pk=entry_pk, fund=fund, user=request.user, reversed_at__isnull=True) if entry_pk else None
    initial = {"date": timezone.localdate(), "kind": request.GET.get("kind", HeldFundEntry.Kind.RECEIPT)}
    return _edit(request, HeldFundEntryForm, entry, "Held-funds movement", lambda obj: ("accounting:fund_detail", fund.pk),
                 lambda data: services.save_held_entry(user=request.user, actor=request.user, instance=entry, fund=fund, **data), initial)


@login_required
def reimbursement_create(request):
    return _edit(request, ReimbursementClaimForm, None, "Reimbursable purchase", lambda obj: obj and ("accounting:reimbursement_detail", obj.pk) or "accounting:hub",
                 lambda data: services.create_reimbursement(user=request.user, actor=request.user, **data), {"purchase_date": timezone.localdate()})


@login_required
def reimbursement_edit(request, pk):
    claim = get_object_or_404(ReimbursementClaim, pk=pk, user=request.user, reversed_at__isnull=True)
    return _edit(request, ReimbursementClaimForm, claim, "Edit reimbursable purchase", lambda obj: ("accounting:reimbursement_detail", claim.pk),
                 lambda data: services.update_reimbursement(claim, actor=request.user, **data))


@login_required
def reimbursement_detail(request, pk):
    claim = get_object_or_404(ReimbursementClaim.objects.prefetch_related("payments__account"), pk=pk, user=request.user)
    return render(request, "accounting/reimbursement_detail.html", {"claim": claim, "outstanding": claim.outstanding()})


@login_required
def reimbursement_payment(request, pk, payment_pk=None):
    claim = get_object_or_404(ReimbursementClaim, pk=pk, user=request.user, reversed_at__isnull=True)
    payment = get_object_or_404(ReimbursementPayment, pk=payment_pk, claim=claim, user=request.user, reversed_at__isnull=True) if payment_pk else None
    return _edit(request, ReimbursementPaymentForm, payment, "Reimbursement payment", lambda obj: ("accounting:reimbursement_detail", claim.pk),
                 lambda data: services.save_reimbursement_payment(user=request.user, actor=request.user, instance=payment, claim=claim, **data), {"date": timezone.localdate()})


@login_required
def lot_create(request):
    return _edit(request, InventoryLotForm, None, "Inventory purchase", lambda obj: obj and ("accounting:lot_detail", obj.pk) or "accounting:hub",
                 lambda data: services.save_inventory_lot(user=request.user, actor=request.user, **data), {"acquisition_date": timezone.localdate()})


@login_required
def lot_edit(request, pk):
    lot = get_object_or_404(InventoryLot, pk=pk, user=request.user, reversed_at__isnull=True)
    return _edit(request, InventoryLotForm, lot, "Edit inventory purchase", lambda obj: ("accounting:lot_detail", lot.pk),
                 lambda data: services.save_inventory_lot(user=request.user, actor=request.user, instance=lot, **data))


@login_required
def lot_detail(request, pk):
    lot = get_object_or_404(InventoryLot.objects.prefetch_related("sales__payments"), pk=pk, user=request.user)
    sales = lot.sales.all()
    return render(request, "accounting/lot_detail.html", {"lot": lot, "sales": sales, "sold": lot.quantity_sold(), "remaining": lot.quantity_remaining()})


@login_required
def sale_create(request, pk):
    lot = get_object_or_404(InventoryLot, pk=pk, user=request.user, reversed_at__isnull=True)
    return _edit(request, InventorySaleForm, None, "Inventory sale", lambda obj: obj and ("accounting:sale_detail", obj.pk) or ("accounting:lot_detail", lot.pk),
                 lambda data: services.save_inventory_sale(user=request.user, actor=request.user, lot=lot, **data), {"sale_date": timezone.localdate()})


@login_required
def sale_edit(request, pk):
    sale = get_object_or_404(InventorySale, pk=pk, user=request.user, reversed_at__isnull=True)
    return _edit(request, InventorySaleForm, sale, "Edit inventory sale", lambda obj: ("accounting:sale_detail", sale.pk),
                 lambda data: services.save_inventory_sale(user=request.user, actor=request.user, instance=sale, lot=sale.lot, **data))


@login_required
def sale_detail(request, pk):
    sale = get_object_or_404(InventorySale.objects.select_related("lot").prefetch_related("payments__account"), pk=pk, user=request.user)
    return render(request, "accounting/sale_detail.html", {"sale": sale, "paid": sale.amount_paid(), "outstanding": sale.outstanding()})


@login_required
def sale_payment(request, pk, payment_pk=None):
    sale = get_object_or_404(InventorySale, pk=pk, user=request.user, reversed_at__isnull=True)
    payment = get_object_or_404(InventorySalePayment, pk=payment_pk, sale=sale, user=request.user, reversed_at__isnull=True) if payment_pk else None
    return _edit(request, InventorySalePaymentForm, payment, "Customer payment", lambda obj: ("accounting:sale_detail", sale.pk),
                 lambda data: services.save_sale_payment(user=request.user, actor=request.user, instance=payment, sale=sale, **data), {"date": timezone.localdate()})


REVERSIBLE = {
    "held-entry": (HeldFundEntry, lambda o: ("accounting:fund_detail", o.fund_id)),
    "reimbursement": (ReimbursementClaim, lambda o: "accounting:hub"),
    "reimbursement-payment": (ReimbursementPayment, lambda o: ("accounting:reimbursement_detail", o.claim_id)),
    "lot": (InventoryLot, lambda o: "accounting:hub"),
    "sale": (InventorySale, lambda o: ("accounting:lot_detail", o.lot_id)),
    "sale-payment": (InventorySalePayment, lambda o: ("accounting:sale_detail", o.sale_id)),
}


@login_required
def reverse_record(request, kind, pk):
    model, destination = REVERSIBLE.get(kind, (None, None))
    if model is None:
        return redirect("accounting:hub")
    obj = get_object_or_404(model, pk=pk, user=request.user, reversed_at__isnull=True)
    form = ReversalForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.reverse(obj, actor=request.user, reason=form.cleaned_data["reason"])
            messages.success(request, "Record reversed; its cash postings were removed and the audit history was preserved.")
            target = destination(obj)
            return redirect(*target) if isinstance(target, tuple) else redirect(target)
        except ValidationError as exc:
            _form_errors(request, exc)
    return render(request, "accounting/reverse.html", {"form": form, "object": obj})
