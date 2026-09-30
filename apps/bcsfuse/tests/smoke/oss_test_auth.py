"""
OSS Test Auth Helper

Provides unified auth token management for OSS smoke/regression tests.

IMPORTANT:
- Only use dummy tokens for testing
- Never print tokens
- Never write tokens to reports
- Never use real secrets
"""
import os
from typing import Dict

# Dummy test tokens - NEVER use real tokens
TEST_TOKEN = "test-token"
DEV_SMOKE_TOKEN = "dev-smoke-token"


def set_test_auth_env(token: str = TEST_TOKEN) -> None:
    """
    Set the test auth token in environment.

    Args:
        token: Dummy token to use (default: test-token)

    NOTE: This modifies global environment state.
    Remember to clean up with clear_test_auth_env() in finally blocks.
    """
    os.environ["BCSFUSE_AUTH_TOKEN"] = token


def clear_test_auth_env() -> None:
    """
    Clear the test auth token from environment.

    Always call this in finally blocks to avoid state leakage between tests.
    """
    os.environ.pop("BCSFUSE_AUTH_TOKEN", None)


def auth_headers(token: str = TEST_TOKEN) -> Dict[str, str]:
    """
    Get Authorization headers for protected endpoints.

    Args:
        token: Dummy token to use (default: test-token)

    Returns:
        Dict with Authorization header

    Example:
        >>> response = client.get("/v1/workers", headers=auth_headers())
    """
    return {"Authorization": f"Bearer {token}"}


def dev_smoke_auth_headers() -> Dict[str, str]:
    """
    Get Authorization headers for dev_smoke mode tests.

    Returns:
        Dict with Authorization header using dev-smoke-token
    """
    return auth_headers(DEV_SMOKE_TOKEN)


# Public endpoints that do NOT require authentication
PUBLIC_ENDPOINTS = [
    "/health",
    "/ready",
    "/openapi.json",
    "/docs",
    "/redoc",
]

# Protected endpoints that REQUIRE authentication
PROTECTED_ENDPOINTS = [
    "/providers",
    "/v1/providers/status",
    "/v1/workers",
    "/v1/workers/{worker_id}",
    "/v1/workers/{worker_id}/online",
    "/v1/workers/{worker_id}/offline",
    "/v1/workers/{worker_id}/profiles",
    "/v1/workers/{worker_id}/profiles/{profile_id}",
    "/v1/workers/{worker_id}/profiles/{profile_id}/activate",
    "/v1/search",
    "/v1/search/stats",
]


def is_public_endpoint(path: str) -> bool:
    """
    Check if an endpoint is public (no auth required).

    Args:
        path: Endpoint path

    Returns:
        True if public endpoint, False otherwise
    """
    # Remove trailing slashes and query params
    clean_path = path.split("?")[0].rstrip("/")
    return any(clean_path == public.rstrip("/") for public in PUBLIC_ENDPOINTS)


def is_protected_endpoint(path: str) -> bool:
    """
    Check if an endpoint is protected (auth required).

    Args:
        path: Endpoint path

    Returns:
        True if protected endpoint, False otherwise
    """
    # Remove trailing slashes and query params
    clean_path = path.split("?")[0].rstrip("/")

    # Handle path parameters
    for protected in PROTECTED_ENDPOINTS:
        protected_clean = protected.rstrip("/")
        # Simple pattern matching for path parameters
        if "{worker_id}" in protected_clean:
            pattern = protected_clean.replace("{worker_id}", r"[^/]+")
            import re
            if re.match(f"^{pattern}$", clean_path):
                return True
        elif "{profile_id}" in protected_clean:
            pattern = protected_clean.replace("{worker_id}", r"[^/]+").replace("{profile_id}", r"[^/]+")
            import re
            if re.match(f"^{pattern}$", clean_path):
                return True
        elif clean_path == protected_clean:
            return True

    return False