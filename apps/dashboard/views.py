from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.dateparse import parse_date

from . import services


@login_required
def home(request):
    if not request.user.profile.first_time_setup_complete:
        return redirect("accounts:onboarding")
    context = services.compute_dashboard(request.user, request.GET.get("month"))
    return render(request, "dashboard/home.html", context)


@login_required
def spending_trend_data(request):
    """AJAX endpoint backing the dashboard's spending-trend period selector.
    Scoped to request.user only — never accepts another user's id. Also
    remembers the chosen period (and custom range, if any) on the user's
    profile so the dashboard reopens with it next time."""
    period = request.GET.get("period", services.TREND_DEFAULT_PERIOD)
    start = parse_date(request.GET.get("start") or "")
    end = parse_date(request.GET.get("end") or "")

    trend = services.spending_trend(request.user, period, start, end)

    profile = request.user.profile
    profile.dashboard_trend_period = trend["period"]
    profile.dashboard_trend_start = start if trend["period"] == "custom" else None
    profile.dashboard_trend_end = end if trend["period"] == "custom" else None
    profile.save(update_fields=["dashboard_trend_period", "dashboard_trend_start", "dashboard_trend_end"])

    return JsonResponse(trend)
