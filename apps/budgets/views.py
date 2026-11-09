import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import BudgetForm
from .models import Budget


@login_required
def budget_list(request):
    month_str = request.GET.get("month")
    if month_str:
        month = datetime.date.fromisoformat(month_str).replace(day=1)
    else:
        month = timezone.localdate().replace(day=1)

    budgets = Budget.objects.filter(user=request.user, month=month).select_related("category")
    thresholds = request.user.settings
    for b in budgets:
        b.pct = b.percent_used()
        b.warn_level = "danger" if b.pct >= thresholds.budget_warning_threshold_2 else (
            "warning" if b.pct >= thresholds.budget_warning_threshold_1 else "ok"
        )
    return render(request, "budgets/budget_list.html", {"budgets": budgets, "month": month})


@login_required
def budget_create(request):
    if request.method == "POST":
        form = BudgetForm(request.POST, user=request.user)
        if form.is_valid():
            budget = form.save(commit=False)
            budget.user = request.user
            try:
                budget.save()
                messages.success(request, "Budget saved.")
                return redirect("budgets:list")
            except IntegrityError:
                messages.error(request, "A budget for this category and month already exists.")
    else:
        form = BudgetForm(user=request.user, initial={"month": timezone.localdate()})
    return render(request, "budgets/budget_form.html", {"form": form})


@login_required
def budget_update(request, pk):
    budget = get_object_or_404(Budget, pk=pk, user=request.user)
    if request.method == "POST":
        form = BudgetForm(request.POST, user=request.user, instance=budget)
        if form.is_valid():
            form.save()
            messages.success(request, "Budget updated.")
            return redirect("budgets:list")
    else:
        form = BudgetForm(user=request.user, instance=budget)
    return render(request, "budgets/budget_form.html", {"form": form})


@login_required
def budget_delete(request, pk):
    budget = get_object_or_404(Budget, pk=pk, user=request.user)
    if request.method == "POST":
        budget.delete()
        messages.success(request, "Budget deleted.")
    return redirect("budgets:list")
