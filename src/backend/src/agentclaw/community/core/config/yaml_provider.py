"""Community-safe YAML configuration provider (B2).

Reads ``configs/application.yaml`` plus a ``SERVER_ENV``-selected overlay
(``application-singlebox.yaml`` / ``application-dev.yaml``) and deep-merges them
into an :class:`~agentclaw.community.core.config.provider.AppConfig`. This is the default
provider (community / test / local) and the body of the loader the local-mode
monkeypatch used to carry inline.

Imports nothing internal beyond the neutral :class:`AppConfig` type — no
``sofapy_base``, no plugins — so a community checkout can read its configuration
with no company packages installed.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from agentclaw.community.core.config.provider import AppConfig

logger = logging.getLogger(__name__)


def _select_overlay_name() -> str:
    """选择要叠加到 application.yaml 之上的 overlay 文件名。

    优先看部署 profile，再看 SERVER_ENV：
    - DEPLOY_PROFILE=community → application-community.yaml
      （开源部署：community 专属配置，如 OIDC；不含任何公司内网字段）
    - DEPLOY_PROFILE=test → application-test.yaml
      （社区 CI：中性测试 overlay，自包含，不依赖 corp 的 application-dev.yaml）
    - SERVER_ENV=singlebox → application-singlebox.yaml（完全无外网）
    - SERVER_ENV=dev / 空 / 其他 → application-dev.yaml（连内网）

    community 用 DEPLOY_PROFILE 而非 SERVER_ENV 选 overlay，是因为 community 是
    一个独立的部署形态（OIDC 鉴权、社区基础设施），与 corp 的 SERVER_ENV 环境轴正交。
    """
    profile = (os.getenv("DEPLOY_PROFILE") or "").lower()
    if profile == "community":
        return "application-community.yaml"
    env = (os.getenv("SERVER_ENV") or os.getenv("REAL_SERVER_ENV") or "").lower()
    if profile in ("test", "corp_test") and env == "":
        # The community suite runs DEPLOY_PROFILE=test and the corp suite runs
        # DEPLOY_PROFILE=corp_test, both with SERVER_ENV unset. Both read the same
        # neutral, community-shipped overlay (B11) rather than riding the corp
        # application-dev.yaml, so community CI is self-contained (no corp overlay). An
        # explicitly set SERVER_ENV still wins (a test can force singlebox/dev).
        return "application-test.yaml"
    return (
        "application-singlebox.yaml" if env == "singlebox" else "application-dev.yaml"
    )


def _load_yaml_configs() -> dict[str, Any]:
    """读取配置 yaml：``application.yaml`` 基座 + 由 :func:`_select_overlay_name`
    选定的 overlay，深度合并。

    所有 profile 统一走「中性基座 + overlay」：

    - ``DEPLOY_PROFILE=community``：``application.yaml``（已中性化，不含任何 corp
      端点/密钥）+ ``application-community.yaml``（社区 overlay）。基座对每个 corp
      专属块（token_exchange / arca_sandbox / dima / …）都不设值，社区 overlay 提供
      自己需要的中性配置，因此合并后仍是纯中性配置。
    - corp / test / singlebox：``application.yaml`` + 由 SERVER_ENV 选定的 overlay
      （``application-{prod,dev,singlebox,…}.yaml``），与 sofapy_base 在 prod 用
      SERVER_ENV 选 yaml 的逻辑一致。corp 专属值由各 env overlay 承载。
    """
    overlay_name = _select_overlay_name()

    # B11: configs live in the community subtree (agentclaw/community/configs). In a
    # deploy the assembled runtime `configs/` (cwd) holds them; in the monorepo they
    # resolve from the subtree. Community never searches corp/configs — the test
    # suite reads only community overlays, and corp prod reads config via sofapy.
    config_dirs = [
        Path.cwd() / "configs",
        Path(__file__).resolve().parents[2] / "configs",  # agentclaw/community/configs
    ]

    base_config: dict[str, Any] = {}
    overlay_config: dict[str, Any] = {}

    for config_dir in config_dirs:
        base_path = config_dir / "application.yaml"
        overlay_path = config_dir / overlay_name
        if base_path.exists():
            with open(base_path, "r", encoding="utf-8") as f:
                base_config = yaml.safe_load(f) or {}
            if overlay_path.exists():
                with open(overlay_path, "r", encoding="utf-8") as f:
                    overlay_config = yaml.safe_load(f) or {}
                logger.info("YamlConfigProvider loaded overlay: %s", overlay_path)
            else:
                logger.warning(
                    "YamlConfigProvider overlay %s not found, using base only",
                    overlay_name,
                )
            break

    return _deep_merge(base_config, overlay_config)


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并两个 dict，override 覆盖 base。"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class YamlConfigProvider:
    """Loads :class:`AppConfig` from local ``application*.yaml`` files."""

    def load(self) -> AppConfig:
        raw = _load_yaml_configs()
        return AppConfig(
            user_config=raw.get("user_config", {}),
            raw=raw,
            app_name=raw["app_name"],
            delegate=None,  # YAML path: no source object to forward to.
        )
