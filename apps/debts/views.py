import uuid
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.core.audit import log_audit_event

from . import services, settlements
from .forms import (
    BorrowingForm,
    BorrowingReclassifyForm,
    BorrowingRepaymentForm,
    LendingForm,
    LendingRepaymentForm,
    PersonRenameForm,
    SettlementForm,
    UndoSettlementForm,
)
from .models import Borrowing, BorrowingRepayment, DebtSettlement, Lending


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
        try:
            services.delete_lending(lending)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("debts:lending_detail", pk=pk)
        messages.success(request, "Lending record deleted.")
        return redirect("debts:dashboard")
    return render(request, "debts/lending_delete_confirm.html", {"lending": lending})
