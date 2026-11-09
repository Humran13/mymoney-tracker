from django.conf import settings


def app_meta(request):
    return {
        "APP_NAME": "MyMoney Tracker",
        "APP_TAGLINE": "Track Your Money. Reach Your Goals.",
        "DEFAULT_CURRENCY_CODE": settings.DEFAULT_CURRENCY_CODE,
    }
