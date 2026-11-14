from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount, SocialLogin, SocialToken
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import FinancialAccount
from apps.adminpanel.models import SiteConfiguration
from apps.core.models import AuditEvent
from apps.users.models import User, UserProfile, UserSettings


TEST_GOOGLE_PROVIDER = {
    "google": {
        "APP": {
            "client_id": "test-client.apps.googleusercontent.com",
            "secret": "test-secret-not-for-production",
            "key": "",
        },
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online", "prompt": "select_account"},
        "OAUTH_PKCE_ENABLED": True,
    }
}


@override_settings(
    GOOGLE_OAUTH_ENABLED=True,
    GOOGLE_OAUTH_CLIENT_ID="test-client.apps.googleusercontent.com",
    GOOGLE_OAUTH_CLIENT_SECRET="test-secret-not-for-production",
    SOCIALACCOUNT_PROVIDERS=TEST_GOOGLE_PROVIDER,
)
class GoogleOAuthTests(TestCase):
    password = "StrongPass123!"

    def _social_login(self, email, uid="google-uid-1", verified=True):
        return SocialLogin(
            user=User(email=email, first_name="Google", last_name="User"),
            account=SocialAccount(
                provider="google",
                uid=uid,
                extra_data={
                    "sub": uid,
                    "email": email,
                    "email_verified": True,
                    "given_name": "Google",
                    "family_name": "User",
                },
            ),
            email_addresses=[EmailAddress(email=email, verified=verified, primary=True)],
        )

    def _start_google(self, process="login", client=None):
        client = client or self.client
        response = client.post(reverse("google_login"), {"process": process})
        self.assertEqual(response.status_code, 302)
        location = urlparse(response.url)
        self.assertEqual(location.netloc, "accounts.google.com")
        query = parse_qs(location.query)
        self.assertIn("state", query)
        self.assertIn("code_challenge", query)
        return query["state"][0]

    def _callback(self, state, sociallogin, client=None):
        client = client or self.client

        def complete_login(adapter, request, app, token, **kwargs):
            # A real Google provider builds SocialLogin with this provider
            # attached. The callback mock supplies only the verified payload,
            # so mirror that one piece of library behavior here.
            sociallogin.provider = adapter.get_provider()
            return sociallogin

        with (
            patch.object(
                GoogleOAuth2Adapter,
                "get_access_token_data",
                return_value={"access_token": "server-side-test-token", "expires_in": 3600},
            ),
            patch.object(GoogleOAuth2Adapter, "complete_login", autospec=True, side_effect=complete_login),
        ):
            return client.get(reverse("google_callback"), {"state": state, "code": "valid-test-code"})

    def test_first_time_google_registration_creates_one_user_profile_and_settings(self):
        state = self._start_google()
        response = self._callback(state, self._social_login("new-google@example.com"))

        user = User.objects.get(email="new-google@example.com")
        self.assertRedirects(response, reverse("accounts:onboarding"), fetch_redirect_response=False)
        self.assertEqual(User.objects.filter(email="new-google@example.com").count(), 1)
        self.assertEqual(UserProfile.objects.filter(user=user).count(), 1)
        self.assertEqual(UserSettings.objects.filter(user=user).count(), 1)
        self.assertFalse(user.profile.first_time_setup_complete)
        self.assertFalse(user.has_usable_password())
        self.assertTrue(SocialAccount.objects.filter(user=user, provider="google", uid="google-uid-1").exists())
        self.assertFalse(SocialToken.objects.exists())
        self.assertTrue(
            AuditEvent.objects.filter(actor=user, action="login", description="Google login successful").exists()
        )

    def test_existing_account_must_explicitly_link_then_google_login_reuses_financial_records(self):
        user = User.objects.create_user(email="owner@example.com", password=self.password)
        account = FinancialAccount.objects.create(
            user=user,
            name="Existing wallet",
            account_type="cash",
            opening_balance=Decimal("25000.00"),
        )
        self.assertTrue(self.client.login(email=user.email, password=self.password))

        state = self._start_google(process="connect")
        response = self._callback(state, self._social_login("different-google@example.com", uid="linked-uid"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("account_reauthenticate")))
        self.assertFalse(SocialAccount.objects.filter(uid="linked-uid").exists())

        response = self.client.post(reverse("account_reauthenticate"), {"password": self.password})
        self.assertRedirects(response, reverse("users:settings"), fetch_redirect_response=False)
        self.assertTrue(SocialAccount.objects.filter(user=user, provider="google", uid="linked-uid").exists())
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(UserProfile.objects.filter(user=user).count(), 1)

        self.client.logout()
        state = self._start_google()
        response = self._callback(
            state,
            self._social_login("different-google@example.com", uid="linked-uid"),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertTrue(FinancialAccount.objects.filter(pk=account.pk, user=user).exists())

    def test_matching_email_never_auto_links_or_creates_a_duplicate(self):
        existing = User.objects.create_user(email="existing@example.com", password=self.password)
        state = self._start_google()
        response = self._callback(state, self._social_login("EXISTING@example.com", uid="unlinked-uid"))

        self.assertRedirects(response, reverse("users:login"), fetch_redirect_response=False)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(UserProfile.objects.filter(user=existing).count(), 1)
        self.assertFalse(SocialAccount.objects.filter(uid="unlinked-uid").exists())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_invalid_or_replayed_oauth_state_is_rejected(self):
        response = self.client.get(
            reverse("google_callback"),
            {"state": "attacker-controlled-state", "code": "untrusted-code"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertContains(response, "Google sign-in failed", status_code=401)
        self.assertEqual(User.objects.count(), 0)

    def test_unverified_google_email_is_rejected(self):
        state = self._start_google()
        response = self._callback(
            state,
            self._social_login("unverified@example.com", uid="unverified-uid", verified=False),
        )

        self.assertRedirects(response, reverse("users:login"), fetch_redirect_response=False)
        self.assertFalse(User.objects.filter(email="unverified@example.com").exists())
        self.assertFalse(SocialAccount.objects.filter(uid="unverified-uid").exists())

    def test_suspended_linked_account_cannot_use_google(self):
        user = User.objects.create_user(email="suspended@example.com", password=self.password, is_suspended=True)
        SocialAccount.objects.create(user=user, provider="google", uid="suspended-uid", extra_data={})

        state = self._start_google()
        response = self._callback(state, self._social_login(user.email, uid="suspended-uid"))

        self.assertRedirects(response, reverse("users:login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertFalse(AuditEvent.objects.filter(actor=user, description="Google login successful").exists())

    def test_administrator_disabled_linked_account_cannot_use_google(self):
        user = User.objects.create_user(email="disabled@example.com", password=self.password, is_active=False)
        SocialAccount.objects.create(user=user, provider="google", uid="disabled-uid", extra_data={})

        state = self._start_google()
        response = self._callback(state, self._social_login(user.email, uid="disabled-uid"))

        self.assertRedirects(response, reverse("users:login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_self_deactivated_account_is_reactivated_after_google_login(self):
        user = User.objects.create_user(
            email="self-deactivated@example.com",
            password=self.password,
            is_deactivated=True,
        )
        SocialAccount.objects.create(user=user, provider="google", uid="reactivate-uid", extra_data={})

        state = self._start_google()
        response = self._callback(state, self._social_login(user.email, uid="reactivate-uid"))

        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertFalse(user.is_deactivated)

    def test_closed_public_registration_blocks_new_google_user_but_not_existing_login(self):
        config = SiteConfiguration.get_solo()
        config.allow_public_registration = False
        config.save()

        state = self._start_google()
        response = self._callback(state, self._social_login("blocked-new@example.com", uid="blocked-new-uid"))
        self.assertRedirects(response, reverse("users:login"), fetch_redirect_response=False)
        self.assertFalse(User.objects.filter(email="blocked-new@example.com").exists())

        existing = User.objects.create_user(email="linked@example.com", password=self.password)
        SocialAccount.objects.create(user=existing, provider="google", uid="existing-uid", extra_data={})
        state = self._start_google()
        response = self._callback(state, self._social_login(existing.email, uid="existing-uid"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), existing.pk)

    def test_provider_start_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.post(reverse("google_login"), {"process": "login"})
        self.assertEqual(response.status_code, 403)

    def test_login_page_has_google_button_and_password_login_still_works(self):
        user = User.objects.create_user(email="password@example.com", password=self.password)
        response = self.client.get(reverse("users:login"))
        self.assertContains(response, "Continue with Google")

        response = self.client.post(
            reverse("users:login"),
            {"username": user.email, "password": self.password},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_parallel_allauth_password_login_is_not_exposed(self):
        response = self.client.get(reverse("account_login"))
        self.assertEqual(response.status_code, 404)


class GoogleOAuthDisabledTests(TestCase):
    @override_settings(GOOGLE_OAUTH_ENABLED=False)
    def test_google_feature_is_hidden_and_provider_endpoint_is_unavailable(self):
        response = self.client.get(reverse("users:login"))
        self.assertNotContains(response, "Continue with Google")

        response = self.client.post(reverse("google_login"), {"process": "login"})
        self.assertEqual(response.status_code, 404)
