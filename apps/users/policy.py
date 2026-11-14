from django.conf import settings


def public_registration_is_open():
    """Return the existing database-backed public-registration policy."""

    try:
        from apps.adminpanel.models import SiteConfiguration

        return SiteConfiguration.get_solo().allow_public_registration
    except Exception:
        # Keep the existing safe startup/migration fallback. During normal
        # operation the singleton database setting is authoritative.
        return settings.ALLOW_PUBLIC_REGISTRATION
