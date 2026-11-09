from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from . import services


@login_required
def home(request):
    if not request.user.profile.first_time_setup_complete:
        return redirect("accounts:onboarding")
    context = services.compute_dashboard(request.user)
    return render(request, "dashboard/home.html", context)
