"""Public DRM Configuration Provider for open-source bcsfuse.

This provider provides DRM-like configuration for open-source deployments using
environment variables and YAML configuration. It does NOT depend on Layotto or
internal DRM infrastructure.

Configuration Environment Variables:
- BCSFUSE_CAPABILITY_VERIFY_ENABLED: Enable capability verification (default: true)
- BCSFUSE_GENERIC_CHECK_ENABLED: Enable generic check for intros (default: true)
- BCSFUSE_PEER_MIN_SIMILARITY: Peer review similarity threshold 0.0-1.0 (default: 0.85)
- BCSFUSE_RECOMMEND_MIN_SCORE: Minimum recommendation score (default: 0.6)
- BCSFUSE_JUDGE_PROMPT_TEMPLATE: Judge prompt template (default: None)
- BCSFUSE_PROFILE_PROMPT_TEMPLATE: Profile generation prompt template (default: None)
- BCSFUSE_TRUST_LEVEL_THRESHOLDS: JSON trust thresholds (default: {"trusted": 0.8, "guarded": 0.6})

This provider is safe for open-source use and does not require any internal infrastructure.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class DrmConfigProvider:
    """Public DRM configuration provider for open-source bcsfuse.

    This provider uses environment variables and YAML configuration to provide
    DRM-like settings without requiring Layotto or internal DRM infrastructure.

    Features:
    - Environment variable configuration
    - YAML configuration fallback
    - Static public defaults
    - No internal dependencies
    - No secrets printing
    - Open-source safe
    """

    # Default values for open-source deployments
    DEFAULTS = {
        "enable_capability_verify": True,
        "enable_generic_check": True,
        "peer_min_similarity": 0.85,
        "recommend_min_score": 0.6,
        "trust_level_thresholds": {"trusted": 0.8, "guarded": 0.6},
        "judge_prompt_template": None,
        "profile_prompt_template": None,
    }

    # Environment variable mapping
    ENV_VARS = {
        "enable_capability_verify": "BCSFUSE_CAPABILITY_VERIFY_ENABLED",
        "enable_generic_check": "BCSFUSE_GENERIC_CHECK_ENABLED",
        "peer_min_similarity": "BCSFUSE_PEER_MIN_SIMILARITY",
        "recommend_min_score": "BCSFUSE_RECOMMEND_MIN_SCORE",
        "judge_prompt_template": "BCSFUSE_JUDGE_PROMPT_TEMPLATE",
        "profile_prompt_template": "BCSFUSE_PROFILE_PROMPT_TEMPLATE",
        "trust_level_thresholds": "BCSFUSE_TRUST_LEVEL_THRESHOLDS",
    }

    def __init__(self, config_provider: Optional[Any] = None):
        """Initialize public DRM config provider.

        Args:
            config_provider: Optional YamlEnvConfigProvider instance for YAML config fallback.
                            If None, only environment variables and defaults are used.
        """
        self._config_provider = config_provider
        self._cache: Dict[str, Any] = {}

        logger.info("[PublicDrmConfig] Provider initialized (open-source, env-based)")

    def _get_bool(self, key: str) -> Optional[bool]:
        """Get boolean configuration value.

        Priority: env var -> config provider -> default

        Args:
            key: Configuration key

        Returns:
            Boolean value or None
        """
        # Check cache first
        if key in self._cache:
            return self._cache[key]

        env_var = self.ENV_VARS.get(key)
        if not env_var:
            return self.DEFAULTS.get(key)

        # Try environment variable
        env_value = os.getenv(env_var)
        if env_value is not None:
            value = env_value.lower() in ("true", "1", "yes", "on")
            self._cache[key] = value
            return value

        # Try config provider
        if self._config_provider:
            config_value = self._config_provider.get(f"drm.{key}")
            if config_value is not None:
                if isinstance(config_value, bool):
                    self._cache[key] = config_value
                    return config_value
                elif isinstance(config_value, str):
                    value = config_value.lower() in ("true", "1", "yes", "on")
                    self._cache[key] = value
                    return value

        # Use default
        default_value = self.DEFAULTS.get(key)
        self._cache[key] = default_value
        return default_value

    def _get_float(self, key: str) -> Optional[float]:
        """Get float configuration value.

        Priority: env var -> config provider -> default

        Args:
            key: Configuration key

        Returns:
            Float value or None
        """
        # Check cache first
        if key in self._cache:
            return self._cache[key]

        env_var = self.ENV_VARS.get(key)
        if not env_var:
            return self.DEFAULTS.get(key)

        # Try environment variable
        env_value = os.getenv(env_var)
        if env_value is not None:
            try:
                value = float(env_value)
                self._cache[key] = value
                return value
            except ValueError:
                logger.warning("[PublicDrmConfig] Invalid float value for %s: %s", key, env_value)

        # Try config provider
        if self._config_provider:
            config_value = self._config_provider.get(f"drm.{key}")
            if config_value is not None:
                try:
                    if isinstance(config_value, (int, float)):
                        self._cache[key] = float(config_value)
                        return float(config_value)
                    elif isinstance(config_value, str):
                        value = float(config_value)
                        self._cache[key] = value
                        return value
                except ValueError:
                    logger.warning("[PublicDrmConfig] Invalid float config for %s: %s", key, config_value)

        # Use default
        default_value = self.DEFAULTS.get(key)
        self._cache[key] = default_value
        return default_value

    def _get_string(self, key: str) -> Optional[str]:
        """Get string configuration value.

        Priority: env var -> config provider -> default

        Args:
            key: Configuration key

        Returns:
            String value or None
        """
        # Check cache first
        if key in self._cache:
            return self._cache[key]

        env_var = self.ENV_VARS.get(key)
        if not env_var:
            return self.DEFAULTS.get(key)

        # Try environment variable
        env_value = os.getenv(env_var)
        if env_value is not None:
            self._cache[key] = env_value
            return env_value

        # Try config provider
        if self._config_provider:
            config_value = self._config_provider.get(f"drm.{key}")
            if config_value is not None:
                self._cache[key] = config_value
                return config_value

        # Use default
        default_value = self.DEFAULTS.get(key)
        self._cache[key] = default_value
        return default_value

    def _get_json(self, key: str) -> Optional[Dict[str, Any]]:
        """Get JSON configuration value.

        Priority: env var -> config provider -> default

        Args:
            key: Configuration key

        Returns:
            Dict value or None
        """
        # Check cache first
        if key in self._cache:
            return self._cache[key]

        env_var = self.ENV_VARS.get(key)
        if not env_var:
            return self.DEFAULTS.get(key)

        # Try environment variable
        env_value = os.getenv(env_var)
        if env_value is not None:
            try:
                value = json.loads(env_value)
                if isinstance(value, dict):
                    self._cache[key] = value
                    return value
                else:
                    logger.warning("[PublicDrmConfig] Invalid JSON for %s: not a dict", key)
            except json.JSONDecodeError:
                logger.warning("[PublicDrmConfig] Invalid JSON for %s: %s", key, env_value)

        # Try config provider
        if self._config_provider:
            config_value = self._config_provider.get(f"drm.{key}")
            if config_value is not None:
                if isinstance(config_value, dict):
                    self._cache[key] = config_value
                    return config_value
                elif isinstance(config_value, str):
                    try:
                        value = json.loads(config_value)
                        if isinstance(value, dict):
                            self._cache[key] = value
                            return value
                    except json.JSONDecodeError:
                        logger.warning("[PublicDrmConfig] Invalid JSON config for %s: %s", key, config_value)

        # Use default
        default_value = self.DEFAULTS.get(key)
        self._cache[key] = default_value
        return default_value

    # Public API compatible with internal DrmConfigProvider

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key.

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        if key in ["enable_capability_verify", "enable_generic_check"]:
            return self._get_bool(key)
        elif key in ["peer_min_similarity", "recommend_min_score"]:
            return self._get_float(key)
        elif key in ["judge_prompt_template", "profile_prompt_template"]:
            return self._get_string(key)
        elif key == "trust_level_thresholds":
            return self._get_json(key)
        else:
            logger.warning("[PublicDrmConfig] Unknown configuration key: %s", key)
            return default

    def get_profile_prompt_template(self) -> Optional[str]:
        """Get profile prompt template.

        Returns:
            Profile prompt template string, or None if not configured
        """
        return self._get_string("profile_prompt_template")

    def is_capability_verify_enabled(self) -> Optional[bool]:
        """Check if capability verification is enabled.

        Returns:
            True if enabled, False if disabled, None if not configured
        """
        return self._get_bool("enable_capability_verify")

    def is_generic_check_enabled(self) -> Optional[bool]:
        """Check if generic check is enabled for intros.

        Returns:
            True if enabled (skip full interview for standard bots),
            False if disabled (all bots go through full interview),
            None if not configured
        """
        return self._get_bool("enable_generic_check")

    def get_peer_min_similarity(self) -> Optional[float]:
        """Get peer review similarity threshold.

        Returns:
            Similarity threshold (0.0-1.0), or None if not configured
        """
        return self._get_float("peer_min_similarity")

    def get_judge_prompt_template(self) -> Optional[str]:
        """Get judge prompt template.

        Returns:
            Judge prompt template string, or None if not configured
        """
        return self._get_string("judge_prompt_template")

    def get_trust_level_thresholds(self) -> Optional[Dict[str, float]]:
        """Get trust level thresholds.

        Returns:
            Dict with "trusted" and "guarded" thresholds, or None if not configured
        """
        return self._get_json("trust_level_thresholds")

    def get_recommend_min_score(self) -> Optional[float]:
        """Get recommendation minimum score.

        Returns:
            Minimum score threshold, or None if not configured
        """
        score = self._get_float("recommend_min_score")
        return score if score and score > 0 else None

    def health_check(self) -> bool:
        """Check if DRM config provider is healthy.

        Returns:
            Always True for public config provider (no external dependencies)
        """
        return True

    def __repr__(self) -> str:
        """String representation (no secrets printed)."""
        return "DrmConfigProvider(mode=public, source=env+config)"