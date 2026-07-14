"""
OSS Diagnostics Helper

Provides safe diagnostic utilities that mask secret values.

Security:
- All token/password/secret values are masked
- No sensitive values appear in logs, reports, or diagnostics
- Base URLs and non-sensitive config can be shown
"""
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def mask_secret_value(key: str, value: Optional[str]) -> str:
    """
    Mask a value if the key name suggests it's a secret.

    Sensitive key patterns:
        - token, auth, key, secret, password (case-insensitive)
        - *_token, *_auth, *_key, *_secret, *_password

    Examples:
        mask_secret_value("EMBEDDING_AUTH_TOKEN", "abc123") → "***MASKED***"
        mask_secret_value("base_url", "http://example.com") → "http://example.com"
        mask_secret_value("PASSWORD", "mypass") → "***MASKED***"

    Args:
        key: Configuration key name
        value: Configuration value (may be None)

    Returns:
        Masked value if key is sensitive, otherwise original value
    """
    if value is None:
        return ""

    # Patterns that indicate sensitive keys
    sensitive_patterns = [
        r"token",
        r"auth",
        r"key",
        r"secret",
        r"password",
        r"credential",
        r"api_key",
    ]

    # Check if key matches any sensitive pattern (case-insensitive)
    key_lower = key.lower()
    for pattern in sensitive_patterns:
        if re.search(pattern, key_lower):
            return "***MASKED***"

    return str(value)


def safe_provider_diagnostics(context: Any) -> Dict[str, Any]:
    """
    Generate safe diagnostics for providers.

    This function extracts provider information and masks all sensitive values.

    Args:
        context: ApplicationContext or similar object with provider registry

    Returns:
        Dict with provider diagnostics, all sensitive values masked
    """
    diagnostics = {
        "providers": {},
        "config": {},
        "env_keys": [],
        "masked_env_values": {},
    }

    try:
        # Get registry
        registry = getattr(context, "registry", None) or getattr(context, "_registry", None)
        if not registry:
            diagnostics["error"] = "No registry found in context"
            return diagnostics

        # Get providers
        provider_names = [
            "embedding_provider",
            "reranker_provider",
            "llm_provider",
            "vector_store",
            "worker_registry_store",
            "worker_runtime_state_store",
            "worker_profile_content_store",
            "audit_log_store",
            "cache_provider",
            "object_storage_provider",
            "auth",
            "config",
        ]

        for name in provider_names:
            try:
                provider = registry.get(name)
                if provider:
                    diagnostics["providers"][name] = {
                        "class": provider.__class__.__name__,
                        "module": provider.__class__.__module__,
                    }

                    # Extract non-sensitive provider attributes
                    if hasattr(provider, "_settings"):
                        settings = provider._settings
                        for attr in ["base_url", "model", "dimension", "timeout_ms"]:
                            if hasattr(settings, attr):
                                diagnostics["providers"][name][attr] = getattr(settings, attr)

                    # Extract auth provider info (without secrets)
                    if name == "auth":
                        if hasattr(provider, "token"):
                            diagnostics["providers"][name]["has_token"] = bool(provider.token)

                    # Extract vector store info
                    if name == "vector_store":
                        if hasattr(provider, "collection_name"):
                            diagnostics["providers"][name]["collection_name"] = provider.collection_name
                        if hasattr(provider, "dimension"):
                            diagnostics["providers"][name]["dimension"] = provider.dimension

            except Exception as e:
                diagnostics["providers"][name] = {"error": str(e)}

        # Get config provider
        config = registry.get("config")
        if config:
            try:
                # List all known config keys
                config_keys = [
                    "embedding.base_url",
                    "embedding.model",
                    "embedding.dimension",
                    "reranker.base_url",
                    "reranker.model",
                    "llm.base_url",
                    "llm.fast_model",
                    "llm.reasoning_model",
                    "vector.qdrant.collection",
                    "database.sqlite.path",
                ]

                for key in config_keys:
                    try:
                        value = config.get(key)
                        if value is not None:
                            # Mask if sensitive
                            masked_value = mask_secret_value(key, str(value))
                            diagnostics["config"][key] = masked_value
                    except Exception:
                        pass

            except Exception as e:
                diagnostics["config"]["error"] = str(e)

        # Get environment variable keys (not values!)
        env_keys = [
            "BCSFUSE_PROVIDER_MODE",
            "BCSFUSE_AUTH_TOKEN",
            "EMBEDDING_BASE_URL",
            "EMBEDDING_AUTH_TOKEN",
            "EMBEDDING_MODEL",
            "EMBEDDING_DIMENSION",
            "RERANKER_BASE_URL",
            "RERANKER_API_KEY",
            "RERANKER_MODEL",
            "LLM_BASE_URL",
            "LLM_AUTH_TOKEN",
            "LLM_ENABLED",
            "LLM_FAST_MODEL",
            "LLM_REASONING_MODEL",
            "MYSQL_HOST",
            "MYSQL_PORT",
            "MYSQL_DATABASE",
            "MYSQL_USER",
            "MYSQL_PASSWORD",
            "QDRANT_LOCAL_PATH",
            "QDRANT_COLLECTION_NAME",
        ]

        diagnostics["env_keys"] = env_keys

        # Show which env vars are set (without values)
        for key in env_keys:
            value = os.environ.get(key)
            if value is not None:
                masked_value = mask_secret_value(key, value)
                diagnostics["masked_env_values"][key] = masked_value

    except Exception as e:
        diagnostics["error"] = f"Failed to generate diagnostics: {str(e)}"
        logger.exception("Failed to generate provider diagnostics")

    return diagnostics


