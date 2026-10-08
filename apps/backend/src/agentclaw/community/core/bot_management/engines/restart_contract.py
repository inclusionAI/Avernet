"""Core-internal collaborators supplied to engine-owned restart execution."""

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class RestartServices:
    repository: Any
    task_queue: Any
    get_bot: Callable[[str, str], dict]
    template_service: Any
    lifecycle: Any
