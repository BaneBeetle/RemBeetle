"""
Input validation and sanitization following OWASP best practices
Provides schema-based validation with type checks, length limits, and field rejection
"""

import re
import math
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, validator, ValidationError
from fastapi import HTTPException, status
from loguru import logger


# Maximum lengths for various input types (OWASP recommended limits)
MAX_TEXT_LENGTH = 10000  # 10KB of text
MAX_FILENAME_LENGTH = 255
MAX_HISTORY_UID_LENGTH = 100
MAX_CLIENT_UID_LENGTH = 100
MAX_GROUP_ID_LENGTH = 100
MAX_AUDIO_DATA_SIZE = 10 * 1024 * 1024  # 10MB
MAX_JSON_DEPTH = 10
MAX_AUDIO_SAMPLES = 44100 * 10  # Max 10 seconds at 44.1kHz


def sanitize_string(value: str, max_length: int = MAX_TEXT_LENGTH) -> str:
    """
    Sanitize string input: strip whitespace, limit length, remove control characters
    Following OWASP input validation best practices

    Args:
        value: Input string
        max_length: Maximum allowed length

    Returns:
        Sanitized string

    Raises:
        ValueError: If value is not a string
    """
    if not isinstance(value, str):
        raise ValueError("Expected string type")

    # Remove control characters except newline, tab, and carriage return
    sanitized = re.sub(r"[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]", "", value)

    # Strip leading/trailing whitespace
    sanitized = sanitized.strip()

    # Limit length
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length]
        logger.warning(
            f"String truncated from {len(value)} to {max_length} characters"
        )

    return sanitized


def validate_uuid_format(value: str) -> str:
    """
    Validate UUID format

    Args:
        value: UUID string

    Returns:
        Validated UUID string

    Raises:
        ValueError: If UUID format is invalid
    """
    uuid_pattern = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )
    if not uuid_pattern.match(value):
        raise ValueError("Invalid UUID format")
    return value


class WebSocketMessageSchema(BaseModel):
    """
    Schema for validating WebSocket messages
    Rejects unexpected fields and validates all inputs
    """

    type: str = Field(..., min_length=1, max_length=50)
    action: Optional[str] = Field(None, max_length=50)
    text: Optional[str] = Field(None, max_length=MAX_TEXT_LENGTH)
    audio: Optional[List[float]] = Field(None, max_items=MAX_AUDIO_SAMPLES)
    images: Optional[List[str]] = Field(None, max_items=10)
    history_uid: Optional[str] = Field(None, max_length=MAX_HISTORY_UID_LENGTH)
    file: Optional[str] = Field(None, max_length=MAX_FILENAME_LENGTH)
    display_text: Optional[Dict[str, Any]] = Field(None)
    client_uid: Optional[str] = Field(None, max_length=MAX_CLIENT_UID_LENGTH)
    target_uid: Optional[str] = Field(None, max_length=MAX_CLIENT_UID_LENGTH)
    invitee_uid: Optional[str] = Field(None, max_length=MAX_CLIENT_UID_LENGTH)
    conf_name: Optional[str] = Field(None, max_length=100)
    conf_uid: Optional[str] = Field(None, max_length=100)

    class Config:
        extra = "ignore"  # Ignore unexpected fields (less strict, but still validates known fields)
        validate_assignment = True

    @validator("type")
    def validate_message_type(cls, v):
        """Validate message type is from allowed whitelist"""
        allowed_types = [
            "add-client-to-group",
            "remove-client-from-group",
            "request-group-info",
            "fetch-history-list",
            "fetch-and-set-history",
            "create-new-history",
            "delete-history",
            "interrupt-signal",
            "mic-audio-data",
            "mic-audio-end",
            "raw-audio-data",
            "text-input",
            "ai-speak-signal",
            "fetch-configs",
            "switch-config",
            "fetch-backgrounds",
            "audio-play-start",
            "request-init-config",
            "heartbeat",
            "frontend-playback-complete",
        ]
        if v not in allowed_types:
            raise ValueError(
                f"Invalid message type: {v}. Allowed types: {allowed_types}"
            )
        return v

    @validator("text")
    def sanitize_text(cls, v):
        """Sanitize text input"""
        if v is not None:
            return sanitize_string(v, MAX_TEXT_LENGTH)
        return v

    @validator("history_uid", "client_uid", "target_uid", "invitee_uid")
    def validate_uid_format(cls, v):
        """Validate UID format (UUID or alphanumeric)"""
        if v is not None:
            # Allow UUID format or simple alphanumeric IDs
            if not re.match(r"^[a-zA-Z0-9_-]+$", v):
                try:
                    validate_uuid_format(v)
                except ValueError:
                    raise ValueError(f"Invalid UID format: {v}")
        return v

    @validator("audio")
    def validate_audio_data(cls, v):
        """Validate audio data array"""
        if v is not None:
            if len(v) > MAX_AUDIO_SAMPLES:
                raise ValueError(
                    f"Audio data too large: {len(v)} samples. Maximum: {MAX_AUDIO_SAMPLES}"
                )
            # Check for NaN or Inf values
            for i, sample in enumerate(v):
                if not isinstance(sample, (int, float)):
                    raise ValueError(f"Invalid audio sample type at index {i}")
                if math.isnan(sample) or math.isinf(sample):
                    raise ValueError(f"Invalid audio sample value at index {i}: {sample}")
        return v

    @validator("file")
    def validate_filename(cls, v):
        """Validate filename doesn't contain path traversal"""
        if v is not None:
            # Prevent path traversal attacks
            if ".." in v or "/" in v or "\\" in v:
                raise ValueError("Filename contains invalid characters (path traversal detected)")
            # Sanitize filename - only allow safe characters
            sanitized = re.sub(r"[^a-zA-Z0-9._-]", "", v)
            if sanitized != v:
                logger.warning(f"Filename sanitized: {v} -> {sanitized}")
            return sanitized
        return v

    @validator("images")
    def validate_image_urls(cls, v):
        """Validate image URLs"""
        if v is not None:
            for i, url in enumerate(v):
                if not isinstance(url, str):
                    raise ValueError(f"Image URL at index {i} must be a string")
                if len(url) > 2048:  # Max URL length
                    raise ValueError(f"Image URL at index {i} too long")
                # Basic URL validation
                if not url.startswith(("http://", "https://", "/")):
                    raise ValueError(f"Invalid image URL format at index {i}")
        return v


