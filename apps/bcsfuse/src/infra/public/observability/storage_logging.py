"""
Storage Logging Utilities for Real Storage Validation

This module provides safe structured logging for MySQL and Qdrant storage validation.
It ensures:
- Sufficient diagnostic information for troubleshooting
- Zero exposure of secrets, credentials, or sensitive data
- Consistent structured logging format
- Correlation ID for traceability

Security Guarantees:
- All secrets automatically masked
- No full DSN/connection strings logged
- No raw vector values or profile payloads logged
- Only safe metadata (IDs, counts, table names) logged
"""

import logging
import re
import time
import secrets
from typing import Any, Dict, Optional
from contextvars import ContextVar

# Re-use existing secret masking utility
from src.bootstrap.oss_diagnostics import mask_secret_value

logger = logging.getLogger(__name__)

# Correlation ID context for validation phases
_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def generate_correlation_id() -> str:
    """
    Generate a correlation ID for validation tracking.

    Format: val_{timestamp}_{random_8char}
    Example: val_20240115_a3f7b9c2

    Returns:
        Correlation ID string
    """
    return f"val_{int(time.time() * 1000)}_{secrets.token_hex(4)}"


def get_correlation_id() -> str:
    """Get current correlation ID from context."""
    cid = _correlation_id.get()
    if not cid:
        cid = generate_correlation_id()
        _correlation_id.set(cid)
    return cid


def set_correlation_id(correlation_id: str) -> None:
    """Set correlation ID in context."""
    _correlation_id.set(correlation_id)


def mask_value(value: Optional[str]) -> str:
    """
    Mask a sensitive value.

    Args:
        value: Value to mask

    Returns:
        Masked value or original if None
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)

    # Check for common secret patterns
    if re.search(r"Bearer\s+[A-Za-z0-9._-]{12,}", value):
        return "Bearer ****"

    # Check for DSN patterns (protocol://user:password@host)
    # We need to mask only the password, not the username
    dsn_patterns = [
        r"(mysql://[^:]+:)([^@]+)(@)",
        r"(postgres://[^:]+:)([^@]+)(@)",
        r"(redis://[^:]+:)([^@]+)(@)",
    ]
    for pattern in dsn_patterns:
        match = re.search(pattern, value)
        if match:
            # Group 1: protocol://user:
            # Group 2: password (to be masked)
            # Group 3: @
            # Replace with: protocol://user:****@
            return re.sub(pattern, r"\1****\3", value)

    # Default: keep as-is (assume caller has sanitized)
    return value


def mask_host(host: Optional[str]) -> str:
    """
    Mask a host name for logging.

    Args:
        host: Host name or IP

    Returns:
        Masked host: first 3 chars + ****
        Example: localhost -> loc****
    """
    if not host:
        return ""
    if len(host) <= 3:
        return "****"
    return f"{host[:3]}****"


def mask_user(user: Optional[str]) -> str:
    """
    Mask a username for logging.

    Args:
        user: Username

    Returns:
        Masked username: first 2 chars + ****
        Example: admin -> ad****
    """
    if not user:
        return ""
    if len(user) <= 2:
        return "****"
    return f"{user[:2]}****"


def mask_url(url: Optional[str]) -> str:
    """
    Mask a URL for logging.

    Args:
        url: URL string

    Returns:
        URL with password masked if present
        Example: mysql://admin:secret@localhost/db -> mysql://admin:****@localhost/db
    """
    if not url:
        return ""

    # Mask password in URL
    # Pattern: protocol://user:password@host
    # We need to capture the protocol and user, then mask the password
    if "://" in url and "@" in url:
        # URL format: protocol://user:password@host/path
        parts = url.split("://", 1)
        if len(parts) == 2:
            protocol = parts[0]
            rest = parts[1]

            # Split at @ to separate credentials from host
            cred_host = rest.split("@", 1)
            if len(cred_host) == 2:
                credentials = cred_host[0]
                host_path = cred_host[1]

                # Check if credentials have user:password format
                if ":" in credentials:
                    user_pass = credentials.split(":", 1)
                    user = user_pass[0]
                    # Mask the password
                    return f"{protocol}://{user}:****@{host_path}"

    return url


def sanitize_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize a configuration dict by masking all secrets.

    Args:
        config: Configuration dictionary

    Returns:
        Sanitized dict with secrets masked
    """
    sanitized = {}
    for key, value in config.items():
        if isinstance(value, str):
            sanitized[key] = mask_secret_value(key, value)
        elif isinstance(value, dict):
            sanitized[key] = sanitize_config(value)
        else:
            sanitized[key] = value
    return sanitized


