"""Engine-owned artifact transfer extension used by build and draft restore.

Unsupported plans and confirmed non-fastdisk storage retain the existing NAS
path. A transfer error must propagate: it must never select the NAS fallback.
"""
from pathlib import Path
from typing import Any, Callable, Protocol

from agentclaw.community.core.workspace.engine_sandbox import EngineBuildPlan


class ArtifactTransferStrategy(Protocol):
    def supports(self, plan: EngineBuildPlan) -> bool:
        """Pure engine-scope check; must not execute commands or access storage."""
        ...

    def try_publish(self, *, device_id: str, plan: EngineBuildPlan,
                    source_root: str, target_dir: Path, ignore_paths: tuple[str, ...],
                    normalize_extra: Any) -> bool:
        """True after completed upload; False only when not applicable."""
        ...

    def try_restore(self, *, device_id: str, plan: EngineBuildPlan,
                    source_root: str, artifact_path: str, artifact_dir: Path,
                    remaining_timeout: Callable[[], float], normalize_extra: Any) -> bool:
        """True after completed restore; False only when not applicable."""
        ...
