from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.contrib.auth.views import (
    PasswordChangeDoneView as DjangoPasswordChangeDoneView,
)
from django.contrib.auth.views import PasswordChangeView as DjangoPasswordChangeView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import FormView

from apps.core.audit import log_audit_event
from apps.core.user_deletion import delete_user_and_all_data

from .forms import (
    DeactivateAccountForm,
    DeleteAccountForm,
    EmailAuthenticationForm,
    ProfileForm,
    RegistrationForm,
    UserBasicForm,
    UserSettingsForm,
)


def _registration_open():
    try:
        from apps.adminpanel.models import SiteConfiguration

        return SiteConfiguration.get_solo().allow_public_registration
    except Exception:
        return settings.ALLOW_PUBLIC_REGISTRATION


class RegisterView(FormView):
    template_name = "registration/register.html"
    form_class = RegistrationForm
    success_url = reverse_lazy("accounts:onboarding")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard:home")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["registration_open"] = _registration_open()
        return ctx

    def form_valid(self, form):
        if not _registration_open():
            messages.error(self.request, "Registration is currently closed. Please contact an administrator.")
            return self.form_invalid(form)
        user = form.save()
        log_audit_event(user, "create", obj=user, description="User registered")
        login(self.request, user, backend="apps.users.backends.EmailModelBackend")
        messages.success(self.request, "Welcome to MyMoney Tracker! Let's set up your accounts.")
        return super().form_valid(form)


class EmailLoginView(DjangoLoginView):
    template_name = "registration/login.html"
    authentication_form = EmailAuthenticationForm

    def form_valid(self, form):
        response = super().form_valid(form)
        user = self.request.user
        if user.is_deactivated:
            user.is_deactivated = False
            user.deactivated_at = None
            user.save(update_fields=["is_deactivated", "deactivated_at"])
            messages.success(self.request, "Welcome back! Your account has been reactivated.")
        log_audit_event(user, "login", request=self.request)
        return response


class EmailLogoutView(View):
    def post(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            log_audit_event(request.user, "logout", request=request)
        logout(request)
        return redirect(settings.LOGOUT_REDIRECT_URL)

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)


@login_required
def profile_view(request):
    profile = request.user.profile
    if request.method == "POST":
        user_form = UserBasicForm(request.POST, instance=request.user)
        profile_form = ProfileForm(request.POST, instance=profile)
        if user_form.is_valid() and profile_form.is_valid():
            user_form.save()
            profile_form.save()
            messages.success(request, "Profile updated.")
            return redirect("users:profile")
    else:
        user_form = UserBasicForm(instance=request.user)
        profile_form = ProfileForm(instance=profile)
    return render(request, "registration/profile.html", {"user_form": user_form, "profile_form": profile_form})


@login_required
def settings_view(request):
    user_settings = request.user.settings
    if request.method == "POST":
        form = UserSettingsForm(request.POST, instance=user_settings, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Settings updated.")
            return redirect("users:settings")
    else:
        form = UserSettingsForm(instance=user_settings, user=request.user)
    return render(request, "registration/settings.html", {"form": form})


class PasswordChangeView(DjangoPasswordChangeView):
    template_name = "registration/password_change.html"
    success_url = reverse_lazy("users:password_change_done")


class PasswordChangeDoneView(DjangoPasswordChangeDoneView):
    template_name = "registration/password_change_done.html"


@login_required
def deactivate_account_view(request):
    if request.method == "POST":
        form = DeactivateAccountForm(request.POST)
        if form.is_valid():
            user = authenticate(request, username=request.user.email, password=form.cleaned_data["password"])
            if user is None:
                form.add_error("password", "Incorrect password.")
            else:
                from django.utils import timezone

                user.is_deactivated = True
                user.deactivated_at = timezone.now()
                user.save(update_fields=["is_deactivated", "deactivated_at"])
                log_audit_event(user, "update", obj=user, description="User deactivated own account")
                logout(request)
                messages.info(request, "Your account has been deactivated. Log in again to reactivate it.")
                return redirect("users:login")
    else:
        form = DeactivateAccountForm()
    return render(request, "registration/deactivate_account.html", {"form": form})


@login_required
def delete_account_view(request):
    if request.method == "POST":
        form = DeleteAccountForm(request.POST)
        if form.is_valid():
            user = authenticate(request, username=request.user.email, password=form.cleaned_data["password"])
            if user is None:
                form.add_error("password", "Incorrect password.")
            else:
                log_audit_event(None, "delete", description=f"User {user.email} deleted own account")
                logout(request)
                delete_user_and_all_data(user)
                messages.info(request, "Your account and all associated data have been permanently deleted.")
                return redirect("users:login")
    else:
        form = DeleteAccountForm()
    return render(request, "registration/delete_account.html", {"form": form})
