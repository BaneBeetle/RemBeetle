"""
Rate limiting middleware for FastAPI
Implements IP-based and user-based rate limiting following OWASP best practices
"""

import time
from collections import defaultdict
from typing import Optional, Dict, Tuple
from fastapi import Request, HTTPException, WebSocket, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect
from loguru import logger


class RateLimiter:
    """
    Rate limiter with IP-based and user-based limiting
    Uses sliding window algorithm for accurate rate limiting
    """

    def __init__(
        self,
        requests_per_minute: int = 60,
        requests_per_hour: int = 1000,
        burst_size: int = 10,
    ):
        """
        Initialize rate limiter

        Args:
            requests_per_minute: Maximum requests per minute per IP/user
            requests_per_hour: Maximum requests per hour per IP/user
            burst_size: Maximum burst requests allowed (not currently used but reserved)
        """
        self.requests_per_minute = requests_per_minute
        self.requests_per_hour = requests_per_hour
        self.burst_size = burst_size

        # Sliding window: {identifier: [timestamps]}
        self.minute_requests: Dict[str, list] = defaultdict(list)
        self.hour_requests: Dict[str, list] = defaultdict(list)

    def _cleanup_old_requests(self, identifier: str) -> None:
        """Remove old requests from sliding window"""
        now = time.time()

        # Clean minute window (keep only last 60 seconds)
        self.minute_requests[identifier] = [
            ts for ts in self.minute_requests[identifier] if now - ts < 60
        ]

        # Clean hour window (keep only last 3600 seconds)
        self.hour_requests[identifier] = [
            ts for ts in self.hour_requests[identifier] if now - ts < 3600
        ]

    def is_allowed(self, identifier: str) -> Tuple[bool, Optional[str], Optional[int]]:
        """
        Check if request is allowed

        Args:
            identifier: Client identifier (IP or user ID)

        Returns:
            Tuple of (is_allowed, error_message, retry_after_seconds)
        """
        now = time.time()

        # Cleanup old requests
        self._cleanup_old_requests(identifier)

        # Check sliding window limits
        minute_count = len(self.minute_requests[identifier])
        hour_count = len(self.hour_requests[identifier])

        if minute_count >= self.requests_per_minute:
            # Calculate retry after (time until oldest request expires)
            if self.minute_requests[identifier]:
                oldest = min(self.minute_requests[identifier])
                retry_after = max(1, int(60 - (now - oldest)))
            else:
                retry_after = 60
            return (
                False,
                f"Rate limit exceeded: {minute_count} requests in the last minute. Maximum allowed: {self.requests_per_minute}",
                retry_after,
            )

        if hour_count >= self.requests_per_hour:
            # Calculate retry after
            if self.hour_requests[identifier]:
                oldest = min(self.hour_requests[identifier])
                retry_after = max(1, int(3600 - (now - oldest)))
            else:
                retry_after = 3600
            return (
                False,
                f"Rate limit exceeded: {hour_count} requests in the last hour. Maximum allowed: {self.requests_per_hour}",
                retry_after,
            )

        # Record request
        self.minute_requests[identifier].append(now)
        self.hour_requests[identifier].append(now)

        return True, None, None


# Global rate limiter instances for different endpoint types
# More lenient for WebSocket connections (they're persistent, so we rate limit connections, not messages)
_websocket_rate_limiter = RateLimiter(
    requests_per_minute=60,  # Allow more WebSocket connections per minute
    requests_per_hour=1000,  # Allow more connections per hour
    burst_size=10,
)

# Standard rate limiter for HTTP endpoints
_http_rate_limiter = RateLimiter(
    requests_per_minute=60,  # 1 request per second average
    requests_per_hour=1000,  # 1000 requests per hour
    burst_size=10,  # Allow small bursts
)

# More restrictive for file upload endpoints
_upload_rate_limiter = RateLimiter(
    requests_per_minute=10,  # Lower for file uploads (more resource intensive)
    requests_per_hour=100,
    burst_size=3,
)


def get_client_identifier(request: Request) -> str:
    """
    Get client identifier for rate limiting
    Uses IP address, with support for X-Forwarded-For header (behind proxy)

    Args:
        request: FastAPI request object

    Returns:
        Client identifier string
    """
    # Check for forwarded IP (behind proxy/load balancer)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take the first IP in the chain (original client)
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        client_ip = request.client.host if request.client else "unknown"

    return client_ip


def get_websocket_identifier(websocket: WebSocket) -> str:
    """
    Get client identifier for WebSocket rate limiting

    Args:
        websocket: WebSocket connection

    Returns:
        Client identifier string
    """
    # Check for forwarded IP
    forwarded_for = websocket.headers.get("X-Forwarded-For")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        client_ip = websocket.client.host if websocket.client else "unknown"

    return client_ip


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware to apply rate limiting to all HTTP requests
    Follows OWASP best practices for rate limiting
    """

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for static files and health checks
        static_paths = [
            "/static/",
            "/cache/",
            "/live2d-models/",
            "/bg/",
            "/avatars/",
            "/web-tool/",
            "/web_tool/",
            "/favicon.ico",
            "/assets/",
            "/libs/",
        ]
        if any(request.url.path.startswith(path) for path in static_paths):
            return await call_next(request)

        # Use upload rate limiter for file upload endpoints
        if request.url.path.startswith("/asr") and request.method == "POST":
            limiter = _upload_rate_limiter
        else:
            limiter = _http_rate_limiter

        identifier = get_client_identifier(request)
        is_allowed, error_msg, retry_after = limiter.is_allowed(identifier)

        if not is_allowed:
            logger.warning(
                f"Rate limit exceeded for {identifier} on {request.url.path}: {error_msg}"
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": "Rate limit exceeded",
                    "message": error_msg,
                    "retry_after": retry_after or 60,
                },
                headers={"Retry-After": str(retry_after or 60)},
            )

        response = await call_next(request)
        return response


async def check_websocket_rate_limit(websocket: WebSocket) -> None:
    """
    Check rate limit for WebSocket connection
    Closes WebSocket connection if rate limit exceeded
    
    Note: This should be called AFTER websocket.accept() to properly close the connection

    Args:
        websocket: WebSocket connection (must be already accepted)

    Raises:
        WebSocketDisconnect: If rate limit exceeded and connection is closed
    """
    identifier = get_websocket_identifier(websocket)
    is_allowed, error_msg, retry_after = _websocket_rate_limiter.is_allowed(identifier)

    if not is_allowed:
        logger.warning(
            f"WebSocket rate limit exceeded for {identifier}: {error_msg}"
        )
        # Send error message before closing
        try:
            await websocket.send_json({
                "type": "error",
                "error": "Rate limit exceeded",
                "message": error_msg,
                "retry_after": retry_after or 60,
            })
        except Exception:
            pass  # Ignore if we can't send
        await websocket.close(code=1008, reason=f"Rate limit exceeded: {error_msg}")
        raise WebSocketDisconnect
