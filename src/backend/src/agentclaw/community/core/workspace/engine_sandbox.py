from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ReadOnlyRule:
    """只读规则。

    path 默认使用相对引擎根路径；如确有需要，也允许绝对路径。
    """
    path: str
    rule_type: str  # "file" | "directory" | "glob"


@dataclass(frozen=True)
class DirectoryItem:
    """目录树条目，path 为相对引擎根路径。"""
    name: str
    path: str
    is_dir: bool


@dataclass(frozen=True)
class EngineBuildPlan:
    """引擎实例迁移构建约定。"""

    engine_type: str
    source_root_name: str
    migration_subpath: str
    workspace_subdir: str
    mcp_config_relpath: str
    skill_source_relpath: str
    skill_target_relpath: str
    rsync_excludes: list[str]
    # 额外同步目录：source_dir 父目录下的 extra_sync_source_relpath
    # → target_dir 下的 extra_sync_target_relpath。
    # 例如 claude_code 引擎需要把会话目录 .claude 同步到 target/claude。
    # 两者同时为空时跳过额外同步。
    extra_sync_source_relpath: str = ""
    extra_sync_target_relpath: str = ""


@runtime_checkable
class EngineSandboxProvider(Protocol):
    @property
    def engine_type(self) -> str:
        ...

    def get_base_path(self) -> str:
        """Absolute root path for this engine's bot files on the host."""
        ...

    def get_sessions_dir(self) -> str:
        ...

    def get_default_read_only_rules(self) -> list[ReadOnlyRule]:
        ...

    def get_build_plan(
        self,
        build_rsync_excludes_append: list[str] | None = None,
    ) -> EngineBuildPlan:
        """Return build plan with optional Bot-level build_rsync_excludes append.

        Args:
            build_rsync_excludes_append: Bot-level excludes from ac_bots.ext.
                可选参数，为 None 时使用模块级默认值。
                **合并语义**：与默认值合并（去重），而非完全覆盖。
        """
        ...

    async def list_directory(
        self,
        sub_path: str = "",
        recursive: bool = False,
        *,
        sandbox_id: str | None = None,
    ) -> list[DirectoryItem]:
        """List a sub-tree under ``get_base_path()``.

        Dispatch:
          * ``sandbox_id`` provided → query the remote sandbox via arca.
          * Otherwise → walk the local filesystem at ``get_base_path()``.

        The provider does not branch on runtime mode; the caller chooses
        whether to pass a sandbox_id based on the bot's device binding.
        """
        ...


class EngineSandboxRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, EngineSandboxProvider] = {}

    def register(self, provider: EngineSandboxProvider) -> None:
        self._providers[provider.engine_type] = provider

    def resolve(self, engine_type: str) -> EngineSandboxProvider:
        provider = self._providers.get(engine_type)
        if provider is not None:
            return provider
        raise ValueError(f"No EngineSandboxProvider registered for engine: {engine_type}")
