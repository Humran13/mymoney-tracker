from django.conf import settings
from django.http import HttpResponseNotFound


class OAuthEndpointGuardMiddleware:
    """Expose only the allauth endpoints needed for Google OAuth.

    MyMoney Tracker keeps its existing password login, signup, password reset,
    and logout views. Blocking allauth's parallel account endpoints prevents a
    second password-authentication surface from bypassing django-axes. Google
    endpoints are additionally unavailable until both credentials are set.
    """

    oauth_prefix = "/oauth/"
    allowed_prefixes = (
        "/oauth/3rdparty/",
        "/oauth/reauthenticate/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if not path.startswith(self.oauth_prefix):
            return self.get_response(request)

        if path.startswith("/oauth/google/"):
            if settings.GOOGLE_OAUTH_ENABLED:
                return self.get_response(request)
            return HttpResponseNotFound()

        if any(path.startswith(prefix) for prefix in self.allowed_prefixes):
            return self.get_response(request)

        return HttpResponseNotFound()
