from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

from .models import User
from .policy import public_registration_is_open


class MyMoneyAccountAdapter(DefaultAccountAdapter):
    """Keep local account operations on the application's existing views."""

    def is_open_for_signup(self, request):
        # Local registration belongs exclusively to RegisterView. Social
        # registration is authorized separately by the social adapter below.
        return False

    def reauthenticate(self, user, password):
        # Existing local users predate allauth and therefore do not necessarily
        # have an allauth EmailAddress row. Reauthenticate the exact signed-in
        # user instead of performing an email lookup.
        return user.is_logged_in_eligible and user.check_password(password)


class MyMoneySocialAccountAdapter(DefaultSocialAccountAdapter):
    """Google account policy that never links identities by email alone."""

    def can_authenticate_by_email(self, login, email):
        # Defense in depth beyond the corresponding allauth settings: provider
        # email claims must never select an existing MyMoney Tracker user.
        return False

    def authenticate_by_email(self, sociallogin):
        return None

    def _stop_login(self, request, message):
        messages.error(request, message)
        raise ImmediateHttpResponse(redirect("users:login"))

    def _ensure_login_allowed(self, request, user):
        if not user.is_active:
            self._stop_login(
                request,
                "This account has been deactivated by an administrator. Contact support.",
            )
        if user.is_suspended:
            self._stop_login(request, "This account is suspended. Contact an administrator.")

    @staticmethod
    def _verified_email(sociallogin):
        for email_address in sociallogin.email_addresses:
            if email_address.verified and email_address.email:
                return email_address.email.strip().lower()
        return None

    def pre_social_login(self, request, sociallogin):
        if sociallogin.account.provider != "google":
            return

        # A connect flow is authorized by the current authenticated session and
        # allauth's mandatory reauthentication step. The Google email is allowed
        # to differ: it is the stable provider UID, not email equality, that is
        # linked to this explicit account.
        if request.user.is_authenticated:
            self._ensure_login_allowed(request, request.user)
            return

        # A previously linked provider UID identifies exactly one local user.
        if sociallogin.is_existing:
            self._ensure_login_allowed(request, sociallogin.user)
            return

        verified_email = self._verified_email(sociallogin)
        if not verified_email:
            self._stop_login(request, "Google did not provide a verified email address.")

        sociallogin.user.email = verified_email

        # A matching email is never enough to authenticate or connect Google.
        # Requiring local sign-in first prevents account takeover and ensures the
        # existing user/profile/financial records remain the sole account.
        if User.objects.filter(email__iexact=verified_email).exists():
            self._stop_login(
                request,
                "An account with this email already exists. Log in with your password, "
                "then connect Google from Settings.",
            )

    def is_open_for_signup(self, request, sociallogin):
        if public_registration_is_open():
            return True
        self._stop_login(request, "Registration is currently closed. Please contact an administrator.")

    def save_user(self, request, sociallogin, form=None):
        verified_email = self._verified_email(sociallogin)
        if not verified_email:
            self._stop_login(request, "Google did not provide a verified email address.")
        sociallogin.user.email = verified_email
        return super().save_user(request, sociallogin, form=form)

    def get_connect_redirect_url(self, request, socialaccount):
        return reverse("users:settings")
