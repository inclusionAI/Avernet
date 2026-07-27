"""Bot 协作者管理服务。

提供协作者的 CRUD 操作和权限检查功能。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, List, Optional, Dict, Any

from agentclaw.community.core.bot_collaborator.models import (
    CollaboratorRecord,
    CollaboratorRole,
    PermissionLevel,
)
from agentclaw.community.core.bot_collaborator.repository.protocol import CollaboratorRepositoryProtocol
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.utils.avernet_tenant import bind_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.plugins.passport import PassportPlugin
    from agentclaw.community.core.devices.services.device_context_resolver import DeviceContextResolver
    from agentclaw.community.di.modules.skill_center_module import DeviceFilesystemDispatcher
    from agentclaw.community.core.devices.services.device_filesystem import DeviceFileSystem

logger = get_logger()

# 运行设备上 .credentials 的固定路径(prod 容器内 start_service.sh 写入处,
# 与 engine ``CredentialsService.DEFAULT_CREDENTIALS_PATH`` 一致)。
_DEVICE_CREDENTIALS_PATH = "/home/admin/.credentials"


def _run_coro_blocking(coro: Any) -> Any:
    """在独立线程里跑协程并阻塞等待结果。

    ``on_collaboration_changed`` 是同步方法，但被 ``async def`` 路由**直接同步调用**，
    运行在事件循环线程上。此处：
      - 不能用 ``asyncio.run``(当前线程已有 running loop → 抛错);
      - 不能用 ``run_coroutine_threadsafe(coro, 当前loop)``(当前线程被阻塞 → 死锁)。
    所以起一个新线程、用全新的 ``asyncio.run`` 跑，再 ``join`` 取结果/异常。
    协作者变更频率低，每次起线程的开销可接受。
    """
    import asyncio
    import threading

    box: Dict[str, Any] = {}

    def _runner() -> None:
        try:
            box["result"] = asyncio.run(coro)
        except BaseException as e:  # noqa: BLE001 - 透传给调用线程统一处理
            box["error"] = e

    thread = threading.Thread(
        target=bind_current_avernet_tenant(_runner), daemon=True
    )
    thread.start()
    thread.join()

    if "error" in box:
        raise box["error"]
    return box.get("result")


def _replace_admins_line(content: str, admins: List[str]) -> str:
    """逐行替换/追加 ``ADMINS=`` 行，其它行原样保留。

    镜像 engine ``CredentialsService.update_fields`` 的逐行替换思路(不引入 engine 依赖)。
    ``admins`` 为空 → 写 ``ADMINS=``(空值)以清空，而非删除行——保证设备端能读到“无 admin”。
    """
    admins_value = ",".join(admins)
    new_line = f"ADMINS={admins_value}"

    lines = content.splitlines()
    replaced = False
    out: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip().upper()
            if key == "ADMINS":
                out.append(new_line)
                replaced = True
                continue
        out.append(line)

    if not replaced:
        out.append(new_line)

    # 保持末尾换行(与 ``_write_credentials`` 写出的文件一致)。
    return "\n".join(out) + "\n"


# ============================================================================
# 异常定义
# ============================================================================
class CollaboratorServiceError(Exception):
    """协作者服务基础异常。"""
    pass


class PermissionDeniedError(CollaboratorServiceError):
    """权限不足异常。"""
    pass


class CollaboratorNotFoundError(CollaboratorServiceError):
    """协作者不存在异常。"""
    pass


class CollaboratorAlreadyExistsError(CollaboratorServiceError):
    """协作者已存在异常。"""
    pass


class CannotRemoveSelfError(CollaboratorServiceError):
    """不能移除自己异常。"""
    pass


class BotNotFoundError(CollaboratorServiceError):
    """Bot 不存在异常。"""
    pass


class BotNotServiceTypeError(CollaboratorServiceError):
    """Bot 不是服务型异常。"""
    pass


# ============================================================================
# 服务实现
# ============================================================================
class CollaboratorService:
    """Bot 协作者管理服务。

    提供协作者的 CRUD 操作和权限检查功能。
    """

    def __init__(
        self,
        collaborator_repo: CollaboratorRepositoryProtocol,
        bot_repo: BotRepository,
        passport_plugin: PassportPlugin,
        resolver_provider: "Callable[[], DeviceContextResolver]",
        device_fs_dispatcher_provider: "Callable[[], DeviceFilesystemDispatcher]",
    ) -> None:
        """初始化服务。

        Args:
            collaborator_repo: 协作者数据访问接口
            bot_repo: Bot 数据访问接口
            passport_plugin: Passport 插件，用于协作者变更时同步 admins 到 Passport 服务
            resolver_provider: 惰性 thunk，返回 ``DeviceContextResolver``（按 bot 解析
                运行设备）。lazy 是为了打断构造期 DI 循环
                （device 图反向依赖 ``BotService``）。
            device_fs_dispatcher_provider: 惰性 thunk，返回 ``DeviceFilesystemDispatcher``
                （按 ``DeviceContext`` 派发 per-bot 文件读写插件）。
        """
        self._collaborator_repo = collaborator_repo
        self._bot_repo = bot_repo
        self._passport_plugin = passport_plugin
        self._resolver_provider = resolver_provider
        self._device_fs_dispatcher_provider = device_fs_dispatcher_provider

    # ========================================================================
    # 权限检查
    # ========================================================================

    def get_permission_level(
        self,
        bot_pk: int,
        user_id: str,
        owner_id: str,
        env: Optional[str] = None,
    ) -> PermissionLevel:
        """获取用户在 Bot 中的权限级别。

        Args:
            bot_pk: Bot 主键
            user_id: 用户工号
            owner_id: Bot 拥有者工号
            env: 环境标识，默认当前环境

        Returns:
            PermissionLevel 权限级别
        """
        if env is None:
            env = get_current_env()

        # 1. 检查是否是 owner
        if user_id == owner_id:
            return PermissionLevel.OWNER

        # 2. 检查是否是协作者
        role = self._collaborator_repo.get_user_role(bot_pk, user_id, env)
        if role == CollaboratorRole.ADMIN:
            return PermissionLevel.ADMIN
        elif role == CollaboratorRole.MEMBER:
            return PermissionLevel.MEMBER

        # 3. 无权限
        return PermissionLevel.NONE

    def check_permission(
        self,
        bot_pk: int,
        user_id: str,
        owner_id: str,
        required_level: PermissionLevel,
        env: Optional[str] = None,
    ) -> None:
        """检查用户是否有足够的权限。

        Args:
            bot_pk: Bot 主键
            user_id: 用户工号
            owner_id: Bot 拥有者工号
            required_level: 需要的权限级别
            env: 环境标识

        Raises:
            PermissionDeniedError: 权限不足
        """
        level = self.get_permission_level(bot_pk, user_id, owner_id, env)
        if level < required_level:
            raise PermissionDeniedError(
                f"权限不足: 需要 {required_level.name} 权限，当前用户 {user_id} 权限为 {level.name}"
            )

    # ========================================================================
    # 添加协作者
    # ========================================================================

    def add_collaborator(
        self,
        bot_id: str,
        owner_id: str,
        user_id: str,
        operator_id: str,
        user_name: Optional[str] = None,
        role: str = CollaboratorRole.ADMIN,
        env: Optional[str] = None,
    ) -> CollaboratorRecord:
        """添加协作者。

        Args:
            bot_id: Bot ID
            owner_id: Bot 拥有者工号
            user_id: 协作者工号
            operator_id: 操作者工号
            user_name: 协作者花名（可选）
            role: 角色（admin/member，默认 admin）
            env: 环境标识

        Returns:
            创建的 CollaboratorRecord

        Raises:
            BotNotFoundError: Bot 不存在
            BotNotServiceTypeError: Bot 不是服务型
            PermissionDeniedError: 操作者权限不足
            CollaboratorAlreadyExistsError: 协作者已存在
        """
        if env is None:
            env = get_current_env()

        # 1. 查询 Bot 信息
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise BotNotFoundError(f"Bot 不存在: bot_id={bot_id}, owner_id={owner_id}")

        # 2. 检查 Bot 类型
        #    - service：Service Bot 协作者，走原逻辑。
        #    - coding 应用（active_engine == "claude_code" 且 template_type ==
        #      "applicationCoding"）：作为"应用成员"复用同一套协作者流程，放行。
        is_coding_app = (
            bot.get("active_engine") == "claude_code"
            and bot.get("template_type") == "applicationCoding"
        )
        if bot.get("bot_type") != "service" and not is_coding_app:
            raise BotNotServiceTypeError(f"Bot 不是服务型且非 coding 应用: bot_id={bot_id}")

        bot_pk = bot["id"]
        owner_id_from_bot = bot["owner_id"]

        # 3. 检查操作者权限（需要 ADMIN 或 OWNER）
        self.check_permission(
            bot_pk=bot_pk,
            user_id=operator_id,
            owner_id=owner_id_from_bot,
            required_level=PermissionLevel.ADMIN,
            env=env,
        )

        # 4. 不能添加 owner 为协作者
        if user_id == owner_id_from_bot:
            raise CollaboratorAlreadyExistsError(
                f"不能添加 Bot 拥有者为协作者: user_id={user_id}"
            )

        # 5. 检查是否已存在
        existing = self._collaborator_repo.get_by_bot_and_user(bot_pk, user_id, env)
        if existing:
            raise CollaboratorAlreadyExistsError(
                f"协作者已存在: bot_pk={bot_pk}, user_id={user_id}"
            )

        # 6. 创建协作者记录
        record = self._collaborator_repo.insert({
            "bot_pk": bot_pk,
            "bot_id": bot_id,
            "owner_id": owner_id_from_bot,
            "user_id": user_id,
            "user_name": user_name,
            "role": role,
            "operator_id": operator_id,
            "env": env,
        })

        logger.info(
            "[add_collaborator] Added: bot_id=%s, user_id=%s, role=%s, operator=%s",
            bot_id, user_id, role, operator_id
        )

        # 7. 触发协作关系变更回调
        self.on_collaboration_changed(bot_id, owner_id_from_bot, env)

        return record

    # ========================================================================
    # 获取协作者列表
    # ========================================================================

    def list_collaborators(
        self,
        bot_id: str,
        owner_id: str,
        user_id: str,
        role: Optional[str] = None,
        env: Optional[str] = None,
    ) -> List[CollaboratorRecord]:
        """获取 Bot 的协作者列表。

        Args:
            bot_id: Bot ID
            owner_id: Bot 拥有者工号
            user_id: 请求用户工号
            role: 角色过滤（可选）
            env: 环境标识

        Returns:
            CollaboratorRecord 列表

        Raises:
            BotNotFoundError: Bot 不存在
            PermissionDeniedError: 用户无权限查看
        """
        if env is None:
            env = get_current_env()

        # 1. 查询 Bot 信息
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise BotNotFoundError(f"Bot 不存在: bot_id={bot_id}, owner_id={owner_id}")

        bot_pk = bot["id"]
        owner_id_from_bot = bot["owner_id"]

        # 2. 检查用户权限（需要 MEMBER 或更高）
        self.check_permission(
            bot_pk=bot_pk,
            user_id=user_id,
            owner_id=owner_id_from_bot,
            required_level=PermissionLevel.MEMBER,
            env=env,
        )

        # 3. 获取协作者列表
        return self._collaborator_repo.list_by_bot(
            bot_id=bot_id,
            owner_id=owner_id_from_bot,
            env=env,
            role=role,
        )

    # ========================================================================
    # 更新协作者
    # ========================================================================

    def update_collaborator(
        self,
        collaborator_id: int,
        operator_id: str,
        user_id: Optional[str] = None,
        user_name: Optional[str] = None,
        role: Optional[str] = None,
        env: Optional[str] = None,
    ) -> CollaboratorRecord:
        """更新协作者信息。

        Args:
            collaborator_id: 协作者记录 ID
            operator_id: 操作者工号
            user_id: 新的协作者工号（可选）
            user_name: 新的协作者花名（可选）
            role: 新的角色（可选）
            env: 环境标识

        Returns:
            更新后的 CollaboratorRecord

        Raises:
            CollaboratorNotFoundError: 协作者不存在
            PermissionDeniedError: 操作者权限不足
            CollaboratorAlreadyExistsError: 新 user_id 已存在于该 Bot
        """
        if env is None:
            env = get_current_env()

        # 1. 查询协作者记录
        collaborator = self._collaborator_repo.get_by_id(collaborator_id)
        if not collaborator:
            raise CollaboratorNotFoundError(f"协作者不存在: id={collaborator_id}")

        bot_pk = collaborator.bot_pk

        # 2. 查询 Bot 信息获取 owner_id
        # 注意：collaborator 记录中有 owner_id，可以直接使用
        owner_id = collaborator.owner_id

        # 3. 检查操作者权限（需要 ADMIN 或 OWNER）
        self.check_permission(
            bot_pk=bot_pk,
            user_id=operator_id,
            owner_id=owner_id,
            required_level=PermissionLevel.ADMIN,
            env=env,
        )

        # 4. 如果要更新 user_id，检查是否冲突
        if user_id and user_id != collaborator.user_id:
            existing = self._collaborator_repo.get_by_bot_and_user(bot_pk, user_id, env)
            if existing:
                raise CollaboratorAlreadyExistsError(
                    f"协作者已存在: bot_pk={bot_pk}, user_id={user_id}"
                )

        # 5. 更新记录
        update_data = {"operator_id": operator_id}
        if user_id is not None:
            update_data["user_id"] = user_id
        if user_name is not None:
            update_data["user_name"] = user_name
        if role is not None:
            update_data["role"] = role

        record = self._collaborator_repo.update(collaborator_id, update_data)

        logger.info(
            "[update_collaborator] Updated: id=%s, operator=%s",
            collaborator_id, operator_id
        )

        # 6. 触发协作关系变更回调
        self.on_collaboration_changed(collaborator.bot_id, owner_id, env)

        return record

    # ========================================================================
    # 移除协作者
    # ========================================================================

    def remove_collaborator(
        self,
        collaborator_id: int,
        operator_id: str,
        env: Optional[str] = None,
    ) -> bool:
        """移除协作者。

        Args:
            collaborator_id: 协作者记录 ID
            operator_id: 操作者工号
            env: 环境标识

        Returns:
            是否成功移除

        Raises:
            CollaboratorNotFoundError: 协作者不存在
            PermissionDeniedError: 操作者权限不足
            CannotRemoveSelfError: 不能移除自己
        """
        if env is None:
            env = get_current_env()

        # 1. 查询协作者记录
        collaborator = self._collaborator_repo.get_by_id(collaborator_id)
        if not collaborator:
            raise CollaboratorNotFoundError(f"协作者不存在: id={collaborator_id}")

        bot_pk = collaborator.bot_pk
        user_id = collaborator.user_id
        owner_id = collaborator.owner_id

        # 2. 检查操作者权限
        operator_level = self.get_permission_level(bot_pk, operator_id, owner_id, env)

        # 3. 不能移除自己（除非是 owner）
        if user_id == operator_id and operator_level != PermissionLevel.OWNER:
            raise CannotRemoveSelfError("不能移除自己")

        # 4. 需要管理员权限
        if operator_level < PermissionLevel.ADMIN:
            raise PermissionDeniedError(
                f"权限不足: 需要 ADMIN 权限才能移除协作者"
            )

        # 5. 删除记录
        success = self._collaborator_repo.delete(collaborator_id)

        logger.info(
            "[remove_collaborator] Removed: id=%s, user_id=%s, operator=%s",
            collaborator_id, user_id, operator_id
        )

        # 6. 触发协作关系变更回调
        self.on_collaboration_changed(collaborator.bot_id, owner_id, env)

        return success

    # ========================================================================
    # 用户参与的 Bot 列表
    # ========================================================================

    def list_user_collaborations(
        self,
        user_id: str,
        env: Optional[str] = None,
    ) -> List[CollaboratorRecord]:
        """获取用户参与的所有 Bot 协作者记录。

        Args:
            user_id: 用户工号
            env: 环境标识

        Returns:
            CollaboratorRecord 列表
        """
        if env is None:
            env = get_current_env()

        return self._collaborator_repo.list_by_user(user_id, env)

    # ========================================================================
    # 检查用户权限
    # ========================================================================

    def check_collaborator_permission(
        self,
        bot_id: str,
        owner_id: str,
        user_id: str,
        required_level: PermissionLevel,
        env: Optional[str] = None,
    ) -> Dict[str, Any]:
        """检查用户在 Bot 中的协作权限。

        这是一个便捷方法，用于 API 层的权限检查。

        Args:
            bot_id: Bot ID
            owner_id: Bot 拥有者工号
            user_id: 要检查的用户工号
            required_level: 需要的权限级别
            env: 环境标识

        Returns:
            包含检查结果的字典：
            - has_permission: bool
            - level: str (权限级别名称)
            - level_value: int (权限级别数值)

        Raises:
            BotNotFoundError: Bot 不存在
        """
        if env is None:
            env = get_current_env()

        logger.info(
            "[check_collaborator_permission] Start: bot_id=%s, owner_id=%s, user_id=%s, required_level=%s, env=%s",
            bot_id, owner_id, user_id, required_level.name, env
        )

        # 1. 查询 Bot 信息
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise BotNotFoundError(f"Bot 不存在: bot_id={bot_id}, owner_id={owner_id}")

        bot_pk = bot["id"]
        owner_id_from_bot = bot["owner_id"]

        # 2. 获取权限级别
        level = self.get_permission_level(bot_pk, user_id, owner_id_from_bot, env)

        result = {
            "has_permission": level >= required_level,
            "level": level.name,
            "level_value": level.value,
        }
        logger.info(
            "[check_collaborator_permission] Result: has_permission=%s, level=%s, user_id=%s, bot_id=%s",
            result["has_permission"], result["level"], user_id, bot_id
        )

        return result

    # ========================================================================
    # 协作关系变更回调
    # ========================================================================

    def on_collaboration_changed(
        self,
        bot_id: str,
        owner_id: str,
        env: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """协作关系变更回调。

        当 Bot 协作关系发生变更时调用此方法，用于处理相关后续逻辑。

        Args:
            bot_id: Bot ID
            owner_id: Bot 拥有者工号
            env: 环境标识

        Returns:
            处理结果列表，每个元素包含：
            - user_id: 协作者工号
            - notified: 是否通知成功

        Raises:
            BotNotFoundError: Bot 不存在
        """
        if env is None:
            env = get_current_env()

        # 1. 查询 Bot 信息
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise BotNotFoundError(f"Bot 不存在: bot_id={bot_id}, owner_id={owner_id}")

        owner_id_from_bot = bot["owner_id"]

        # 2. 获取所有协作者
        collaborators = self._collaborator_repo.list_by_bot(
            bot_id=bot_id,
            owner_id=owner_id_from_bot,
            env=env,
        )
        logger.info(
            "[on_collaboration_changed] Collaborators: %s",
            [{"user_id": c.user_id, "role": c.role} for c in collaborators]
        )

        # 3. 通知所有协作者（TODO: 实现实际通知逻辑）
        results = []

        logger.info(
            "[on_collaboration_changed] Results: %s, bot_id=%s, owner_id=%s",
            results, bot_id, owner_id
        )

        # 4. 同步 admin 协作者到 Passport 服务
        # 注意：Passport 服务协议中 admins=[] 表示清空、admins=None 表示不更新；
        # 这里要把 admin 全部移除时必须传 []，不能短路成 None。
        admins = [c.user_id for c in collaborators if c.role == CollaboratorRole.ADMIN]
        try:
            self._passport_plugin.update_passport(
                bot_id=bot_id,
                user_id=owner_id_from_bot,
                admins=admins,
            )
            logger.info(
                "[on_collaboration_changed] Synced admins to the passport service: bot_id=%s, admins=%s",
                bot_id, admins,
            )
        except Exception as e:
            logger.warning(
                "[on_collaboration_changed] Failed to sync admins to the passport service: bot_id=%s, error=%s",
                bot_id, e,
            )

        # 5. 同步 admin 工号到运行设备的 .credentials 文件（ADMINS= 行）。
        # 失败只 warning，不影响协作者变更主流程，也不回滚已完成的 Passport 服务同步。
        self._sync_admins_to_credentials(bot_id, owner_id_from_bot, admins)

        return results

    # ========================================================================
    # 同步 admins 到运行设备 .credentials
    # ========================================================================

    def _sync_admins_to_credentials(
        self,
        bot_id: str,
        owner_id: str,
        admins: List[str],
    ) -> None:
        """把最新的 ADMIN 工号写回运行设备的 ``/home/admin/.credentials``。

        走 ``DeviceContextResolver`` 解析 bot 的运行设备，再用
        ``DeviceFilesystemDispatcher`` 派发 per-bot 文件读写插件做 read-modify-write，
        只改 ``ADMINS=`` 一行，保留 TOKEN/CLIENT_ID 等其它字段。

        注意：``admins`` 为空列表（最后一个 admin 被移除）时仍要写 ``ADMINS=``（空值）
        以清空设备端旧值——不能因 falsy 短路跳过。

        bot 无运行设备 / 文件不存在 / 任何异常 → 跳过并记日志，
        绝不抛出影响主流程。
        """
        try:
            from agentclaw.community.core.devices.services.device_context import (
                DeviceNotBoundError,
                UnknownProviderError,
            )

            try:
                ctx = self._resolver_provider().resolve_for_bot(bot_id, owner_id)
            except (DeviceNotBoundError, UnknownProviderError) as e:
                logger.info(
                    "[_sync_admins_to_credentials] bot 无运行设备，跳过 credentials 同步: "
                    "bot_id=%s, reason=%s",
                    bot_id, e,
                )
                return

            fs = self._device_fs_dispatcher_provider().dispatch(ctx)
            _run_coro_blocking(self._rewrite_credentials_admins(fs, admins))
            logger.info(
                "[_sync_admins_to_credentials] Synced admins to .credentials: "
                "bot_id=%s, admins=%s",
                bot_id, admins,
            )
        except Exception as e:
            logger.warning(
                "[_sync_admins_to_credentials] Failed to sync admins to .credentials: "
                "bot_id=%s, error=%s",
                bot_id, e,
            )

    @staticmethod
    async def _rewrite_credentials_admins(
        fs: "DeviceFileSystem",
        admins: List[str],
    ) -> None:
        """read-modify-write 设备 .credentials，只替换/追加 ``ADMINS=`` 行。

        文件不存在（``read_file`` 返回 ``None``）→ 跳过：不凭空造文件，否则会丢失
        TOKEN 等启动时写入的字段。
        """
        raw = await fs.read_file(_DEVICE_CREDENTIALS_PATH)
        if raw is None:
            logger.info(
                "[_rewrite_credentials_admins] %s 不存在，跳过",
                _DEVICE_CREDENTIALS_PATH,
            )
            return

        new_content = _replace_admins_line(raw.decode("utf-8"), admins)
        await fs.write_file(_DEVICE_CREDENTIALS_PATH, new_content.encode("utf-8"))
