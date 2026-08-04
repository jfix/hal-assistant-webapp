from __future__ import annotations


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
