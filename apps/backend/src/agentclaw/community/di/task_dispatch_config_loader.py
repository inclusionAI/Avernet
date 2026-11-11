"""YAML-to-TODO adapter for the task_dispatch typed configuration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from agentclaw.community.di import config as cfg


def load_task_dispatch_config(
    *,
    task_dispatch_block: dict[str, Any],
    bcs_client_block: dict[str, Any],
    coerce: Callable,
    as_bool: Callable,
) -> cfg.TaskDispatchConfig:
    """Build task-dispatch runtime configuration from two YAML blocks.

    ``ConfigModule`` remains the boundary that reads sofa configuration and
    supplies the fallback/typing helpers. This loader owns only the typed
    assembly of task-dispatch fields, keeping ``config_module.py`` below the
    repository's one-thousand-line module limit.
    """
    defaults = cfg.TaskDispatchConfig()
    backend_url = coerce(
        task_dispatch_block,
        "backend_url",
        str,
        "",
        "task_dispatch",
    ).strip()
    if not backend_url:
        callback_url = coerce(
            bcs_client_block,
            "task_callback_url",
            str,
            "",
            "bcs_client",
        ).strip()
        parsed = urlparse(callback_url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            backend_url = f"{parsed.scheme}://{parsed.netloc}"
    timeout = coerce(
        task_dispatch_block,
        "todo_collab_timeout_seconds",
        float,
        defaults.todo_collab_timeout_seconds,
        "task_dispatch",
    )
    if timeout <= 0:
        raise ValueError("task_dispatch.todo_collab_timeout_seconds must be positive")
    return cfg.TaskDispatchConfig(
        backend_url=backend_url or defaults.backend_url,
        task_search_skill_enabled=coerce(
            task_dispatch_block,
            "task_search_skill_enabled",
            as_bool,
            defaults.task_search_skill_enabled,
            "task_dispatch",
        ),
        skill_report_enabled=coerce(
            task_dispatch_block,
            "skill_report_enabled",
            as_bool,
            defaults.skill_report_enabled,
            "task_dispatch",
        ),
        todo_collab_base_url=coerce(
            task_dispatch_block,
            "todo_collab_base_url",
            str,
            defaults.todo_collab_base_url,
            "task_dispatch",
        ).strip(),
        todo_collab_timeout_seconds=timeout,
    )
