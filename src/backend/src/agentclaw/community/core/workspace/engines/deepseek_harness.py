"""DeepSeek Harness sandbox filesystem adapter."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from agentclaw.community.core.workspace.engine_sandbox import (
    DirectoryItem,
    EngineBuildPlan,
    ReadOnlyRule,
)
from agentclaw.community.di import config as cfg
from agentclaw.community.log import get_logger

logger = get_logger()


class DeepSeekHarnessSandboxProvider:
    """Describe the DSH workspace and session layout used in an ARCA sandbox."""

    def __init__(self, workspace: cfg.WorkspaceConfig) -> None:
        self._workspace = workspace

    @property
    def engine_type(self) -> str:
        return "deepseek_harness"

    def get_base_path(self) -> str:
        # 以下为安全注释COSEC：资源与只读目录接口仅暴露 workspace，禁止读取同级凭证文件。
        return f"{self._workspace.deepseek_harness_root}/workspace"

    def get_sessions_dir(self) -> str:
        return f"{self._workspace.deepseek_harness_root}/sessions"

    def get_default_read_only_rules(self) -> list[ReadOnlyRule]:
        return []

    def get_build_plan(
        self,
        build_rsync_excludes_append: list[str] | None = None,
        bot: dict[str, Any] | None = None,
    ) -> EngineBuildPlan:
        del build_rsync_excludes_append, bot
        raise ValueError("DeepSeek Harness service bot builds are not supported yet")

    @staticmethod
    def _normalize_sub_path(sub_path: str) -> str:
        if not sub_path:
            return ""
        if "\x00" in sub_path:
            raise ValueError(f"Invalid sub_path: {sub_path!r}")
        path = PurePosixPath(sub_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Invalid sub_path: {sub_path}")
        normalized = str(path)
        return "" if normalized == "." else normalized

    async def list_directory(
        self,
        sub_path: str = "",
        recursive: bool = False,
        *,
        device_fs=None,
    ) -> list[DirectoryItem]:
        sub_path = self._normalize_sub_path(sub_path)
        items: list[DirectoryItem] = []
        base_path = self.get_base_path()

        if device_fs is not None:

            async def walk(current_sub_path: str) -> None:
                target = (
                    f"{base_path}/{current_sub_path}" if current_sub_path else base_path
                )
                for item in await device_fs.list_dir(target, recursive=False) or ():
                    name = item.get("name", "")
                    if not name:
                        continue
                    relative_path = (
                        f"{current_sub_path}/{name}" if current_sub_path else name
                    )
                    is_dir = item.get("is_dir", False)
                    items.append(
                        DirectoryItem(
                            name=name,
                            path=relative_path,
                            is_dir=is_dir,
                        )
                    )
                    if recursive and is_dir:
                        await walk(relative_path)

            await walk(sub_path)
            return items

        root = Path(base_path)
        target = root / sub_path if sub_path else root
        if target.exists() and target.is_dir():
            entries = target.rglob("*") if recursive else target.iterdir()
            for entry in entries:
                items.append(
                    DirectoryItem(
                        name=entry.name,
                        path=str(entry.relative_to(root)),
                        is_dir=entry.is_dir(),
                    )
                )
        else:
            logger.info(
                "[DeepSeekHarnessSandboxProvider.list_directory] local root %s "
                "is absent",
                root,
            )
        return items


__all__ = ["DeepSeekHarnessSandboxProvider"]
