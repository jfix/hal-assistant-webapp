from __future__ import annotations

from django.utils import translation


class UserLanguageMiddleware:
    """Prefer an authenticated user's explicit interface-language choice."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # LocaleMiddleware runs before us and already activated the
        # browser/cookie-detected language. Snapshot it so views can offer an
        # accurate "automatic" fallback even after we override it below.
        request.BROWSER_LANGUAGE_CODE = translation.get_language()
        user = request.user
        if user.is_authenticated:
            from catalog.models import UserInterfacePreference

            language = (
                UserInterfacePreference.objects.filter(user=user)
                .values_list("language", flat=True)
                .first()
            )
            if language:
                translation.activate(language)
                request.LANGUAGE_CODE = language
        response = self.get_response(request)
        translation.deactivate()
        return response


class SecurityHeadersMiddleware:
    """Apply a strict CSP to the app and a compatible policy to Django Admin."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        admin_inline = " 'unsafe-inline'" if request.path.startswith("/admin/") else ""
        response["Content-Security-Policy"] = "; ".join(
            [
                "default-src 'self'",
                f"script-src 'self'{admin_inline}",
                "style-src 'self' 'unsafe-inline'",
                "img-src 'self' data:",
                "font-src 'self'",
                "connect-src 'self'",
                "form-action 'self'",
                "frame-ancestors 'none'",
                "base-uri 'none'",
                "object-src 'none'",
            ]
        )
        response["Referrer-Policy"] = "same-origin"
        response["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response["Cross-Origin-Opener-Policy"] = "same-origin"
        return response
