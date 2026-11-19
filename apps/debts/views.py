import copy
import uuid
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.core.audit import log_audit_event

from . import editing, payments, people, services, settlements
from .forms import (
    BorrowingAddForm,
    BorrowingEditForm,
    BorrowingForm,
    LendingAddForm,
    LendingEditForm,
    BorrowingReclassifyForm, LendingReclassifyForm,
    BorrowingRepaymentForm,
    LendingForm,
    LendingRepaymentForm,
    PersonPaymentForm,
    PersonRenameForm,
    SettlementForm,
    UndoSettlementForm,
)
from .models import Borrowing, BorrowingRepayment, DebtSettlement, Lending, LendingRepayment


@login_required
def dashboard(request):
    positions = settlements.debt_positions(request.user)
    lenders, borrowers = positions["lenders"], positions["borrowers"]
    total_owed_by_me = sum((g["total"] for g in lenders), Decimal("0.00"))
    total_owed_to_me = sum((g["total"] for g in borrowers), Decimal("0.00"))
    for group in (*lenders, *borrowers):
        confirmed = [p for p in group["pairs"] if p["state"] == settlements.CONFIRMED]
        group["confirmed_pairs"] = [p for p in confirmed if p["offsettable"] > 0]
        group["pending_pairs"] = [p for p in group["pairs"] if p["state"] == settlements.NEEDS_CONFIRMATION]

    return render(
        request,
        "debts/dashboard.html",
        {
            "lenders": lenders,
            "borrowers": borrowers,
            "total_owed_by_me": total_owed_by_me,
            "total_owed_to_me": total_owed_to_me,
            "settled_borrowings": Borrowing.objects.filter(user=request.user, status=Borrowing.Status.SETTLED)[:20],
            "settled_lendings": Lending.objects.filter(user=request.user, status=Lending.Status.SETTLED)[:20],
            "settlements": DebtSettlement.objects.filter(user=request.user)
            .select_related("reversal")
            .prefetch_related("lines__borrowing", "lines__lending")[:20],
        },
    )


@login_required
def name_suggest(request):
    """Remembered names for the autocomplete: this user's records only."""
    return JsonResponse({"results": people.suggest(request.user, request.GET.get("q", ""))})


def _side_or_404(side):
    if side not in (settlements.LENDER, settlements.BORROWER):
        raise Http404
    return side


def _person_args(request):
    source = request.POST if request.method == "POST" else request.GET
    return source.get("name", ""), source.get("contact", "")


@login_required
def person_detail(request, side):
    """All of one person's transactions, with the grouped Record payment action."""
    _side_or_404(side)
    name, contact = _person_args(request)
    records = payments.person_records(request.user, side, name, contact)
    if not records:
        messages.info(request, "That person has nothing outstanding.")
        return redirect("debts:dashboard")
    return render(
        request,
        "debts/person_detail.html",
        {
            "side": side, "name": settlements.clean_name(name), "contact": contact, "records": records,
            "total": payments.total_outstanding(records), "is_lender": side == settlements.LENDER,
        },
    )


@login_required
def person_payment(request, side):
    """Grouped Record payment: propose an oldest-first allocation, confirm,
    then record it atomically. The allocation is recomputed from live,
    locked balances on the confirm POST."""
    _side_or_404(side)
    name, contact = _person_args(request)
    records = payments.person_records(request.user, side, name, contact)
    if not records:
        messages.info(request, "That person has nothing outstanding.")
        return redirect("debts:dashboard")
    total = payments.total_outstanding(records)
    is_lender = side == settlements.LENDER
    context = {"side": side, "name": settlements.clean_name(name), "contact": contact, "total": total,
               "records": records, "is_lender": is_lender, "confirming": False}
    if request.method == "POST":
        form = PersonPaymentForm(request.POST, user=request.user, side=side)
        if form.is_valid():
            data = form.cleaned_data
            amount = data["amount"]
            if request.POST.get("confirm") == "1" and payments.already_recorded(request.user, data["token"]):
                messages.info(request, "That payment was already recorded.")
                return redirect("debts:dashboard")
            if amount > total:
                form.add_error("amount", f"That is more than the {total:,.2f} still outstanding.")
            elif request.POST.get("confirm") == "1":
                try:
                    results = payments.record_person_payment(
                        user=request.user, side=side, name=name, contact=contact, principal=amount,
                        interest=data.get("interest_amount") or 0, fee=data.get("fee_amount") or 0,
                        date=data["date"], account=data["account"], notes=data["notes"], token=data["token"],
                    )
                except payments.DuplicatePayment:
                    messages.info(request, "That payment was already recorded.")
                except ValueError as exc:
                    messages.error(request, str(exc))
                else:
                    for _record, repayment in results:
                        log_audit_event(request.user, "create", obj=repayment, description="Grouped payment allocation")
                    messages.success(request, f"Payment of {amount:,.0f} recorded across {len(results)} transaction(s).")
                return redirect("debts:dashboard")
            else:
                allocation = payments.allocate(records, amount)
                context.update(
                    confirming=True, allocation=allocation, remaining=total - amount,
                    interest=data.get("interest_amount") or 0, fee=data.get("fee_amount") or 0,
                )
    else:
        form = PersonPaymentForm(
            user=request.user, side=side, initial={"amount": total, "date": timezone.localdate(), "token": uuid.uuid4()}
        )
    context["form"] = form
    return render(request, "debts/person_payment.html", context)


