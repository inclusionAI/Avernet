"""OpenClawSkillsPort — native port for bulk symlink-based skills management.

Skills operations are local-infra: they reconcile symlinks on the pod
filesystem (``$SKILLS_LINK_BASE_DIR``) and rsync from a NAS source — no
gateway, no pool, no token.  The port impl owns all FS/subprocess logic;
the adapter builds core DTOs from the primitive dicts returned here.

Decision 5 (leaf-safety): ``CapabilityNotSupportedError`` and ``Capability``
are core types; the port impl must never import them.  The 10 per-skill ops
(``list_skills``, ``get_skill``, ``install_skill``, ``uninstall_skill``,
``update_skill``, ``enable_skill``, ``disable_skill``, ``execute_skill``,
``validate_skill``, ``discover_skills``) are NOT on this port — the adapter
raises ``CapabilityNotSupportedError`` directly for each.
"""
from __future__ import annotations

from typing import Any, Literal, Protocol, TypedDict


class PoolLayoutActivationPortResult(TypedDict):
    """Plugin API 返回的版本化 Pool 激活结果。"""

    committed: bool
    status: Literal[
        "COMMITTED",
        "ALREADY_COMMITTED",
        "ACTIVE_ENTRY_CONFLICT",
        "DATA_INCONSISTENT",
        "INVALID",
        "TRANSIENT_ERROR",
        "POST_CUTOVER_SYNC_PENDING",
        "NOT_ATOMIC",
    ]
    evidence: dict[str, Any]


class OpenClawSkillsPort(Protocol):
    """Native port for OpenClaw bulk-symlink skills management."""

    async def ensure_center_skills(self, params: dict[str, Any]) -> dict[str, Any]:
        """Ensure each (skill_uuid, version) in ``params["items"]`` exists locally.

        ``params`` keys:
          ``items`` — list[dict] each with ``skill_uuid`` (str) and
          ``version`` (str).

        Returns a dict with keys:
          ``ok`` (list[dict] — items that succeeded),
          ``failed`` (list[dict] each with ``skill_uuid``, ``version``,
          ``reason``).

        Individual item failures do not abort the batch.  Items already
        present locally are returned in ``ok`` without IO.
        """
        ...

    async def sync_symlinks(self, params: dict[str, Any]) -> dict[str, Any]:
        """Reconcile relative-path symlinks under the base dir to match ``params``.

        ``params`` keys:
          ``symlinks`` — list[dict] each with ``source`` (str, relative) and
          ``target`` (str, relative).

        Returns a dict with keys:
          ``total`` (int), ``created`` (list[str]), ``updated`` (list[str]),
          ``kept`` (list[str]), ``removed`` (list[str]), ``base_dir`` (str).

        Raises ``ValueError`` for duplicate targets or path validation errors.
        Raises ``RuntimeError`` when a target is occupied by a non-symlink.
        """
        ...

    async def sync_bindpaths(self, params: dict[str, Any]) -> dict[str, Any]:
        """Reconcile absolute-path symlinks to match ``params``.

        ``params`` keys:
          ``symlinks`` — list[dict] each with ``source`` (str, absolute) and
          ``target`` (str, absolute);
          ``clean_target_dir`` (bool, default True) — when True, also removes
          stale symlinks in each unique parent of the desired targets.

        Returns a dict with keys:
          ``total`` (int), ``created`` (list[str]), ``updated`` (list[str]),
          ``kept`` (list[str]), ``removed`` (list[str]).

        Raises ``ValueError`` for duplicate targets or path validation errors.
        Raises ``RuntimeError`` when a target is occupied by a non-symlink.
        """
        ...

    async def clean_symlinks(self, params: dict[str, Any]) -> dict[str, Any]:
        """Remove every symlink under each directory in ``params["directories"]``.

        ``params`` keys:
          ``directories`` — list[str] of absolute directory paths to scan.

        Returns a dict with keys:
          ``directories_scanned`` (int), ``removed`` (list[str]).

        Raises ``ValueError`` when ``directories`` is empty or a path is
        invalid.
        """
        ...

    async def activate_pool_layout(
        self, params: dict[str, Any]
    ) -> PoolLayoutActivationPortResult:
        """核对登记事实、同步完整 local 并原子提交永久兼容 bridge。"""
        ...

    async def probe_pool_layout(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """核验当前 OpenClaw Pool layout。"""
        ...

    async def publish_pool_mappings(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """发布目标 Pool layout 的完整受管 mapping。"""
        ...

    async def verify_pool_mappings(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """验证受管入口精确解析到目标 Pool source。"""
        ...


__all__ = ["OpenClawSkillsPort", "PoolLayoutActivationPortResult"]