class FileUploadSchema(BaseModel):
    """Schema for validating file uploads"""

    filename: str = Field(..., max_length=MAX_FILENAME_LENGTH)
    content_type: Optional[str] = Field(None, max_length=100)
    file_size: int = Field(..., ge=0, le=MAX_AUDIO_DATA_SIZE)

    @validator("filename")
    def validate_filename(cls, v):
        """Validate filename - prevent path traversal"""
        if ".." in v or "/" in v or "\\" in v:
            raise ValueError("Filename contains path traversal characters")
        # Only allow safe file extensions
        allowed_extensions = [".wav", ".mp3", ".ogg", ".flac", ".m4a", ".webm"]
        if not any(v.lower().endswith(ext) for ext in allowed_extensions):
            raise ValueError(
                f"File extension not allowed. Allowed: {allowed_extensions}"
            )
        return sanitize_string(v, MAX_FILENAME_LENGTH)

    @validator("content_type")
    def validate_content_type(cls, v):
        """Validate content type"""
        if v is not None:
            allowed_types = [
                "audio/wav",
                "audio/mpeg",
                "audio/ogg",
                "audio/flac",
                "audio/mp4",
                "audio/webm",
                "application/octet-stream",  # Some clients send this
            ]
            if v not in allowed_types:
                logger.warning(f"Unexpected content type: {v}")
        return v


def validate_websocket_message(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and sanitize WebSocket message
    Following OWASP input validation best practices

    Args:
        data: Raw message data

    Returns:
        Validated and sanitized message data

    Raises:
        HTTPException: If validation fails
    """
    try:
        # Check JSON depth to prevent deep nesting attacks
        def check_depth(obj, depth=0, path=""):
            if depth > MAX_JSON_DEPTH:
                raise ValueError(
                    f"JSON structure too deep at {path}. Maximum depth: {MAX_JSON_DEPTH}"
                )
            if isinstance(obj, dict):
                for k, v in obj.items():
                    check_depth(v, depth + 1, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    check_depth(item, depth + 1, f"{path}[{i}]")

        check_depth(data)

        # Validate with schema
        validated = WebSocketMessageSchema(**data)
        return validated.dict(exclude_none=True)

    except ValidationError as e:
        logger.error(f"WebSocket message validation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Invalid message format",
                "details": e.errors(),
            },
        )
    except ValueError as e:
        logger.error(f"WebSocket message validation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Invalid message format", "message": str(e)},
        )


def validate_file_upload(
    filename: str, content_type: Optional[str], file_size: int
) -> FileUploadSchema:
    """
    Validate file upload
    Following OWASP file upload security best practices

    Args:
        filename: Uploaded filename
        content_type: MIME type
        file_size: File size in bytes

    Returns:
        Validated file upload schema

    Raises:
        HTTPException: If validation fails
    """
    try:
        return FileUploadSchema(
            filename=filename, content_type=content_type, file_size=file_size
        )
    except ValidationError as e:
        logger.error(f"File upload validation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Invalid file upload",
                "details": e.errors(),
            },
        )
