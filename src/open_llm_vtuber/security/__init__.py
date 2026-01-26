"""
Security module for Open-LLM-VTuber
Provides rate limiting, input validation, and API key management following OWASP best practices
"""

from .rate_limiter import (
    RateLimiter,
    RateLimitMiddleware,
    check_websocket_rate_limit,
    get_client_identifier,
    get_websocket_identifier,
)
from .input_validation import (
    validate_websocket_message,
    validate_file_upload,
    WebSocketMessageSchema,
    FileUploadSchema,
    sanitize_string,
)
from .api_key_manager import APIKeyManager, get_api_key_from_env
from .security_headers import SecurityHeadersMiddleware

__all__ = [
    "RateLimiter",
    "RateLimitMiddleware",
    "check_websocket_rate_limit",
    "get_client_identifier",
    "get_websocket_identifier",
    "validate_websocket_message",
    "validate_file_upload",
    "WebSocketMessageSchema",
    "FileUploadSchema",
    "sanitize_string",
    "APIKeyManager",
    "get_api_key_from_env",
    "SecurityHeadersMiddleware",
]