def _rename(request, side, name, contact, record=None):
    """Edit a person's name/contact across all their transactions on one side,
    or on a single transaction. Never touches amounts, accounts or settlements."""
    pk = record.pk if record else None
    group_label = "Lenders" if side == settlements.LENDER else "Borrowers"
    preview = settlements.rename_preview(request.user, side, name, contact, pk=pk)
    if not preview["count"]:
        messages.error(request, "That person was not found.")
        return redirect("debts:dashboard")
    if request.method == "POST":
        form = PersonRenameForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                result = settlements.rename_person(
                    user=request.user, side=side, name=data["old_name"], contact=data["old_contact"],
                    new_name=data["name"], new_contact=data["contact"], pk=pk,
                    expected_count=data["expected_count"],
                )
            except settlements.RenameError as exc:
                form.add_error(None, str(exc))
            else:
                if result["changed"]:
                    log_audit_event(
                        request.user, "update", affected_user=request.user, obj=preview["records"][0],
                        description=f"Renamed {group_label.lower()[:-1]} '{result['old_name']}' to '{result['new_name']}'",
                        metadata={
                            "side": side, "scope": "transaction" if pk else "person",
                            "old_name": result["old_name"], "old_contact": result["old_contact"],
                            "new_name": result["new_name"], "new_contact": result["new_contact"],
                            "records": result["changed"], "identity_links_removed": result["links_removed"],
                        },
                        request=request,
                    )
                    messages.success(
                        request,
                        f"Updated {len(result['changed'])} transaction{'s' if len(result['changed']) != 1 else ''} "
                        f"to {result['new_name']}. No amounts or account balances changed.",
                    )
                else:
                    messages.info(request, "Nothing changed.")
                return redirect("debts:dashboard")
        else:
            form.is_bound = True
        new_name, new_contact = request.POST.get("name"), request.POST.get("contact", "")
        if new_name:
            preview = settlements.rename_preview(
                request.user, side, name, contact, pk=pk, new_name=new_name, new_contact=new_contact
            )
    else:
        first = preview["records"][0]
        shown_name = settlements.clean_name(getattr(first, "lender_name" if side == settlements.LENDER else "borrower_name"))
        form = PersonRenameForm(
            initial={
                "name": shown_name, "contact": settlements.clean_name(first.contact),
                "expected_count": preview["count"], "old_name": name, "old_contact": contact,
            }
        )
    return render(
        request,
        "debts/person_rename.html",
        {"form": form, "preview": preview, "group_label": group_label, "single": record, "side": side},
    )


@login_required
def person_rename(request, side):
    if side not in (settlements.LENDER, settlements.BORROWER):
        raise Http404
    source = request.POST if request.method == "POST" else request.GET
    return _rename(request, side, source.get("old_name", source.get("name", "")), source.get("old_contact", source.get("contact", "")))


@login_required
def borrowing_rename(request, pk):
    record = get_object_or_404(Borrowing, pk=pk, user=request.user)
    return _rename(request, settlements.LENDER, record.lender_name, record.contact, record)


@login_required
def lending_rename(request, pk):
    record = get_object_or_404(Lending, pk=pk, user=request.user)
    return _rename(request, settlements.BORROWER, record.borrower_name, record.contact, record)


