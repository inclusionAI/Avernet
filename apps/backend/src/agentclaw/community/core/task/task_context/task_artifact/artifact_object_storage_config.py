"""``TaskArtifactStorageConfig`` — the task module's own object-store config.

task 模块自有的 ``user_config.task_artifact_storage`` 块类型(spec 2026-10-20
§13)。定义在 core(task 模块自己的地盘)而非 ``di/``:di 是组合根,import 方向是
di → core;core 服务在运行期类型引用此 dataclass,若它住在 ``di`` 包下会形成
core → di 的逆行边,并在 api 模块冷导入时成环(di/__init__ 引导组合根 → adapters
→ 路由 → api)。di 侧的读块 provider 是
``di/modules/task_artifact_config_module.py``(独立小模块,不进 1000 行帽沿零
余量的 ``di/config.py`` / ``di/modules/config_module.py``)。

``backend`` selects the column's implementation: community supports ``fs``
only, corp (consumer repo) supports ``oss`` — each column's provider fails
fast on anything else. Corp-only fields stay neutral placeholders:
``secret_name`` is the ``SecretResolver`` key (resolved by the deployment's
own credential resolver in the consumer repo), never the credential itself.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskArtifactStorageConfig:
    """``user_config.task_artifact_storage`` block."""

    backend: str = "fs"
    fs_root: str = "./data/task-artifact-objects"
    key_prefix: str = "task-artifacts"
    max_upload_mb: int = 64
    endpoint: str = ""
    bucket_name: str = ""
    secret_name: str = ""


__all__ = ["TaskArtifactStorageConfig"]