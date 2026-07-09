"""OSS to NAS Migration Service — OSS 文件向 NAS 文件系统迁移服务.

此模块负责将 bot 数据从 OSS 存储迁移到 NAS 文件系统。
通过 rsync 命令实现文件同步。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Literal, TYPE_CHECKING

from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    # Type-only: runtime ``from agentclaw.community.di import config`` would form a
    # cycle (di/__init__ -> container -> devices_module ->
    # oss_to_nas_migration_service). This service is @provider-constructed
    # (devices_module), so the annotation is never resolved at runtime.
    from agentclaw.community.di import config as cfg


logger = get_logger()

# NAS 挂载根目录（部署环境注入 NAS_ROOT；社区构建默认中性路径，不含公司标识）。
# 实际运行时使用的是注入的 OssToNasConfig.nas_root，这里只是兜底默认值。
DEFAULT_NAS_ROOT = os.getenv("NAS_ROOT", "/home/admin/.bot_shared_nas")
DEFAULT_AIDESKTOP_ROOT = "/aidesktop"


class OssToNasMigrationService:
    """OSS 文件向 NAS 文件系统迁移服务。

    将指定 entity 和 bot 对应的数据从 OSS 存储路径
    通过 rsync 同步到 NAS 文件系统路径。
    """

    def __init__(self, oss_to_nas_config: cfg.OssToNasConfig) -> None:
        """初始化迁移服务。

        Args:
            oss_to_nas_config: 注入的 OssToNasConfig，提供 oss_root / nas_root。
        """
        self._oss_root = oss_to_nas_config.oss_root
        self._nas_root = oss_to_nas_config.nas_root

    def oss_path_to_nas_path(self, oss_file_path: Path) -> Path | None:
        """将 OSS 路径转换为对应的 NAS 路径。

        OSS 路径格式: {oss_root}/aidesktop_{env}/bolt_data/{entity_type}_{entity_id}/{bot_id}/{engine_type}/...
        NAS 路径格式: {nas_root}/{env}/{env}_{entity_type}_{entity_id}_{engine_type}_{bot_id}/{engine_type}/...

        Returns:
            对应的 NAS 路径，如果无法解析则返回 None
        """
        try:
            parts = oss_file_path.parts
            try:
                bolt_idx = parts.index("bolt_data")
            except ValueError:
                return None

            aidesktop_dir = parts[bolt_idx - 1]  # e.g. "aidesktop_pre"
            if not aidesktop_dir.startswith("aidesktop_"):
                return None
            env = aidesktop_dir[len("aidesktop_"):]
            if env not in ("prod", "pre"):
                return None

            entity_dir = parts[bolt_idx + 1]   # e.g. "staff_100013"
            bot_id = parts[bolt_idx + 2]       # e.g. "20260401_xxx"
            engine_type = parts[bolt_idx + 3]  # e.g. "openclaw"

            nas_bot_dir = f"{env}_{entity_dir}_{engine_type}_{bot_id}"
            remaining = parts[bolt_idx + 4:]
            base = Path(self._nas_root) / env / nas_bot_dir / engine_type
            return base / Path(*remaining) if remaining else base
        except (IndexError, Exception) as e:
            logger.debug(f"[oss_path_to_nas_path] Failed to convert {oss_file_path}: {e}")
            return None

    @staticmethod
    def _resolve_env_dir(env: str) -> str:
        """根据环境确定 OSS 侧的目录名。"""
        if env == "prod":
            return "aidesktop_prod"
        elif env == "pre":
            return "aidesktop_pre"
        return "aidesktop_dev"

    def _get_oss_path(
        self, env: str, entity_type: str, entity_id: str, bot_type: str, bot_id: str
    ) -> Path:
        """构造 OSS 端 bot 数据路径。

        格式: {oss_root}/{env_dir}/bolt_data/{entity_type}_{entity_id}/{bot_id}/{bot_type}/
        """
        env_dir = self._resolve_env_dir(env)
        return (
            Path(self._oss_root)
            / env_dir
            / "bolt_data"
            / f"{entity_type}_{entity_id}"
            / bot_id
            / bot_type
        )

    def _get_nas_path(
        self, env: str, entity_type: str, entity_id: str, bot_type: str, bot_id: str
    ) -> Path:
        """构造 NAS 端目标路径。

        格式: {nas_root}/prod/{env}_{entity_type}_{entity_id}_{bot_type}_{bot_id}
        """
        assert env in ('prod', 'pre'), 'env must be prod or pre'
        return (
            Path(self._nas_root)
            / "prod"
            / f"{env}_{entity_type}_{entity_id}_{bot_type}_{bot_id}"
        )

    def migrate(
        self,
        env: str,
        entity_type: str,
        entity_id: str,
        bot_type: str,
        bot_id: str,
        direction: Literal["oss_to_nas", "nas_to_oss"] = "oss_to_nas",
    ) -> bool:
        """在 OSS 与 NAS 之间同步指定 bot 的数据。

        通过 rsync -av --delete 同步整个 bot 数据目录。

        Args:
            env: 环境标识 (pre/prod)
            entity_type: 实体类型 (如 staff)
            entity_id: 实体 ID
            bot_type: Bot 类型 (如 openclaw)
            bot_id: Bot ID
            direction: 同步方向，"oss_to_nas"（默认）或 "nas_to_oss"

        Returns:
            True 迁移成功，False 迁移失败
        """
        oss_path = self._get_oss_path(env, entity_type, entity_id, bot_type, bot_id)
        nas_path = self._get_nas_path(env, entity_type, entity_id, bot_type, bot_id)
        # NAS 侧存储在 .openclaw 子目录下
        nas_openclaw_path = nas_path / ".openclaw"

        if direction == "oss_to_nas":
            src_path = oss_path
            dst_path = nas_openclaw_path
            src_label, dst_label = "OSS", "NAS"
        else:
            src_path = nas_openclaw_path
            dst_path = oss_path
            src_label, dst_label = "NAS", "OSS"

        logger.info(
            f"[OssToNasMigration] 收到迁移请求: env={env}, entity_type={entity_type}, "
            f"entity_id={entity_id}, bot_type={bot_type}, bot_id={bot_id}, "
            f"direction={direction}"
        )
        logger.info(f"[OssToNasMigration] 源路径: {src_path}, 目标路径: {dst_path}")

        if not src_path.exists():
            logger.error(f"[OssToNasMigration] {src_label} 源路径不存在: {src_path}")
            return False

        # 确保目标目录存在
        try:
            dst_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"[OssToNasMigration] 创建 {dst_label} 目标目录失败: {dst_path}, error={e}")
            return False

        # rsync 行为说明:
        # - "rsync -av src/ dst/" 同步源目录内容到目标目录内容（两边子目录结构一致）
        # - "rsync -av src dst" 会把 src 目录本身放到 dst 下面
        # 源和目标都带 / 结尾，确保同步的是目录内容而非目录本身
        src = str(src_path).rstrip("/") + "/"
        dst = str(dst_path).rstrip("/") + "/"

        try:
            cmd = ["rsync", "-av", "--delete", src, dst]
            logger.info(f"[OssToNasMigration] 开始迁移: {src} -> {dst}")
            logger.info(f"[OssToNasMigration] 执行命令: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode != 0:
                logger.error(
                    f"[OssToNasMigration] rsync 命令执行失败: returncode={result.returncode}, "
                    f"stderr={result.stderr}"
                )
                return False

            logger.info(
                f"[OssToNasMigration] 迁移完成: {src} -> {dst}, "
                f"rsync output:\n{result.stdout}"
            )
            return True
        except subprocess.TimeoutExpired:
            logger.error(f"[OssToNasMigration] rsync 命令超时: {src} -> {dst}")
            return False
        except Exception as e:
            logger.error(f"[OssToNasMigration] 迁移异常: {e}")
            return False
