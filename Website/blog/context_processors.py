from django.conf import settings


def auth_features(request):
    return {
        "allauth_enabled": getattr(settings, "ALLAUTH_ENABLED", False),
        "google_auth_enabled": getattr(settings, "GOOGLE_AUTH_ENABLED", False),
    }
