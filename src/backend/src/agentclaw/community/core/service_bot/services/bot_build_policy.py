"""Publish-build resource policy — 单一构建码道，资源由部署声明。

发布构建不得靠探宿主机推断环境能力（那会把业务语义按环境分叉）。
部署经 ``publish_build`` user_config 块声明自己的资源，DI 解析成本类型
注入 ``BotBuildService``。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PublishBuildPolicyConfig:
    """Per-deployment publish-build resource declarations."""

    # NAS instance staging required: when True (default, prod semantics) a
    # missing NAS source fails the build loudly — a silent skip would hide
    # a broken NAS mount. Deployments without NAS (e.g. singlebox local
    # sandboxes) declare False and the migration is skipped by explicit
    # configuration, not host probing.
    nas_migration_required: bool = True
    # Remote MCP catalog required: when True (default) an empty mcporter
    # catalog from the device fails the build — it hides a broken mcporter
    # or dead device shell. Deployments where "no remote MCP tools" is a
    # legal fresh-device state declare False.
    mcp_catalog_required: bool = True


__all__ = ["PublishBuildPolicyConfig"]