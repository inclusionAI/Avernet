"""Deployment configuration for backend-owned BBS background tasks.

The configuration lives outside ``di/config.py`` because that shared module is
at the repository's 1,000-line limit. The matching providers live in
``di/modules/bbs_config_module.py``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BbsTaskConfig:
    """Policy for one one-instance periodic BBS task.

    All instances may start the same loop. They compete for that task's named
    lease; only the token holder executes a tick. The heartbeat must be shorter
    than the lock TTL so long model calls do not cross an expiry boundary
    without a renewal attempt.
    """

    enabled: bool = False
    interval: int = 60
    jitter: int = 5
    lock_ttl: int = 240
    heartbeat: int = 45


@dataclass(frozen=True)
class BbsDigestTaskConfig(BbsTaskConfig):
    """Settings for the BBS digest task, protected by ``bbs:summary``."""


@dataclass(frozen=True)
class BbsContentImportTaskConfig(BbsTaskConfig):
    """Settings for the import task, protected by ``bbs:content_import``."""


@dataclass(frozen=True)
class BbsLLMConfig:
    """BBS-specific OpenAI-compatible model settings.

    This configuration is deliberately separate from the Harness health-check
    LLM configuration and secret. Reusing the health-check token would silently
    consume another feature's model quota, so BBS must opt into its own endpoint
    and secret name before live calls are enabled.
    """

    base_url: str = ""
    secret_name: str = "BBS_LLM_AUTH_TOKEN"
    model: str = "glm-5.2"
    timeout_ms: int = 180_000


__all__ = [
    "BbsContentImportTaskConfig",
    "BbsDigestTaskConfig",
    "BbsLLMConfig",
    "BbsTaskConfig",
]
