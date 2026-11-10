from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.audit import log_audit_event
from apps.core.models import AuditEvent
from apps.core.user_deletion import delete_user_and_all_data
from apps.users.models import User

from apps.feedback.forms import AdminFeedbackResponseForm
from apps.feedback.models import FeedbackSubmission

from .forms import SiteConfigurationForm
from .models import SiteConfiguration

BACKUP_DIR = Path(settings.BASE_DIR) / "backups"


def _require_staff(view_func):
    return login_required(staff_member_required(view_func))


@_require_staff
def dashboard_home(request):
    stats = {
        "total_users": User.objects.count(),
        "active_users": User.objects.filter(is_active=True, is_suspended=False).count(),
        "suspended_users": User.objects.filter(is_suspended=True).count(),
        "deactivated_users": User.objects.filter(is_deactivated=True).count(),
        "staff_users": User.objects.filter(is_staff=True).count(),
    }
    recent_events = AuditEvent.objects.select_related("actor", "affected_user")[:15]
    return render(request, "adminpanel/dashboard.html", {"stats": stats, "recent_events": recent_events})


@_require_staff
def user_list(request):
    q = request.GET.get("q", "").strip()
    users = User.objects.all()
    if q:
        users = users.filter(Q(email__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))
    paginator = Paginator(users, 25)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "adminpanel/user_list.html", {"page": page, "q": q})


@_require_staff
def user_detail(request, pk):
    target = get_object_or_404(User, pk=pk)
    events = AuditEvent.objects.filter(affected_user=target)[:25]
    return render(request, "adminpanel/user_detail.html", {"target": target, "events": events})


@_require_staff
def user_activate(request, pk):
    if request.method == "POST":
        target = get_object_or_404(User, pk=pk)
        target.is_active = True
        target.is_suspended = False
        target.save(update_fields=["is_active", "is_suspended"])
        log_audit_event(request.user, "admin_action", affected_user=target, obj=target,
                         description="Administrator activated account", request=request)
        messages.success(request, f"{target.email} has been activated.")
    return redirect("adminpanel:user_detail", pk=pk)


@_require_staff
def user_suspend(request, pk):
    if request.method == "POST":
        target = get_object_or_404(User, pk=pk)
        target.is_suspended = True
        target.save(update_fields=["is_suspended"])
        log_audit_event(request.user, "admin_action", affected_user=target, obj=target,
                         description="Administrator suspended account", request=request)
        messages.success(request, f"{target.email} has been suspended.")
    return redirect("adminpanel:user_detail", pk=pk)


@_require_staff
def user_deactivate(request, pk):
    if request.method == "POST":
        target = get_object_or_404(User, pk=pk)
        target.is_active = False
        target.save(update_fields=["is_active"])
        log_audit_event(request.user, "admin_action", affected_user=target, obj=target,
                         description="Administrator deactivated account", request=request)
        messages.success(request, f"{target.email} has been deactivated.")
    return redirect("adminpanel:user_detail", pk=pk)


@_require_staff
def user_delete(request, pk):
    target = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        confirm = request.POST.get("confirm_phrase", "").strip().upper()
        if confirm != "DELETE":
            messages.error(request, 'Type "DELETE" to confirm.')
            return render(request, "adminpanel/user_delete_confirm.html", {"target": target})
        email = target.email
        log_audit_event(request.user, "admin_action", description=f"Administrator deleted account {email}", request=request)
        delete_user_and_all_data(target)
        messages.success(request, f"Account {email} has been permanently deleted.")
        return redirect("adminpanel:user_list")
    return render(request, "adminpanel/user_delete_confirm.html", {"target": target})


@_require_staff
def audit_log(request):
    events = AuditEvent.objects.select_related("actor", "affected_user")
    action = request.GET.get("action", "")
    if action:
        events = events.filter(action=action)
    paginator = Paginator(events, 50)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "adminpanel/audit_log.html", {"page": page, "action": action, "actions": AuditEvent.Action.choices})


@_require_staff
def site_settings(request):
    config = SiteConfiguration.get_solo()
    if request.method == "POST":
        form = SiteConfigurationForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            log_audit_event(request.user, "admin_action", description="Updated site configuration", request=request)
            messages.success(request, "Settings updated.")
            return redirect("adminpanel:site_settings")
    else:
        form = SiteConfigurationForm(instance=config)
    return render(request, "adminpanel/site_settings.html", {"form": form})


@_require_staff
def system_health(request):
    db_ok = True
    db_error = ""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception as exc:  # pragma: no cover - environment dependent
        db_ok = False
        db_error = str(exc)
    context = {
        "db_ok": db_ok,
        "db_error": db_error,
        "debug": settings.DEBUG,
        "total_users": User.objects.count(),
    }
    return render(request, "adminpanel/system_health.html", context)


@_require_staff
def backups_view(request):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(BACKUP_DIR.glob("*.sql"), reverse=True)
    return render(request, "adminpanel/backups.html", {"files": files})


@_require_staff
def backups_trigger(request):
    if request.method == "POST":
        try:
            from django.core.management import call_command

            call_command("backup_db")
            log_audit_event(request.user, "admin_action", description="Triggered database backup", request=request)
            messages.success(request, "Backup created successfully.")
        except Exception as exc:  # pragma: no cover - environment dependent
            messages.error(request, f"Backup failed: {exc}")
    return redirect("adminpanel:backups")


@_require_staff
def feedback_list(request):
    submissions = FeedbackSubmission.objects.select_related("user").all()
    category = request.GET.get("category", "")
    status = request.GET.get("status", "")
    if category:
        submissions = submissions.filter(category=category)
    if status:
        submissions = submissions.filter(status=status)
    paginator = Paginator(submissions, 25)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "adminpanel/feedback_list.html",
        {
            "page": page,
            "category": category,
            "status": status,
            "categories": FeedbackSubmission.Category.choices,
            "statuses": FeedbackSubmission.Status.choices,
        },
    )


@_require_staff
def feedback_detail(request, pk):
    submission = get_object_or_404(FeedbackSubmission.objects.select_related("user"), pk=pk)
    if request.method == "POST":
        form = AdminFeedbackResponseForm(request.POST, instance=submission)
        if form.is_valid():
            submission = form.save(commit=False)
            submission.responded_by = request.user
            submission.responded_at = timezone.now()
            submission.save()
            log_audit_event(
                request.user, "admin_action", obj=submission,
                description=f"Responded to feedback #{submission.pk} from {submission.user.email}",
                request=request,
            )
            messages.success(request, "Response saved.")
            return redirect("adminpanel:feedback_detail", pk=pk)
    else:
        form = AdminFeedbackResponseForm(instance=submission)
    return render(request, "adminpanel/feedback_detail.html", {"submission": submission, "form": form})
