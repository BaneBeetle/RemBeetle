"""
Security headers middleware following OWASP best practices
Adds security headers to all HTTP responses
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Middleware to add security headers to all responses
    Follows OWASP security headers best practices
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Add security headers
        # X-Content-Type-Options: Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # X-Frame-Options: Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # X-XSS-Protection: Enable XSS filtering (legacy but still useful)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Referrer-Policy: Control referrer information
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Permissions-Policy: Restrict unused browser features
        # microphone and camera are intentionally ALLOWED (voice input + vision)
        response.headers["Permissions-Policy"] = "geolocation=()"

        # Content-Security-Policy: Restrict resource loading
        # Note: This is a basic CSP. Adjust based on your frontend needs
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://esm.sh; "  # unsafe-eval for Live2D, esm.sh for Supabase SDK
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "font-src 'self' data:; "
            "connect-src 'self' ws: wss: https://*.supabase.co; "  # Supabase auth API
            "media-src 'self' blob: data:; "  # Added data: for base64 audio
            "frame-ancestors 'none';"
        )
        response.headers["Content-Security-Policy"] = csp

        # Strict-Transport-Security: Force HTTPS
        # Caddy terminates TLS and sets X-Forwarded-Proto
        forwarded_proto = request.headers.get("x-forwarded-proto", "")
        if forwarded_proto == "https" or request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        return response
