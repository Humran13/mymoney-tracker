from django.db.models.signals import post_save
from django.dispatch import receiver

from allauth.account.signals import user_logged_in as allauth_user_logged_in
from allauth.account.signals import user_signed_up as allauth_user_signed_up
from allauth.socialaccount.signals import social_account_added, social_account_removed

from apps.core.audit import log_audit_event

from .models import User, UserProfile, UserSettings


@receiver(post_save, sender=User)
def create_profile_and_settings(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)
        UserSettings.objects.create(user=instance)


def _google_sociallogin(kwargs):
    sociallogin = kwargs.get("sociallogin")
    if sociallogin and sociallogin.account.provider == "google":
        return sociallogin
    return None


@receiver(allauth_user_signed_up)
def audit_google_registration(sender, request, user, **kwargs):
    if _google_sociallogin(kwargs):
        log_audit_event(
            user,
            "create",
            obj=user,
            description="User registered with Google",
            metadata={"provider": "google"},
            request=request,
        )


@receiver(allauth_user_logged_in)
def audit_google_login(sender, request, user, **kwargs):
    if not _google_sociallogin(kwargs):
        return

    if user.is_deactivated:
        user.is_deactivated = False
        user.deactivated_at = None
        user.save(update_fields=["is_deactivated", "deactivated_at"])
        from django.contrib import messages

        messages.success(request, "Welcome back! Your account has been reactivated.")

    log_audit_event(
        user,
        "login",
        obj=user,
        description="Google login successful",
        metadata={"provider": "google"},
        request=request,
    )


@receiver(social_account_added)
def audit_google_connection(sender, request, sociallogin, **kwargs):
    if sociallogin.account.provider == "google":
        log_audit_event(
            request.user,
            "update",
            obj=request.user,
            description="Google account connected",
            metadata={"provider": "google"},
            request=request,
        )


@receiver(social_account_removed)
def audit_google_disconnection(sender, request, socialaccount, **kwargs):
    if socialaccount.provider == "google":
        log_audit_event(
            request.user,
            "update",
            obj=request.user,
            description="Google account disconnected",
            metadata={"provider": "google"},
            request=request,
        )
