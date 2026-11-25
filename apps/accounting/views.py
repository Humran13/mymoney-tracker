import mimetypes
from decimal import Decimal
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.urls import reverse

from . import services
from .forms import (
    AccountingAttachmentForm,
    FirstHeldReceiptForm,
    HeldFundEntryForm,
    HeldFundForm,
    InventoryLotForm,
    InventorySaleForm,
    InventorySalePaymentForm,
    ReimbursementClaimForm,
    ReimbursementPaymentForm,
    ReversalForm,
)
from .models import (
    AccountingAttachment,
    HeldFund,
    HeldFundEntry,
    InventoryLot,
    InventorySale,
    InventorySalePayment,
    ReimbursementClaim,
    ReimbursementPayment,
)
from .reporting import period_summary, position_summary

ZERO = Decimal("0.00")


def _form_errors(request, exc):
    messages.error(request, "; ".join(exc.messages if hasattr(exc, "messages") else [str(exc)]))


@login_required
def hub(request):
    today = timezone.localdate()
    period_start = parse_date(request.GET.get("start") or "") or today.replace(day=1)
    period_end = parse_date(request.GET.get("end") or "") or today
    as_of = parse_date(request.GET.get("as_of") or "") or period_end
    if period_start > period_end:
        period_start = period_end.replace(day=1)
    query = request.GET.get("q", "").strip()
    outstanding_only = request.GET.get("status") == "outstanding"

    fund_qs = HeldFund.objects.filter(user=request.user).prefetch_related("entries__account")
    claim_qs = ReimbursementClaim.objects.filter(
        user=request.user, reversed_at__isnull=True
    ).prefetch_related("payments__account")
    lot_qs = InventoryLot.objects.filter(
        user=request.user, reversed_at__isnull=True
    ).prefetch_related("sales__payments")
    if query:
        fund_qs = fund_qs.filter(
            Q(owner_name__icontains=query) | Q(purpose__icontains=query) | Q(entries__sender_name__icontains=query)
        ).distinct()
        claim_qs = claim_qs.filter(
            Q(responsible_party__icontains=query) | Q(supplier__icontains=query) | Q(description__icontains=query)
        ).distinct()
        lot_qs = lot_qs.filter(
            Q(product_name__icontains=query) | Q(supplier__icontains=query) | Q(sales__customer__icontains=query)
        ).distinct()

    funds = list(fund_qs)
    claims = list(claim_qs)
    lots = list(lot_qs)
    if outstanding_only:
        funds = [fund for fund in funds if fund.outstanding() > ZERO]
        claims = [claim for claim in claims if claim.outstanding() > ZERO]
        lots = [lot for lot in lots if lot.quantity_remaining(as_of) > ZERO]

    all_claims = list(
        ReimbursementClaim.objects.filter(user=request.user, reversed_at__isnull=True).prefetch_related("payments")
    )
    active_sales = InventorySale.objects.filter(user=request.user, reversed_at__isnull=True).select_related("lot")
    recent_held = HeldFundEntry.objects.filter(user=request.user).select_related("fund", "account")[:6]
    recent_payments = ReimbursementPayment.objects.filter(user=request.user).select_related("claim", "account")[:5]
    recent_sales = active_sales.prefetch_related("payments")[:5]
    outstanding_sales = [sale for sale in active_sales.prefetch_related("payments") if sale.outstanding() > ZERO]
    period = period_summary(request.user, period_start, period_end)
    position = position_summary(request.user, as_of)
    return render(request, "accounting/hub.html", {
        "funds": funds,
        "claims": claims,
        "lots": lots,
        "held_total": position["held_funds_liability"],
        "reimbursement_total": position["reimbursement_receivable"],
        "inventory_cost": sum(
            (lot.remaining_value(as_of) for lot in InventoryLot.objects.filter(
                user=request.user, reversed_at__isnull=True, acquisition_date__lte=as_of
            )), ZERO
        ),
        "period": period,
        "period_start": period_start,
        "period_end": period_end,
        "as_of": as_of,
        "query": query,
        "outstanding_only": outstanding_only,
        "recent_held": recent_held,
        "recent_payments": recent_payments,
        "recent_sales": recent_sales,
        "outstanding_sales": outstanding_sales[:8],
        "claim_counts": {
            "pending": sum(c.status == "pending" for c in all_claims),
            "partial": sum(c.status == "partially_reimbursed" for c in all_claims),
            "paid": sum(c.status == "fully_reimbursed" for c in all_claims),
            "written_off": sum(c.status == "written_off" for c in all_claims),
        },
    })


def _edit(request, form_class, instance, title, cancel_url, save, initial=None, extra_context=None):
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
    context = {"form": form, "title": title, "cancel_url": destination(instance)}
    context.update(extra_context or {})
    return render(request, "accounting/form.html", context)


