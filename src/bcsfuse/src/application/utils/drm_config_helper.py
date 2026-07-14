"""DRM Config Helper for open-source bcsfuse.

This module provides helper functions to access DRM-like configuration
without directly importing internal DRM infrastructure.

This module uses the public DrmConfigProvider which reads configuration
from environment variables and YAML config files.
"""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.infra.public.config.drm_config_provider import DrmConfigProvider

logger = logging.getLogger(__name__)

# Cached DRM config instance
_drm_config_cache: Optional["DrmConfigProvider"] = None


def get_drm_config() -> "DrmConfigProvider":
    """Get DRM config provider.

    This function returns a DrmConfigProvider instance that reads configuration
    from environment variables and YAML config files.

    Environment Variables (with defaults):
    - BCSFUSE_CAPABILITY_VERIFY_ENABLED: Enable capability verification (default: true)
    - BCSFUSE_GENERIC_CHECK_ENABLED: Enable generic check for intros (default: true)
    - BCSFUSE_PEER_MIN_SIMILARITY: Peer review similarity threshold 0.0-1.0 (default: 0.85)
    - BCSFUSE_RECOMMEND_MIN_SCORE: Minimum recommendation score (default: 0.6)
    - BCSFUSE_JUDGE_PROMPT_TEMPLATE: Judge prompt template (default: None)
    - BCSFUSE_PROFILE_PROMPT_TEMPLATE: Profile generation prompt template (default: None)
    - BCSFUSE_TRUST_LEVEL_THRESHOLDS: JSON trust thresholds (default: {"trusted": 0.8, "guarded": 0.6})

    Returns:
        DrmConfigProvider instance (cached after first call)
    """
    global _drm_config_cache

    if _drm_config_cache is not None:
        return _drm_config_cache

    try:
        from src.infra.public.config.drm_config_provider import DrmConfigProvider
        _drm_config_cache = DrmConfigProvider()
        logger.info("[DRM Config] Using public DRM config provider (env-based)")
        return _drm_config_cache
    except Exception as e:
        logger.error("[DRM Config] Failed to create DRM config provider: %s", e, exc_info=True)
        raise


# Backward-compatible function aliases
# These functions provide the same API as src.infrastructure.drm.drm_resource


def get_judge_prompt_template() -> Optional[str]:
    """Get judge prompt template from DRM config.

    Returns:
        Judge prompt template string, or None if not configured
    """
    return get_drm_config().get_judge_prompt_template()


def get_peer_min_similarity() -> Optional[float]:
    """Get peer review similarity threshold from DRM config.

    Returns:
        Similarity threshold (0.0-1.0), or None if not configured
    """
    return get_drm_config().get_peer_min_similarity()


def is_capability_verify_enabled() -> Optional[bool]:
    """Check if capability verification is enabled from DRM config.

    Returns:
        True if enabled, False if disabled, None if not configured
    """
    return get_drm_config().is_capability_verify_enabled()


def is_generic_check_enabled() -> Optional[bool]:
    """Check if generic check is enabled for intros from DRM config.

    Returns:
        True if enabled, False if disabled, None if not configured
    """
    return get_drm_config().is_generic_check_enabled()


def get_recommend_min_score() -> Optional[float]:
    """Get recommendation minimum score from DRM config.

    Returns:
        Minimum score threshold, or None if not configured
    """
    return get_drm_config().get_recommend_min_score()


def get_trust_level_thresholds() -> Optional[dict]:
    """Get trust level thresholds from DRM config.

    Returns:
        Dict with "trusted" and "guarded" thresholds, or None if not configured
    """
    return get_drm_config().get_trust_level_thresholds()


# Module-level profile_prompt_template for backward compatibility
# This is evaluated at import time
try:
    _drm_config_instance = get_drm_config()
    profile_prompt_template = _drm_config_instance.get_profile_prompt_template()
except Exception:
    profile_prompt_template = None