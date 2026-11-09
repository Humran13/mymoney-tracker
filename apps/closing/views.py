import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.core.audit import log_audit_event

from . import services
from .forms import ReconciliationForm
from .models import DailyClosing


@login_required
def today(request):
    return redirect("closing:detail", date=timezone.localdate().isoformat())


@login_required
def detail(request, date):
    the_date = datetime.date.fromisoformat(date)
    closing, _ = DailyClosing.objects.get_or_create(user=request.user, date=the_date)
    breakdown = services.day_breakdown(request.user, the_date)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "close":
            closing.is_closed = True
            closing.closed_at = timezone.now()
            closing.save(update_fields=["is_closed", "closed_at"])
            log_audit_event(request.user, "update", obj=closing, description=f"Closed day {the_date}")
            messages.success(request, f"{the_date} closed.")
        elif action == "reopen":
            closing.is_closed = False
            closing.reopened_at = timezone.now()
            closing.save(update_fields=["is_closed", "reopened_at"])
            log_audit_event(request.user, "update", obj=closing, description=f"Reopened day {the_date}")
            messages.info(request, f"{the_date} reopened for edits.")
        elif action == "reconcile":
            form = ReconciliationForm(request.POST, instance=closing)
            if form.is_valid():
                form.save()
                messages.success(request, "Reconciliation notes saved.")
        return redirect("closing:detail", date=date)

    reconciliation_form = ReconciliationForm(instance=closing)
    prev_date = the_date - datetime.timedelta(days=1)
    next_date = the_date + datetime.timedelta(days=1)
    return render(
        request,
        "closing/detail.html",
        {
            "closing": closing,
            "breakdown": breakdown,
            "date": the_date,
            "prev_date": prev_date,
            "next_date": next_date,
            "reconciliation_form": reconciliation_form,
        },
    )


@login_required
def history(request):
    closings = DailyClosing.objects.filter(user=request.user)[:60]
    return render(request, "closing/history.html", {"closings": closings})