@login_required
def first_receipt(request):
    if request.method == "POST":
        form = FirstHeldReceiptForm(request.POST, user=request.user)
        if form.is_valid():
            data = form.cleaned_data
            try:
                fund, _entry = services.create_held_fund_with_receipt(
                    user=request.user,
                    actor=request.user,
                    owner_name=data["owner_name"],
                    purpose=data["purpose"],
                    fund_notes="",
                    operation_key=data["operation_key"],
                    account=data["account"],
                    amount=data["amount"],
                    fee_amount=data["fee_amount"],
                    fee_bearer=data["fee_bearer"],
                    date=data["date"],
                    sender_name=data["sender_name"],
                    recipient_name="",
                    reference=data["reference"],
                    description=data["purpose"],
                    notes=data["notes"],
                )
                messages.success(request, "Fund owner and first receipt saved.")
                return redirect("accounting:fund_detail", pk=fund.pk)
            except ValidationError as exc:
                _form_errors(request, exc)
    else:
        form = FirstHeldReceiptForm(user=request.user, initial={"date": timezone.localdate()})
    return render(request, "accounting/form.html", {
        "form": form,
        "title": "Receive money for someone else",
        "cancel_url": reverse("accounting:hub"),
        "intro": "Record the owner and the first cash receipt together. This money is a liability, not personal income.",
    })


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
    active = fund.entries.filter(reversed_at__isnull=True)
    context = {
        "fund": fund,
        "entries": fund.entries.select_related("account").order_by("date", "id"),
        "outstanding": fund.outstanding(),
        "total_received": active.filter(kind=HeldFundEntry.Kind.RECEIPT).aggregate(t=Sum("amount"))["t"] or ZERO,
        "total_distributed": active.filter(kind=HeldFundEntry.Kind.DISTRIBUTION).aggregate(t=Sum("amount"))["t"] or ZERO,
        "owner_fees": active.filter(fee_bearer=HeldFundEntry.FeeBearer.OWNER).aggregate(t=Sum("fee_amount"))["t"] or ZERO,
        "user_fees": active.filter(fee_bearer=HeldFundEntry.FeeBearer.USER).aggregate(t=Sum("fee_amount"))["t"] or ZERO,
    }
    context.update(_attachment_context(request, fund))
    return render(request, "accounting/fund_detail.html", context)


@login_required
def fund_entry(request, pk, entry_pk=None):
    fund = get_object_or_404(HeldFund, pk=pk, user=request.user)
    entry = get_object_or_404(HeldFundEntry, pk=entry_pk, fund=fund, user=request.user, reversed_at__isnull=True) if entry_pk else None
    kind = request.GET.get("kind", entry.kind if entry else HeldFundEntry.Kind.RECEIPT)
    initial = {"date": timezone.localdate(), "kind": kind}
    title = "Distribute money" if kind == HeldFundEntry.Kind.DISTRIBUTION else "Receive money"
    form_context = {"intro": f"{fund.owner_name} · available liability {fund.outstanding():,.2f} UGX"}
    if entry is None:
        form_context.update({
            "projection_base": fund.outstanding(),
            "projection_mode": "held" if kind == HeldFundEntry.Kind.DISTRIBUTION else "add",
        })
    return _edit(request, HeldFundEntryForm, entry, title, lambda obj: ("accounting:fund_detail", fund.pk),
                 lambda data: services.save_held_entry(user=request.user, actor=request.user, instance=entry, fund=fund, **data), initial,
                 form_context)


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
    context = {
        "claim": claim,
        "outstanding": claim.outstanding(),
        "total_reimbursed": claim.total_reimbursed(),
        "total_written_off": claim.total_written_off(),
    }
    context.update(_attachment_context(request, claim))
    return render(request, "accounting/reimbursement_detail.html", context)


@login_required
def reimbursement_payment(request, pk, payment_pk=None):
    claim = get_object_or_404(ReimbursementClaim, pk=pk, user=request.user, reversed_at__isnull=True)
    payment = get_object_or_404(ReimbursementPayment, pk=payment_pk, claim=claim, user=request.user, reversed_at__isnull=True) if payment_pk else None
    initial = {"date": timezone.localdate()}
    if payment is None:
        initial["amount"] = claim.outstanding()
    form_context = {
        "intro": f"{claim.responsible_party} currently owes {claim.outstanding():,.2f} UGX. You may record a partial payment."
    }
    if payment is None:
        form_context.update({"projection_base": claim.outstanding(), "projection_mode": "reimbursement"})
    return _edit(request, ReimbursementPaymentForm, payment, "Record reimbursement received", lambda obj: ("accounting:reimbursement_detail", claim.pk),
                 lambda data: services.save_reimbursement_payment(user=request.user, actor=request.user, instance=payment, claim=claim, **data), initial,
                 form_context)


@login_required
def lot_create(request):
    return _edit(request, InventoryLotForm, None, "Inventory purchase", lambda obj: obj and ("accounting:lot_detail", obj.pk) or "accounting:hub",
                 lambda data: services.save_inventory_lot(user=request.user, actor=request.user, **data), {"acquisition_date": timezone.localdate()},
                 {"inventory_cost_calculator": True})


