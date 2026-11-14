from django.contrib.auth.backends import ModelBackend
from allauth.account.auth_backends import AuthenticationBackend


class EmailModelBackend(ModelBackend):
    """ModelBackend only checks is_active. Administrator-suspended accounts
    must be rejected at the authentication layer itself — not just in the
    login form — so suspension holds regardless of which code path
    authenticates the user."""

    def user_can_authenticate(self, user):
        return super().user_can_authenticate(user) and not getattr(user, "is_suspended", False)


class SocialAccountBackend(AuthenticationBackend):
    """Backend marker used by django-allauth when it creates a session.

    Password authentication deliberately remains disabled here. The existing
    EmailModelBackend (wrapped by django-axes) stays the only password-login
    path, so installing allauth cannot introduce a rate-limit bypass or an
    alternate email alias through allauth's EmailAddress table.
    """

    def authenticate(self, request, **credentials):
        return None
