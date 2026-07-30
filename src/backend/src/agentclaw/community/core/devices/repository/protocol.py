"""Device Binding Repository Protocol — 业务数据访问接口.

根据 README.md FAQ:
  Q: 业务 Repository 接口（如 SkillRepository）放哪里？
  A: 放在 core/<module>/ 内部，不放 plugin_api/。plugin_api/ 只放纯基础设施接口。

DeviceBindingRepository 包含设备绑定的 CRUD 操作和业务语义方法，
属于设备领域的业务抽象，因此放在 core/ 层内部。

纯基础设施接口（如 DatabasePlugin、CachePlugin）仍保留在 plugin_api/ 层。
"""

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.devices.repository.record import DeviceBindingRecord


@runtime_checkable
class DeviceBindingRepository(Protocol):
    """设备绑定仓库接口 Protocol.

    定义设备绑定记录的 CRUD 操作，支持设备生命周期管理。
    实现类必须提供完整的方法实现。
    """

    def insert_binding(
        self,
        *,
        entity_id: str,
        entity_type: str,
        device_id: str,
        device_provider: str,
        env: str,
        device_props: dict[str, Any],
        status: str,
        apply_reason: str | None,
        applied_by: str,
    ) -> int:
        """插入新的设备绑定记录.

        Args:
            entity_id: 实体ID（用户工号或团队ID）
            entity_type: 实体类型（staff/team/proj）
            device_id: 设备唯一标识
            device_provider: 设备提供商（local/daas/arca）
            env: 环境标识（dev/pre/prod）
            device_props: 设备属性字典
            status: 初始状态（PENDING/ACTIVE/FAILED/RELEASED）
            apply_reason: 申请原因
            applied_by: 申请人ID

        Returns:
            新记录的绑定ID
        """
        ...

    def get_by_id(self, binding_id: int) -> DeviceBindingRecord | None:
        """根据绑定ID获取设备绑定记录."""
        ...

    def get_active_by_bot_and_owner(
        self, bot_id: str, owner_id: str
    ) -> "DeviceBindingRecord | None":
        """返回 bot 当前 active binding。

        通过 ac_bots.binding_id 反查 ac_entity_device_binding (JOIN)。
        bot 不存在 / 无 active binding / owner 不匹配 → 返 None。
        DeviceContextResolver 入口：一次查询拿到 binding 完整对象
        （id / device_provider / device_props / status 等），
        替代旧的 ``get_device_provider_by_bot_id_and_owner``
        返 dict 缺 ``binding_id`` 的链路。
        """
        ...

    def get_by_device_id(self, device_id: str) -> DeviceBindingRecord | None:
        """根据设备ID获取最新的设备绑定记录."""
        ...

    def release_binding(
        self,
        *,
        binding_id: int,
        release_reason: str | None,
        released_by: str,
    ) -> None:
        """释放设备，标记绑定记录为 RELEASED 状态."""
        ...

    def update_status(self, *, binding_id: int, status: str) -> None:
        """更新设备绑定状态."""
        ...

    def update_status_and_alive_at(self, *, binding_id: int, status: str) -> None:
        """更新设备状态并刷新 alive 时间戳."""
        ...

    def update_device_props(self, *, binding_id: int, props: dict[str, Any]) -> None:
        """合并 ``props`` 到绑定的 device_props（保留其它键）。

        Caller 可以只传新 key（如 ``{"publish_id": ...}``），其它已有 key
        会被保留；亦支持 caller 自己做完整合并后整字典传入（幂等）。
        用于刷新 teclaw 绑定上的 baas ``publish_id`` 状态读取句柄，以及
        singlebox 回填 adapter_port 等 BaaS 解析到的值。绑定不存在时静默忽略。
        """
        ...

    def transition_teclaw_publish_terminal(
        self,
        *,
        binding_id: int,
        bot_id: str,
        owner_id: str,
        publish_id: int,
        status: str,
    ) -> bool:
        """Atomically persist a guarded Teclaw terminal transition.

        The implementation reloads and locks ``binding_id`` in one
        transaction. It returns ``False`` without writes unless the binding is
        still PENDING, owned by Teclaw, and references ``publish_id``. On a
        match it updates the expected live bot first, then the binding. A bot
        update that does not match exactly one row raises and rolls back the
        transaction.
        """
        ...

    def list_bindings(
        self,
        *,
        env: str,
        entity_id: str | None,
        entity_type: str | None,
        status: str | None,
        page: int,
        page_size: int,
    ) -> tuple[int, list[DeviceBindingRecord]]:
        """查询设备绑定记录列表（分页）.

        ``env`` 必传 —— ac_bots / ac_entity_device_binding 表在 pre / prod
        共享同一 DB，仅靠 env 字段区分；不传 env 会跨环境串数据。caller
        通常传 ``get_current_env()``；如需扫全环境（admin / 迁移工具），
        显式分别传 "pre" / "prod" 两次。
        """
        ...

    def count_non_released_bindings(
        self,
        *,
        entity_id: str,
        entity_type: str,
        env: str,
    ) -> int:
        """统计非释放状态的设备数量（用于配额检查）."""
        ...

    def exists_device_id(self, *, device_id: str) -> bool:
        """检查设备ID是否已存在."""
        ...

    def get_released_binding(self, *, device_id: str) -> DeviceBindingRecord | None:
        """获取已释放状态的设备绑定记录（用于重新申请时复用）."""
        ...

    def reuse_binding(
        self,
        *,
        binding_id: int,
        device_props: dict[str, Any],
        apply_reason: str | None,
        applied_by: str,
        status: str = "PENDING",
    ) -> None:
        """重用已释放的设备绑定记录."""
        ...

    def get_active_engine_by_device_id(self, *, device_id: str) -> str:
        """通过设备ID获取Bot的 active_engine."""
        ...

    def batch_update_env(self, *, binding_ids: list[int], env: str) -> int:
        """批量更新环境字段."""
        ...

    def get_by_ids(self, binding_ids: list[int]) -> list[DeviceBindingRecord]:
        """根据ID列表批量获取绑定记录."""
        ...

    def get_active_bindings_by_entity(
        self,
        *,
        entity_id: str,
        env: str,
    ) -> list[DeviceBindingRecord]:
        """按 entity_id 查询所有 ACTIVE 状态的绑定记录.

        与 ``get_active_by_bot_and_owner`` 不同，本方法**不通过**
        ``ac_bots.binding_id`` JOIN，而是直接查 ``ac_entity_device_binding``
        表中该实体下所有 status='ACTIVE' 的记录。

        用途：default 区评测沙箱的 binding 不被 ``ac_bots.binding_id``
        引用，只能通过实体 + device_props 标记来查找。

        不使用 entity_type 作为查询条件：entity_type 依赖上游 bot 记录
        的 fallback，当 bot 数据缺失时可能产生错误匹配（如 staff 误查
        team），去掉后由 entity_id + env + status 三字段精确隔离，
        Python 层按 device_props 中的 AGENTCLAW_DEFAULT_TAG 和 bot_id
        进一步过滤。

        Args:
            entity_id: 实体 ID（与 ac_bots.owner_id 一致）
            env: 环境标识（dev/pre/prod）

        Returns:
            该实体下所有 ACTIVE 绑定记录列表（可能为空）
        """
        ...

    def update_bot_start_status(self, *, binding_id: int, status: str, message: str | None) -> None:
        """更新 ac_bots 表 ext 字段中的启动状态."""
        ...

    def update_bot_status_on_device_active(self, *, binding_id: int) -> None:
        """设备变 ACTIVE 时更新关联 Bot 状态为 ACTIVE（仅当 Bot 当前状态为 PENDING 时）."""
        ...

    def update_bot_status_on_device_failed(self, *, binding_id: int) -> None:
        """设备变 FAILED 时更新关联 Bot 状态为 FAILED."""
        ...