@login_required
def settle(request):
    """Propose, confirm, then record a noncash offset with one person. The
    amount is re-validated against live balances again on the confirm POST."""
    source = request.POST if request.method == "POST" else request.GET
    name, contact = source.get("name", ""), source.get("contact", "")
    other_contact = source.get("other_contact", contact)
    summary = settlements.settlement_summary(request.user, name, contact, other_contact)
    if not summary["borrowings"] or not summary["lendings"]:
        messages.info(request, "Nothing to offset with this person right now.")
        return redirect("debts:dashboard")
    if summary["state"] != settlements.CONFIRMED:
        if summary["state"] == settlements.NEEDS_CONFIRMATION:
            messages.info(request, "Confirm that these entries are the same person first.")
            query = urlencode({"name": name, "contact": contact, "other_contact": other_contact})
            return redirect(f"{reverse('debts:identity')}?{query}")
        messages.error(request, "These entries have different contacts, so they are treated as different people.")
        return redirect("debts:dashboard")

    context = {
        "summary": summary, "name": name, "contact": contact, "other_contact": other_contact, "confirming": False,
    }
    if request.method == "POST":
        form = SettlementForm(request.POST)
        if form.is_valid():
            amount = form.cleaned_data["amount"]
            if amount > summary["maximum"]:
                form.add_error("amount", f"The most you can offset is {summary['maximum']:,.2f}.")
            elif request.POST.get("confirm") == "1":
                try:
                    result = settlements.settle_with_person(
                        user=request.user,
                        name=name,
                        contact=contact,
                        other_contact=other_contact,
                        amount=amount,
                        date=form.cleaned_data["date"],
                        notes=form.cleaned_data["notes"],
                        token=form.cleaned_data["token"],
                    )
                except settlements.DuplicateSettlement:
                    messages.info(request, "That settlement was already recorded.")
                except ValueError as exc:
                    messages.error(request, str(exc))
                else:
                    log_audit_event(request.user, "create", obj=result, description="Offset debts with the same person")
                    messages.success(
                        request, f"Offset {amount:,.0f} with {summary['display_name']}. No account balance changed."
                    )
                return redirect("debts:dashboard")
            else:
                context.update(
                    confirming=True,
                    remaining_owed_by_me=summary["owed_by_me"] - amount,
                    remaining_owed_to_me=summary["owed_to_me"] - amount,
                )
    else:
        form = SettlementForm(initial={"amount": summary["maximum"], "date": timezone.localdate(), "token": uuid.uuid4()})
    context["form"] = form
    return render(request, "debts/settle.html", context)


@login_required
def identity_confirm(request):
    """Ask the user to confirm that a lender-side and a borrower-side entry are
    the same person. Only offered when the names match and the contacts do not
    conflict; when several people share the name, each candidate has its own
    confirm page, so the choice is always explicit."""
    source = request.POST if request.method == "POST" else request.GET
    name, contact = source.get("name", ""), source.get("contact", "")
    other_contact = source.get("other_contact", "")
    found = settlements.find_pair(request.user, name, contact, other_contact)
    if found is None:
        messages.error(request, "Those entries cannot be matched.")
        return redirect("debts:dashboard")
    lender, borrower, pair = found
    if request.method == "POST":
        try:
            settlements.confirm_identity(user=request.user, name=name, contact=contact, other_contact=other_contact)
        except settlements.IdentityError as exc:
            messages.error(request, str(exc))
            return redirect("debts:dashboard")
        messages.success(request, f"{lender['name']} is now recognised as the same person on both sides.")
        return redirect("debts:dashboard")
    return render(
        request,
        "debts/identity_confirm.html",
        {
            "lender": lender, "borrower": borrower, "pair": pair,
            "name": name, "contact": contact, "other_contact": other_contact,
        },
    )


@login_required
def identity_unlink(request):
    if request.method == "POST":
        settlements.unlink_identity(
            user=request.user,
            name=request.POST.get("name", ""),
            contact=request.POST.get("contact", ""),
            other_contact=request.POST.get("other_contact", ""),
        )
        messages.success(request, "Those entries are no longer treated as the same person.")
    return redirect("debts:dashboard")


