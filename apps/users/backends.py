from django.contrib.auth.backends import ModelBackend


class EmailModelBackend(ModelBackend):
    """ModelBackend only checks is_active. Administrator-suspended accounts
    must be rejected at the authentication layer itself — not just in the
    login form — so suspension holds regardless of which code path
    authenticates the user."""

    def user_can_authenticate(self, user):
        return super().user_can_authenticate(user) and not getattr(user, "is_suspended", False)
