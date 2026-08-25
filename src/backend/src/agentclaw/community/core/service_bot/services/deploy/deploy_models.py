"""Value types that make up the BaaS ``deploy_config`` payload.

They live here rather than in ``baas_service`` because both sides of the
deploy-composition seam need to construct them: ``BaasService`` assembles the
payload, and each :class:`~...deploy_config_composer.DeployConfigComposer`
produces the mounts and storage that go into it. Homing them in the service
would make every composer import the service that imports the composer.

``baas_service`` re-exports both names, so the ~15 call sites that import them
from there keep working.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class Storage:
    """
    Sandbox storage.
    """

    type: str
    """
    Storage type.
    """
    path: str
    """
    Storage path.
    """
    storage_id: str
    """
    Storage id.
    """
    quota: str
    """
    Storage quota, such as "1Gi".
    """
    permission: str

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式。"""
        return {
            "type": self.type,
            "path": self.path,
            "storage_id": self.storage_id,
            "quota": self.quota,
            "permission": self.permission,
        }


@dataclass
class MountPointEntry:
    """OSS mount point configuration for DeployConfig.

        Platform-agnostic representation, converted to Arca MountPoint when needed.
        Keeps domain model independent of Arca SDK per D-01.
        Field names match Arca MountPoint for clarity (id, remote_dir, local_dir, permission).
        """
    remote_dir: str
    local_dir: str
    permission: str

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式。"""
        return {
            "remote_dir": self.remote_dir,
            "local_dir": self.local_dir,
            "permission": self.permission,
        }