@login_required
def settlement_undo(request, pk):
    settlement = get_object_or_404(
        DebtSettlement.objects.select_related("reversal").prefetch_related("lines__borrowing", "lines__lending"),
        pk=pk,
        user=request.user,
    )
    if hasattr(settlement, "reversal"):
        messages.info(request, "This settlement was already undone.")
        return redirect("debts:dashboard")
    blockers = settlements.reversal_blockers(settlement)
    form = UndoSettlementForm(request.POST or None, initial={"date": timezone.localdate()})
    if request.method == "POST" and not blockers and form.is_valid():
        try:
            reversal = settlements.reverse_settlement(
                user=request.user,
                settlement_id=settlement.pk,
                date=form.cleaned_data["date"],
                notes=form.cleaned_data["notes"],
            )
        except settlements.AlreadyReversed:
            messages.info(request, "This settlement was already undone.")
            return redirect("debts:dashboard")
        except ValueError as exc:
            form.add_error(None, str(exc))
        else:
            log_audit_event(request.user, "update", obj=reversal, description="Undid a debt offset")
            messages.success(request, "Settlement undone. Both balances were restored; no account balance changed.")
            return redirect("debts:dashboard")
    return render(request, "debts/settlement_undo.html", {"settlement": settlement, "form": form, "blockers": blockers})


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
        try:
            services.delete_borrowing(borrowing)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("debts:borrowing_detail", pk=pk)
        messages.success(request, "Borrowing record deleted.")
        return redirect("debts:dashboard")
    return render(request, "debts/borrowing_delete_confirm.html", {"borrowing": borrowing})


@login_required
def repayment_update(request, pk):
    """Edit a borrowing repayment (see `_payment_edit`)."""
    repayment = get_object_or_404(
        BorrowingRepayment.objects.select_related("borrowing", "paying_account"), pk=pk, user=request.user
    )
    return _payment_edit(request, settlements.LENDER, repayment)


@login_required
def lending_repayment_update(request, pk):
    """Edit a payment received against a loan (see `_payment_edit`)."""
    repayment = get_object_or_404(
        LendingRepayment.objects.select_related("lending", "receiving_account"), pk=pk, user=request.user
    )
    return _payment_edit(request, settlements.BORROWER, repayment)


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
            try:
                services.record_lending_repayment(user=request.user, lending=lending, **form.cleaned_data)
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, "Repayment received recorded.")
                return redirect("debts:lending_detail", pk=pk)
    else:
        form = LendingRepaymentForm(user=request.user, lending=lending, initial={"date": timezone.localdate()})
    person_total = payments.total_outstanding(
        payments.person_records(request.user, settlements.BORROWER, lending.borrower_name, lending.contact)
    )
    return render(request, "debts/lending_detail.html", {"lending": lending, "form": form, "person_total": person_total})


@login_required
def lending_reclassify(request, pk):
    lending = get_object_or_404(Lending, pk=pk, user=request.user)
    if request.method == "POST":
        form = LendingReclassifyForm(request.POST, user=request.user)
        if form.is_valid():
            debt_type = form.cleaned_data["debt_type"]
            source_account = form.cleaned_data.get("source_account")
            if request.POST.get("confirm") == "1":
                services.reclassify_lending(lending=lending, debt_type=debt_type, source_account=source_account)
                log_audit_event(request.user, "update", obj=lending, description="Reclassified debt type")
                messages.success(request, "Debt type updated.")
                return redirect("debts:lending_detail", pk=pk)
            preview = services.preview_reclassify_lending(lending, debt_type, source_account)
            return render(
                request,
                "debts/lending_reclassify.html",
                {"lending": lending, "form": form, "preview": preview, "confirming": True},
            )
    else:
        form = LendingReclassifyForm(
            user=request.user,
            initial={"debt_type": lending.debt_type, "source_account": lending.source_account},
        )
    return render(request, "debts/lending_reclassify.html", {"lending": lending, "form": form, "confirming": False})


@login_required
def lending_delete(request, pk):
    lending = get_object_or_404(Lending, pk=pk, user=request.user)
    if request.method == "POST":
        try:
            services.delete_lending(lending)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("debts:lending_detail", pk=pk)
        messages.success(request, "Lending record deleted.")
        return redirect("debts:dashboard")
    return render(request, "debts/lending_delete_confirm.html", {"lending": lending})


# --- Editing transactions and payments ---------------------------------------


def _side_meta(side):
    if side == settlements.LENDER:
        return Borrowing, "lender_name", "date_borrowed", "debts:borrowing_detail", "debts:borrowing_edit"
    return Lending, "borrower_name", "date_lent", "debts:lending_detail", "debts:lending_edit"