def safe_provider_status(context: Any) -> Dict[str, Any]:
    """
    Generate safe provider status summary.

    This is a simpler version of diagnostics for health checks.

    Args:
        context: ApplicationContext or similar object with provider registry

    Returns:
        Dict with provider status (no sensitive values)
    """
    status = {
        "mode": "unknown",
        "providers": {},
    }

    try:
        # Get mode
        if hasattr(context, "mode"):
            status["mode"] = context.mode

        # Get registry
        registry = getattr(context, "registry", None) or getattr(context, "_registry", None)
        if not registry:
            status["error"] = "No registry found"
            return status

        # Check critical providers
        providers_to_check = [
            "embedding_provider",
            "reranker_provider",
            "llm_provider",
            "vector_store",
            "worker_registry_store",
            "auth",
        ]

        for name in providers_to_check:
            try:
                provider = registry.get(name)
                if provider:
                    status["providers"][name] = {
                        "status": "available",
                        "class": provider.__class__.__name__,
                    }
                else:
                    status["providers"][name] = {"status": "not_configured"}
            except Exception as e:
                status["providers"][name] = {
                    "status": "error",
                    "error": str(e),
                }

    except Exception as e:
        status["error"] = f"Failed to generate status: {str(e)}"

    return status


def log_safe_config(key: str, value: Any) -> None:
    """
    Log a configuration value safely (masking secrets).

    Args:
        key: Configuration key name
        value: Configuration value
    """
    if isinstance(value, str):
        masked = mask_secret_value(key, value)
        logger.info("Config %s = %s", key, masked)
    else:
        logger.info("Config %s = %s", key, value)


def validate_no_secrets_in_dict(data: Dict[str, Any]) -> List[str]:
    """
    Validate that a dict doesn't contain unmasked secrets.

    This is useful for checking diagnostics or status responses
    before returning them to users.

    Args:
        data: Dict to check

    Returns:
        List of keys that may contain unmasked secrets (empty if all safe)
    """
    issues = []

    def check_value(path: str, value: Any) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                check_value(f"{path}.{k}", v)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                check_value(f"{path}[{i}]", v)
        elif isinstance(value, str):
            # Skip if already masked
            if "***MASKED***" in value:
                return

            # Check for common secret patterns
            if re.search(r"Bearer\s+[A-Za-z0-9._-]{12,}", value):
                issues.append(f"{path}: contains Bearer token")
            # Check for token-like values only in specific keys
            elif re.search(r"[A-Za-z0-9._-]{32,}", value):
                # Only flag if the path clearly indicates a secret field
                path_lower = path.lower()
                # Be more specific - only flag if it's an actual value field, not metadata
                if any(s in path_lower for s in ["token", "auth", "key", "secret", "password"]):
                    # Exclude module names, class names, paths, URLs
                    safe_patterns = ["module", "class", "path", "url", "name", "type"]
                    if not any(pattern in path_lower for pattern in safe_patterns):
                        issues.append(f"{path}: may contain unmasked secret")

    check_value("root", data)
    return issues


__all__ = [
    "mask_secret_value",
    "safe_provider_diagnostics",
    "safe_provider_status",
    "log_safe_config",
    "validate_no_secrets_in_dict",
]