"""_DefaultConfigPortMixin — get_default_config port method."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("openclaw-port")


class _DefaultConfigPortMixin:
    """Domain mixin: default config read (local-infra, no gateway/pool/token)."""

    # Constants relocated from engines/openclaw/default_config.py
    _DEFAULT_CONFIG_PATH = (
        "/home/admin/agentclaw-daas-scripts/confs/openclaw/openclaw.json"
    )
    _CONFIG_PATH_ENV = "OPENCLAW_DEFAULT_CONFIG_PATH"
    _MODEL_CONFIG_SOURCE_ENV = "OPENCLAW_MODEL_CONFIG_SOURCE"

    def _resolve_config_path(self) -> Path:
        """Resolve the default-config file path from env or fallback default."""
        return Path(
            os.getenv(
                self._CONFIG_PATH_ENV,
                os.getenv(self._MODEL_CONFIG_SOURCE_ENV, self._DEFAULT_CONFIG_PATH),
            )
        ).expanduser()

    async def get_default_config(self) -> dict[str, Any]:
        """Read and parse the OpenClaw default config JSON file.

        Returns a primitive dict with keys ``path`` (str) and ``config`` (dict).
        Raises ``FileNotFoundError``, ``IsADirectoryError``, or ``ValueError``
        on failure — preserved intact from
        ``engines/openclaw/default_config.py:OpenClawDefaultConfigService.get_default_config``.
        """
        path = self._resolve_config_path()
        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在: {path}")
        if not path.is_file():
            raise IsADirectoryError(f"配置路径不是文件: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"配置文件 JSON 格式错误: {e}") from e

        if not isinstance(payload, dict):
            raise ValueError(
                f"配置文件 JSON 顶层必须为 object: {type(payload).__name__}"
            )
        return {"path": str(path), "config": payload}