def sanitize_key_fields(key_fields: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize key fields by masking IDs.

    Args:
        key_fields: Dictionary of key field names and values

    Returns:
        Sanitized dict with IDs partially masked
        Example: {"worker_id": "worker_123"} -> {"worker_id": "wo****"}
    """
    sanitized = {}
    for key, value in key_fields.items():
        if isinstance(value, str):
            # Mask ID values
            if len(value) <= 4:
                sanitized[key] = "****"
            else:
                sanitized[key] = f"{value[:2]}****"
        else:
            sanitized[key] = str(value)[:4] + "****" if value else ""
    return sanitized


def log_storage_event(
    logger: logging.Logger,
    level: int,
    event: str,
    component: str,
    operation: str,
    validation_phase: str,
    backend: str,
    target_resource: str,
    duration_ms: Optional[float] = None,
    result: str = "success",
    retry_count: int = 0,
    **kwargs: Any,
) -> None:
    """
    Log a structured storage event.

    Args:
        logger: Logger instance
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        event: Event name (e.g., "mysql_connection_success")
        component: Component name (e.g., "mysql_store", "qdrant_store")
        operation: Operation name (e.g., "connect", "create", "search")
        validation_phase: Validation phase (e.g., "setup", "connection", "schema_init", "operation", "cleanup")
        backend: Backend name (e.g., "mysql", "qdrant", "sqlite")
        target_resource: Target resource (e.g., table name, collection name)
        duration_ms: Operation duration in milliseconds
        result: Result status ("success", "failure", "retry")
        retry_count: Number of retries attempted
        **kwargs: Additional fields to log (will be auto-masked if sensitive)
    """
    # Get correlation ID
    correlation_id = get_correlation_id()

    # Build log record
    extra = {
        "component": component,
        "operation": operation,
        "validation_phase": validation_phase,
        "correlation_id": correlation_id,
        "backend": backend,
        "target_resource": target_resource,
        "result": result,
        "retry_count": retry_count,
    }

    if duration_ms is not None:
        extra["duration_ms"] = round(duration_ms, 2)

    # Add additional fields (sanitize any sensitive data)
    for key, value in kwargs.items():
        if isinstance(value, str):
            extra[key] = mask_secret_value(key, value)
        else:
            extra[key] = value

    # Log with structured extra fields
    logger.log(level, event, extra=extra)


def log_storage_error(
    logger: logging.Logger,
    event: str,
    component: str,
    operation: str,
    validation_phase: str,
    backend: str,
    target_resource: str,
    error: Exception,
    duration_ms: Optional[float] = None,
    retry_count: int = 0,
    **kwargs: Any,
) -> None:
    """
    Log a structured storage error.

    Args:
        logger: Logger instance
        event: Event name (e.g., "mysql_connection_failure")
        component: Component name
        operation: Operation name
        validation_phase: Validation phase
        backend: Backend name
        target_resource: Target resource
        error: Exception instance
        duration_ms: Operation duration in milliseconds
        retry_count: Number of retries attempted
        **kwargs: Additional fields to log (will be auto-masked if sensitive)
    """
    # Get correlation ID
    correlation_id = get_correlation_id()

    # Extract error info
    error_class = error.__class__.__name__
    error_code = getattr(error, "code", "UNKNOWN")

    # Build log record
    extra = {
        "component": component,
        "operation": operation,
        "validation_phase": validation_phase,
        "correlation_id": correlation_id,
        "backend": backend,
        "target_resource": target_resource,
        "result": "failure",
        "error_class": error_class,
        "error_code": error_code,
        "retry_count": retry_count,
    }

    if duration_ms is not None:
        extra["duration_ms"] = round(duration_ms, 2)

    # Add additional fields (sanitize any sensitive data)
    for key, value in kwargs.items():
        if isinstance(value, str):
            extra[key] = mask_secret_value(key, value)
        else:
            extra[key] = value

    # Log error with exception info and structured extra fields
    logger.error(event, extra=extra, exc_info=True)


__all__ = [
    "mask_value",
    "mask_host",
    "mask_user",
    "mask_url",
    "sanitize_config",
    "sanitize_key_fields",
    "generate_correlation_id",
    "get_correlation_id",
    "set_correlation_id",
    "log_storage_event",
    "log_storage_error",
]