def _person_query(name, contact):
    return urlencode({"name": name, "contact": contact})


def _audit_metadata(plan, side):
    return editing.audit_metadata(
        plan,
        side=side,
        balance_changes=[
            {"account": row["account"].name, "change": str(row["change"]), "before": str(row["before"])}
            for row in plan["balance_changes"]
        ],
    )


@login_required
def person_edit(request, side):
    """The dashboard Edit button. One transaction opens straight into its edit
    form; several are listed so the user picks which to edit, with their payment
    history and an Add Another Transaction action."""
    _side_or_404(side)
    name, contact = _person_args(request)
    records = settlements.rename_targets(request.user, side, name, contact)
    _model, name_attr, date_attr, detail_url, edit_url = _side_meta(side)
    if not records:
        messages.error(request, "That person was not found.")
        return redirect("debts:dashboard")
    if len(records) == 1:
        return redirect(edit_url, pk=records[0].pk)
    records.sort(key=lambda r: (getattr(r, date_attr), r.pk))
    is_lender = side == settlements.LENDER
    rows = []
    for record in records:
        payments_list = []
        related = "paying_account" if is_lender else "receiving_account"
        for repayment in record.repayments.select_related(related).order_by("date", "id"):
            payments_list.append(
                {
                    "date": repayment.date,
                    "account": repayment.paying_account if is_lender else repayment.receiving_account,
                    "amount": repayment.amount if is_lender else repayment.principal_amount,
                    "extra": repayment.fee_amount if is_lender else repayment.interest_amount,
                    "edit_url": reverse(
                        "debts:repayment_update" if is_lender else "debts:lending_repayment_update", args=[repayment.pk]
                    ),
                }
            )
        rows.append(
            {
                "record": record,
                "date": getattr(record, date_attr),
                "account": record.receiving_account if is_lender else record.source_account,
                "total": record.total_owed() if is_lender else record.amount,
                "outstanding": record.outstanding(),
                "payments": payments_list,
                "edit_url": reverse(edit_url, args=[record.pk]),
                "detail_url": reverse(detail_url, args=[record.pk]),
            }
        )
    latest = records[-1]
    shown_name = settlements.clean_name(getattr(latest, name_attr))
    shown_contact = settlements.clean_name(latest.contact)
    return render(
        request,
        "debts/person_edit.html",
        {
            "side": side, "is_lender": is_lender, "name": shown_name, "contact": shown_contact, "rows": rows,
            "total": sum((row["outstanding"] for row in rows), Decimal("0.00")),
            "query": _person_query(shown_name, shown_contact),
        },
    )


@login_required
def person_add(request, side):
    """Add Another Transaction: the person's stored identity is reused (the
    name cannot be retyped or altered), everything else is entered fresh."""
    _side_or_404(side)
    # The person always comes from the URL (the form posts back to the same URL).
    name, contact = request.GET.get("name", ""), request.GET.get("contact", "")
    records = settlements.rename_targets(request.user, side, name, contact)
    if not records:
        messages.error(request, "That person was not found.")
        return redirect("debts:dashboard")
    is_lender = side == settlements.LENDER
    _model, name_attr, date_attr, _detail, _edit = _side_meta(side)
    latest = max(records, key=lambda r: (getattr(r, date_attr), r.pk))
    person = (settlements.clean_name(getattr(latest, name_attr)), settlements.clean_name(latest.contact))
    form_class = BorrowingAddForm if is_lender else LendingAddForm
    back = f"{reverse('debts:person_edit', args=[side])}?{_person_query(*person)}"
    if request.method == "POST":
        form = form_class(request.POST, user=request.user, person=person)
        if form.is_valid():
            try:
                if is_lender:
                    created = services.create_borrowing(user=request.user, **form.cleaned_data)
                else:
                    created = services.create_lending(user=request.user, **form.cleaned_data)
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                log_audit_event(
                    request.user, "create", obj=created,
                    description=f"Added another {'borrowing' if is_lender else 'lending'} for {person[0]}",
                )
                messages.success(request, f"Another transaction recorded for {person[0]}.")
                return redirect(back)
    else:
        form = form_class(user=request.user, person=person, initial={date_attr: timezone.localdate()})
    template = "debts/borrowing_form.html" if is_lender else "debts/lending_form.html"
    return render(request, template, {"form": form, "person_name": person[0], "back_url": back})


