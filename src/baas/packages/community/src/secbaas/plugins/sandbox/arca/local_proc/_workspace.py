"""本地进程沙箱的 workspace 目录解析与初始化。"""

from __future__ import annotations

import os
from pathlib import Path

from secbaas.logger import get_logger

logger = get_logger("plugin-sandbox-arca-local-proc")


def resolve_workspace_dir(metadata: dict[str, str], bot_id: str) -> Path:
    """Resolve workspace directory.

    Priority:
      1. metadata["workspace_dir"]  — caller 显式指定（兼容旧路径）
      2. $LOCAL_WORKSPACE_DIR/{bot_id}  — 环境变量
      3. /aidesktop/{env}/bolt_data/{entity_dir}/{bot_id}/{engine}/workspace
         — 与 Backend LocalDeviceService._setup_directory 对齐的默认路径
      4. /tmp/{bot_id}  — 最终 fallback

    Args:
        metadata: 来自 BaaS create_bot 流程的元数据字典。
        bot_id: Bot 标识符。

    Returns:
        workspace 目录的 Path。
    """
    logger.info("Resolving workspace dir with metadata: %s", metadata)

    # 1. caller 显式指定
    if metadata and metadata.get("workspace_dir"):
        return Path(metadata["workspace_dir"])

    # 2. 环境变量
    env_dir = os.environ.get("LOCAL_WORKSPACE_DIR")
    if env_dir:
        return Path(env_dir) / bot_id

    # 3. 对齐 Backend LocalDeviceService._setup_directory 的路径结构
    #    ${LOCAL_AIDESKTOP_ROOT}/aidesktop/{env_folder}/bolt_data/{entity_type}_{entity_id}/{bot_id}/{engine}/workspace
    aidesktop_root = os.environ.get(
        "LOCAL_AIDESKTOP_ROOT", os.environ.get("HOME") + "/aidesktop"
    )

    # Get from metadata (no need to check)
    entity_id = metadata.get("entity_id")
    entity_type = metadata.get("entity_type")
    engine = metadata.get("engine")

    # WORKSPACE_ENV_FOLDER 显式覆盖 (singlebox 模式由 app.sh 注入
    # "aidesktop_singlebox",让 baas 和 backend 路径对齐); 否则按 SERVER_ENV 映射。
    env_folder = os.environ.get("WORKSPACE_ENV_FOLDER")
    if not env_folder:
        env = os.environ.get("SERVER_ENV", "dev")
        env_folder = {
            "prod": "aidesktop_prod",
            "pre": "aidesktop_pre",
        }.get(env, "aidesktop_dev")
    entity_dir = f"{entity_type}_{entity_id}"

    workspace_dir = (
        Path(aidesktop_root)
        / env_folder
        / "bolt_data"
        / entity_dir
        / bot_id
        / engine
        / "workspace"
    )
    return workspace_dir


def setup_workspace_dirs(workspace_dir: Path, engine: str) -> None:
    """Create the bot's workspace directory tree.

    Migrated from LocalDeviceService._setup_directory() in the Backend.
    Creates the workspace directory and, for openclaw/hermes engines,
    the skills subdirectory structure.

    Args:
        workspace_dir: workspace 根目录。
        engine: 引擎类型（openclaw / hermes / aicoding 等）。
    """
    workspace_dir.mkdir(parents=True, exist_ok=True)

    if engine in ("openclaw", "hermes"):
        skills_dir = workspace_dir / "skills"
        (skills_dir / "skills-local").mkdir(parents=True, exist_ok=True)
        (skills_dir / "skills-repo").mkdir(parents=True, exist_ok=True)
        (skills_dir / "active").mkdir(parents=True, exist_ok=True)
