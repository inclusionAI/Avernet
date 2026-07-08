"""Device Plugin Protocols — 设备服务所需的外部依赖接口.

根据 README.md 分层规范：
- core/ 层通过 Protocol 接口访问外部依赖，不直接 import 具体实现
- 具体实现通过 dependencies/ 注入

这些 Protocol 接口描述设备服务对 Bot 系统、配置系统、存储系统的依赖，
而非纯基础设施接口（DatabasePlugin 等），因此放在 core/devices/
而非 plugins/。
"""

from pathlib import Path
from typing import Any, Protocol


class BotQueryProtocol(Protocol):
    """Bot 查询接口 — 供设备服务检查 Bot 可见性和查询绑定关系."""

    def get_by_binding_id(self, binding_id: int) -> dict[str, Any] | None:
        """根据 binding_id 获取 Bot 信息.

        Args:
            binding_id: 设备绑定 ID

        Returns:
            Bot 信息字典，包含 public, bot_id, owner_id, owner_name, ext 等字段；
            如果未找到返回 None.
        """
        ...

    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict[str, Any] | None:
        """根据 bot_id + owner_id 获取 Bot 信息.

        Args:
            bot_id: Bot ID
            owner_id: Bot 所有者 ID

        Returns:
            Bot 信息字典；如果未找到返回 None.
        """
        ...

    def get_by_id(self, bot_id: str) -> dict[str, Any] | None:
        ...

    def list_active_bots_by_entity(
        self,
        entity_id: str,
        entity_type: str | None = None,
        bot_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """根据 entity_id 查询激活的 bot.

        Args:
            entity_id: 实体 ID（用户 ID）
            entity_type: 实体类型（可选）
            bot_type: bot 类型过滤（可选，如 "personal" 或 "service"）

        Returns:
            激活的 bot 列表，每项包含 bot_id, binding_id, owner_id 等字段
        """
        ...


class BotSyncProtocol(Protocol):
    """Bot 配置同步接口 — 供设备服务在设备激活时同步 Bot 配置."""

    def sync_bot_config_to_device(
        self,
        *,
        bot_id: str,
        user_id: str,
        public: str,
        permission_owner: str,
    ) -> dict[str, Any]:
        """同步 Bot 配置到设备.

        Task 2.1: 入参精简 — binding 由 resolver 内部查,死参 nick_name 去掉。

        Args:
            bot_id: Bot ID
            user_id: 用户 ID
            public: Bot 是否公开
            permission_owner: 权限所有者

        Returns:
            同步结果字典，包含 success 等字段
        """
        ...


class StoragePathProtocol(Protocol):
    """存储路径解析接口 — 供 Arca 设备服务解析 OSS 路径."""

    def get_bolt_data_path(self, *, entity_type: str, entity_id: str, bot_id: str) -> str:
        """获取 Bot 数据目录的 OSS 路径."""
        ...

    def get_skills_repo_path(self) -> str:
        """获取 Skills 仓库的 OSS 路径."""
        ...

    def get_default_skills_repo_path(self) -> str:
        """获取默认 Skills 仓库的 OSS 路径."""
        ...

    def get_skills_center_path(self) -> str:
        """获取 SkillCenter 产物的 OSS 路径."""
        ...


class ProcessManagerProtocol(Protocol):
    """进程管理器接口 — 供 LocalDeviceService 管理本地进程对的生命周期."""

    def allocate_ports(self, engine: str = "openclaw") -> tuple[int, int]:
        """分配一对 (adapter_port, engine_port).

        Args:
            engine: 引擎类型 ("openclaw", "hermes", 或 "aicoding")

        Returns:
            Tuple of (adapter_port, engine_port):
            - openclaw: OpenClaw Gateway port (18800-18899)
            - hermes: Hermes Dashboard port (18700-18799)
            - aicoding: 0 (外部 relay)
        """
        ...

    def create_openclaw_config(
        self,
        *,
        bolt_id: str,
        openclaw_port: int,
        workspace_dir: Path,
        entity_id: str = "",
    ) -> Path:
        """为指定 bot 创建 openclaw 配置目录."""
        ...

    def create_hermes_config(
        self,
        *,
        bolt_id: str,
        hermes_port: int,
        workspace_dir: Path,
    ) -> Path:
        """为指定 bot 创建 Hermes 配置目录."""
        ...

    def start(
        self,
        *,
        device_id: str,
        bot_id: str,
        adapter_port: int,
        engine_port: int,
        config_dir: Path,
        workspace_dir: Path,
        callback_token: str = "",
        entity_id: str = "",
        symbol_json: str | None = None,
        agent_code: str | None = None,
        engine: str = "openclaw",
    ) -> Any:
        """启动 adapter + 引擎 进程对.

        Args:
            device_id: 设备 ID
            bot_id: Bot ID
            adapter_port: Adapter 端口
            engine_port: 引擎端口（openclaw/hermes）
            config_dir: 配置目录
            workspace_dir: 工作空间目录
            callback_token: 回调 token
            entity_id: 实体 ID
            symbol_json: 技能符号链接映射 JSON
            agent_code: Agent 代码
            engine: 引擎类型 ("openclaw", "hermes", "aicoding")
        """
        ...

    def stop(self, device_id: str) -> bool:
        """停止指定设备的进程对."""
        ...

    def stop_all(self) -> None:
        """停止所有进程对."""
        ...

    def is_healthy(self, device_id: str) -> bool:
        """检查指定设备的进程是否健康."""
        ...


class McpSyncProtocol(Protocol):
    """MCP 配置同步接口 — 供设备在激活时同步 MCP 配置到设备."""

    # ------------------------------------------------------------------
    # 配置层面 —— 推送/移除 MCP 详细配置到设备
    # ------------------------------------------------------------------

    async def sync_mcp_details(
        self,
        *,
        user_id: str,
        entity_id: str,
        bot_id: str,
        entity_type: str,
        engine_type: str | None = None,
        active_only: bool = False,
    ) -> dict[str, Any]:
        """推送 MCP 完整配置到设备.

        Args:
            active_only: 为 True 时只推送当前激活 skill sets 中的 MCP；
                为 False 时推送该 bot 关联的全部 MCP（含 inactive）。

        Returns:
            同步结果字典，包含 success、success_count、failed_count 等字段.
        """
        ...

    async def sync_mcp_detail(
        self,
        *,
        user_id: str,
        mcp_data: dict[str, Any],
        bot_id: str,
        engine_type: str | None = None,
    ) -> dict[str, Any]:
        """推送单个 MCP 配置到指定 bot 的设备.

        Returns:
            ``{"success": bool, "error": str|None}``.
        """
        ...

    async def remove_mcp_detail(
        self,
        *,
        server_code: str,
        bot_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """从 bot 设备上移除单个 MCP.

        Returns:
            ``{"success": bool, "error": str|None}``.
        """
        ...

    # ------------------------------------------------------------------
    # 权限层面 —— 刷新白名单与许可证
    # ------------------------------------------------------------------

    async def refresh_mcp_scope(
        self,
        *,
        user_id: str,
        entity_id: str,
        bot_id: str,
        entity_type: str,
        engine_type: str | None = None,
    ) -> dict[str, Any]:
        """刷新 MCP 授权范围.

        同时向设备声明 filter-servers 白名单，并更新 passport 的 MCP codes 列表.

        Returns:
            ``{"success": bool, "error": str|None}``.
        """
        ...