def _transaction_edit(request, side, record):
    is_lender = side == settlements.LENDER
    _model, name_attr, _date_attr, detail_url, _edit = _side_meta(side)
    form_class = BorrowingEditForm if is_lender else LendingEditForm
    context = {"side": side, "is_lender": is_lender, "record": record, "confirming": False}
    version = record.updated_at.isoformat()
    if request.method == "POST":
        version = request.POST.get("version", version)
        form = form_class(request.POST, user=request.user, instance=copy.copy(record))
        if form.is_valid():
            changes = dict(form.cleaned_data)
            try:
                plan = editing.plan_edit(request.user, side, record.pk, changes)
                if not plan["diff"]:
                    messages.info(request, "Nothing changed.")
                    return redirect(detail_url, pk=record.pk)
                if plan["needs_confirmation"] and request.POST.get("confirm") != "1":
                    context.update(confirming=True, plan=plan)
                else:
                    plan = editing.apply_edit(
                        user=request.user, side=side, pk=record.pk, changes=changes, version=request.POST.get("version")
                    )
                    log_audit_event(
                        request.user, "update", affected_user=request.user, obj=plan["new"],
                        description="Edited debt transaction", metadata=_audit_metadata(plan, side), request=request,
                    )
                    messages.success(request, "Transaction updated.")
                    return redirect(detail_url, pk=record.pk)
            except editing.EditError as exc:
                form.add_error(None, str(exc))
    else:
        form = form_class(user=request.user, instance=record)
    shown_name = settlements.clean_name(getattr(record, name_attr))
    shown_contact = settlements.clean_name(record.contact)
    context.update(
        form=form, version=version, name=shown_name,
        add_url=f"{reverse('debts:person_add', args=[side])}?{_person_query(shown_name, shown_contact)}",
        rename_url=f"{reverse('debts:person_rename', args=[side])}?{_person_query(shown_name, shown_contact)}",
        detail_url=reverse(detail_url, args=[record.pk]),
        payments=list(record.repayments.order_by("date", "id")),
    )
    return render(request, "debts/transaction_edit.html", context)


@login_required
def borrowing_edit(request, pk):
    return _transaction_edit(request, settlements.LENDER, get_object_or_404(Borrowing, pk=pk, user=request.user))


@login_required
def lending_edit(request, pk):
    return _transaction_edit(request, settlements.BORROWER, get_object_or_404(Lending, pk=pk, user=request.user))


def _payment_edit(request, side, payment):
    """Edit one recorded payment. Balance-affecting changes are previewed and
    must be confirmed; the write re-validates under a lock. Other payments of
    the same debt, grouped siblings and offsets are never touched."""
    is_lender = side == settlements.LENDER
    parent = payment.borrowing if is_lender else payment.lending
    detail_url = "debts:borrowing_detail" if is_lender else "debts:lending_detail"
    form_class = BorrowingRepaymentForm if is_lender else LendingRepaymentForm
    form_kwargs = {"borrowing": parent} if is_lender else {"lending": parent}
    context = {"side": side, "is_lender": is_lender, "payment": payment, "parent": parent, "confirming": False}
    if request.method == "POST":
        form = form_class(request.POST, user=request.user, instance=copy.copy(payment), **form_kwargs)
        if form.is_valid():
            changes = dict(form.cleaned_data)
            try:
                plan = editing.plan_payment_edit(request.user, side, payment.pk, changes)
                if not plan["diff"]:
                    messages.info(request, "Nothing changed.")
                    return redirect(detail_url, pk=parent.pk)
                if plan["needs_confirmation"] and request.POST.get("confirm") != "1":
                    context.update(confirming=True, plan=plan)
                else:
                    plan = editing.apply_payment_edit(user=request.user, side=side, pk=payment.pk, changes=changes)
                    log_audit_event(
                        request.user, "update", affected_user=request.user, obj=payment,
                        description="Edited debt payment", metadata=_audit_metadata(plan, side), request=request,
                    )
                    messages.success(request, "Payment updated and balances recalculated.")
                    return redirect(detail_url, pk=parent.pk)
            except editing.EditError as exc:
                form.add_error(None, str(exc))
    else:
        form = form_class(user=request.user, instance=payment, **form_kwargs)
    context.update(form=form, back_url=reverse(detail_url, args=[parent.pk]))
    return render(request, "debts/repayment_form.html", context)
