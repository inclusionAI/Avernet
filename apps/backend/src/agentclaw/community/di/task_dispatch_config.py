"""Typed deployment configuration for task dispatch runtime wiring."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskDispatchConfig:
    """Values loaded from the deployment's ``task_dispatch`` YAML block."""

    backend_url: str = ""
    task_search_skill_enabled: bool = False
    skill_report_enabled: bool = True