# =============================================================================
# OssToNasRecordRepository — ac_oss_to_nas_record 表 CRUD 接口
# 实现见 plugins/{prod,local}/oss_to_nas_record_repository.py
# =============================================================================

@runtime_checkable
class OssToNasRecordRepository(Protocol):
    """OSS → NAS 迁移记录仓库接口."""

    def get_record(
        self,
        staff_no: str,
        bot_id: str,
        env: str | None = None,
    ) -> Any | None:
        """按 staff_no + bot_id 查询单条迁移记录.

        Args:
            env: 环境标 (pre/prod)，若不传则自动获取当前环境.
        """
        ...

    def query_records_by_batch(
        self,
        env: str,
        batch_no: str,
        sub_batch_no: str,
        status_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """按批次查询迁移记录，可按 storage_status 过滤."""
        ...

    def update_status(
        self,
        staff_no: str,
        bot_id: str,
        new_status: str,
        env: str | None = None,
    ) -> None:
        """更新单条记录的 storage_status."""
        ...

    def insert_record(
        self,
        staff_no: str,
        bot_id: str,
        env: str,
        batch_no: str,
        sub_batch_no: str,
        bot_info: dict[str, Any] | None = None,
        storage_status: str = "oss",
    ) -> dict[str, Any]:
        """插入一条迁移记录."""
        ...

    def update_record(
        self,
        staff_no: str,
        bot_id: str,
        updates: dict[str, Any],
        env: str | None = None,
    ) -> dict[str, Any]:
        """更新单条记录的指定字段，返回更新后的记录."""
        ...

    def delete_record(
        self,
        staff_no: str,
        bot_id: str,
        env: str | None = None,
    ) -> bool:
        """删除单条迁移记录，返回是否删除成功."""
        ...

    def batch_update_status(
        self,
        env: str,
        batch_no: str,
        sub_batch_no: str,
        new_status: str,
    ) -> int:
        """批量更新批次记录的 storage_status，返回影响行数."""
        ...
