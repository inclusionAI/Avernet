# Copyright (c) 2004-2026, Ant Group.
# All Rights Reserved.

"""Backward-compatible re-export of workspace root helpers.

Canonical helpers live in ``engine.community.plugin_api.workspace_root`` so concrete
plugins can depend on them without importing ``engine.community.core``.
"""
from engine.community.plugin_api.workspace_root import workspace_root, workspace_root_strict

__all__ = ["workspace_root", "workspace_root_strict"]
