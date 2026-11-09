from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.services import total_cash_on_hand
from apps.core.audit import log_audit_event

from . import services
from .forms import GoalAllocationForm, GoalScenarioForm, SavingsGoalForm
from .models import SavingsGoal


@login_required
def goal_list(request):
    goals = SavingsGoal.objects.filter(user=request.user).exclude(status=SavingsGoal.Status.ARCHIVED)
    for g in goals:
        g.saved = g.current_saved()
        g.pct = g.progress_pct()

    total_allocated = sum((g.saved for g in goals if g.status == SavingsGoal.Status.ACTIVE), 0)
    cash_on_hand = total_cash_on_hand(request.user)
    over_allocated = total_allocated > cash_on_hand

    return render(
        request,
        "goals/goal_list.html",
        {"goals": goals, "total_allocated": total_allocated, "cash_on_hand": cash_on_hand, "over_allocated": over_allocated},
    )


@login_required
def goal_create(request):
    if request.method == "POST":
        form = SavingsGoalForm(request.POST)
        if form.is_valid():
            goal = form.save(commit=False)
            goal.user = request.user
            goal.save()
            log_audit_event(request.user, "create", obj=goal, description=f"Created goal {goal.name}")
            messages.success(request, "Goal created.")
            return redirect("goals:detail", pk=goal.pk)
    else:
        form = SavingsGoalForm()
    return render(request, "goals/goal_form.html", {"form": form, "mode": "create"})


@login_required
def goal_update(request, pk):
    goal = get_object_or_404(SavingsGoal, pk=pk, user=request.user)
    if request.method == "POST":
        form = SavingsGoalForm(request.POST, instance=goal)
        if form.is_valid():
            form.save()
            messages.success(request, "Goal updated.")
            return redirect("goals:detail", pk=pk)
    else:
        form = SavingsGoalForm(instance=goal)
    return render(request, "goals/goal_form.html", {"form": form, "mode": "edit"})


@login_required
def goal_detail(request, pk):
    goal = get_object_or_404(SavingsGoal, pk=pk, user=request.user)
    if request.method == "POST":
        form = GoalAllocationForm(request.POST)
        if form.is_valid():
            allocation = form.save(commit=False)
            allocation.goal = goal
            allocation.user = request.user
            allocation.save()
            if goal.is_achieved() and goal.status == SavingsGoal.Status.ACTIVE:
                goal.status = SavingsGoal.Status.COMPLETED
                goal.save(update_fields=["status"])
                messages.success(request, f"🎉 Goal '{goal.name}' reached!")
            else:
                messages.success(request, "Allocation recorded.")
            return redirect("goals:detail", pk=pk)
    else:
        form = GoalAllocationForm(initial={"date": timezone.localdate()})

    forecast = services.forecast_goal(goal)
    target_calc = services.savings_target_calculator(goal)

    scenario_form = GoalScenarioForm(request.GET or None)
    scenario = None
    if request.GET and scenario_form.is_valid():
        scenario = services.goal_scenario(
            goal,
            reduce_expense_monthly=scenario_form.cleaned_data.get("reduce_expense_monthly"),
            increase_income_monthly=scenario_form.cleaned_data.get("increase_income_monthly"),
            deadline=scenario_form.cleaned_data.get("deadline"),
        )

    return render(
        request,
        "goals/goal_detail.html",
        {
            "goal": goal, "form": form, "forecast": forecast, "target_calc": target_calc,
            "scenario_form": scenario_form, "scenario": scenario,
        },
    )


@login_required
def goal_pause(request, pk):
    goal = get_object_or_404(SavingsGoal, pk=pk, user=request.user)
    if request.method == "POST":
        goal.status = SavingsGoal.Status.PAUSED if goal.status == SavingsGoal.Status.ACTIVE else SavingsGoal.Status.ACTIVE
        goal.save(update_fields=["status"])
    return redirect("goals:detail", pk=pk)


@login_required
def goal_archive(request, pk):
    goal = get_object_or_404(SavingsGoal, pk=pk, user=request.user)
    if request.method == "POST":
        goal.status = SavingsGoal.Status.ARCHIVED
        goal.save(update_fields=["status"])
        messages.success(request, "Goal archived.")
    return redirect("goals:list")


@login_required
def required_income(request):
    extend_months = int(request.GET.get("extend_months", 0) or 0)
    result = services.required_income_calculator(request.user, deadline_extension_months=extend_months)
    return render(request, "goals/required_income.html", {"result": result, "extend_months": extend_months})
