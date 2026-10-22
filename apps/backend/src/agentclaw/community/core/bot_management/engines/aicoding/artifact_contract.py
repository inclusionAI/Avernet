"""Engine-owned transfer hooks; the caller retains legacy NAS fallback.

Publish returns False / restore returns None only when not applicable. Errors
propagate without fallback. Implementations must reject unsupported engines
before reading runtime paths, checking devices or consuming timeout budgets.
"""
from pathlib import Path
from typing import Any, Callable, Protocol

from agentclaw.community.core.workspace.engine_sandbox import (
    EngineBuildPlan, EngineSandboxProvider,
)


class ArtifactTransferStrategy(Protocol):
    def publish(self, *, device_id: str, plan: EngineBuildPlan,
                provider: EngineSandboxProvider, target_dir: Path,
                ignore_paths: tuple[str, ...], include_runtime_artifacts: bool = False) -> bool:
        """True only after transfer; reuse the caller-resolved CLI rollout decision.

        False (the safe default) omits CLI snapshots using legacy NAS semantics.
        It does not disable engine artifacts or affect historical restore.
        """
        ...

    def restore(self, *, bot: dict[str, Any], source_version: int,
                plan: EngineBuildPlan, provider: EngineSandboxProvider,
                artifact_dir: Path, migration_path_base: Callable[..., str],
                remaining_timeout: Callable[[], float],
                migration_error: type[Exception]) -> dict[str, str] | None:
        """Return the existing restore result, or None to use legacy NAS.

        migration_path_base is lazy: an unsupported engine must not resolve it.
        """
        ...
