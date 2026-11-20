"""Deployment configuration for the task_experience LLM provider.

The matching provider lives in
``di/modules/task_experience_config_module.py``; ``BbsLLMConfig`` /
``BbsConfigModule`` from ``di/bbs_digest_config.py`` /
``di/modules/bbs_config_module.py`` is the structural precedent — mirror
that exact ``dataclass + module`` split so the two features share the
resolve-then-disable / resolve-then-attach pattern.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["TaskExperienceLLMConfig"]


@dataclass(frozen=True)
class TaskExperienceLLMConfig:
    """OpenAI-compatible model settings for task_experience's LLM calls.

    Defaults keep the provider disabled:
      * ``base_url = ""`` ⇒ ``OpenAICompatibleTaskExperienceLLM._resolve_token``
        short-circuits to ``""`` and ``chat()`` returns the
        ``TASK_EXPERIENCE_LLM_DISABLED`` sentinel without HTTP.
      * ``secret_name = ""`` ⇒ ``_resolve_token`` returns ``""`` immediately,
        even if a non-empty base_url is later supplied by a profile.

    A deploy profile that opts in via
    ``task_experience.llm.base_url`` must also set ``secret_name`` and
    ``model``; ``TaskExperienceConfigModule.task_experience_llm_config``
    rejects a half-configured LLM block with a ValueError (mirrors the
    ``BbsLLMConfig`` ``if base_url and not secret_name / not model``
    guard).
    """

    base_url: str = ""
    secret_name: str = ""
    model: str = ""
    timeout_ms: int = 180_000