@login_required
def lot_edit(request, pk):
    lot = get_object_or_404(InventoryLot, pk=pk, user=request.user, reversed_at__isnull=True)
    return _edit(request, InventoryLotForm, lot, "Edit inventory purchase", lambda obj: ("accounting:lot_detail", lot.pk),
                 lambda data: services.save_inventory_lot(user=request.user, actor=request.user, instance=lot, **data),
                 extra_context={"inventory_cost_calculator": True})


@login_required
def lot_detail(request, pk):
    lot = get_object_or_404(InventoryLot.objects.prefetch_related("sales__payments"), pk=pk, user=request.user)
    sales = lot.sales.all()
    context = {
        "lot": lot, "sales": sales, "sold": lot.quantity_sold(),
        "remaining": lot.quantity_remaining(), "remaining_value": lot.remaining_value(),
    }
    context.update(_attachment_context(request, lot))
    return render(request, "accounting/lot_detail.html", context)


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
    context = {"sale": sale, "paid": sale.amount_paid(), "outstanding": sale.outstanding()}
    context.update(_attachment_context(request, sale))
    return render(request, "accounting/sale_detail.html", context)


@login_required
def sale_payment(request, pk, payment_pk=None):
    sale = get_object_or_404(InventorySale, pk=pk, user=request.user, reversed_at__isnull=True)
    payment = get_object_or_404(InventorySalePayment, pk=payment_pk, sale=sale, user=request.user, reversed_at__isnull=True) if payment_pk else None
    initial = {"date": timezone.localdate()}
    if payment is None:
        initial["amount"] = sale.outstanding()
    form_context = {"intro": f"Customer balance: {sale.outstanding():,.2f} UGX. Partial payments are supported."}
    if payment is None:
        form_context.update({"projection_base": sale.outstanding(), "projection_mode": "subtract"})
    return _edit(request, InventorySalePaymentForm, payment, "Record customer payment", lambda obj: ("accounting:sale_detail", sale.pk),
                 lambda data: services.save_sale_payment(user=request.user, actor=request.user, instance=payment, sale=sale, **data), initial,
                 form_context)


ATTACHMENT_TARGETS = {
    "fund": HeldFund,
    "reimbursement": ReimbursementClaim,
    "lot": InventoryLot,
    "sale": InventorySale,
}


def _attachment_context(request, target):
    content_type = ContentType.objects.get_for_model(target)
    return {
        "attachments": AccountingAttachment.objects.filter(
            user=request.user, content_type=content_type, object_id=target.pk
        ),
        "attachment_form": AccountingAttachmentForm(),
        "attachment_kind": next(key for key, model in ATTACHMENT_TARGETS.items() if isinstance(target, model)),
    }


def _attachment_target(request, kind, pk):
    model = ATTACHMENT_TARGETS.get(kind)
    if model is None:
        raise Http404
    return get_object_or_404(model, pk=pk, user=request.user)


@login_required
def attachment_add(request, kind, pk):
    target = _attachment_target(request, kind, pk)
    if request.method != "POST":
        raise Http404
    form = AccountingAttachmentForm(request.POST, request.FILES)
    form.instance.user = request.user
    form.instance.target = target
    if form.is_valid():
        services.create_attachment(
            user=request.user, actor=request.user, target=target,
            label=form.cleaned_data["label"], document=form.cleaned_data["document"],
        )
        messages.success(request, "Supporting document attached privately.")
    else:
        messages.error(request, "; ".join(error for errors in form.errors.values() for error in errors))
    return redirect(_attachment_destination(kind, target))


def _attachment_destination(kind, target):
    target_name = {
        "fund": ("accounting:fund_detail", target.pk),
        "reimbursement": ("accounting:reimbursement_detail", target.pk),
        "lot": ("accounting:lot_detail", target.pk),
        "sale": ("accounting:sale_detail", target.pk),
    }[kind]
    return reverse(target_name[0], args=target_name[1:])


@login_required
def attachment_download(request, pk):
    attachment = get_object_or_404(AccountingAttachment, pk=pk, user=request.user)
    content_type, _ = mimetypes.guess_type(attachment.document.name)
    response = FileResponse(
        attachment.document.open("rb"),
        content_type=content_type or "application/octet-stream",
        filename=Path(attachment.document.name).name,
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
def attachment_delete(request, pk):
    attachment = get_object_or_404(AccountingAttachment, pk=pk, user=request.user)
    if request.method != "POST":
        raise Http404
    target = attachment.target
    kind = next(key for key, model in ATTACHMENT_TARGETS.items() if isinstance(target, model))
    services.delete_attachment(attachment, actor=request.user)
    messages.success(request, "Supporting document removed.")
    return redirect(_attachment_destination(kind, target))


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
