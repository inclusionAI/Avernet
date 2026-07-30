"""协作者权限检查拦截器。

检查用户是否有操作 Bot 的权限，支持操作日志记录。
"""
from __future__ import annotations

import asyncio
import inspect
import json
from typing import Callable

from agentclaw.community.core.bot_collaborator.interceptor.base import (
    InterceptedResponse,
    InterceptorContext,
)
from agentclaw.community.core.bot_collaborator.interceptor.expression import ExpressionResolver
from agentclaw.community.core.bot_collaborator.interceptor.extractors import (
    PermissionParams,
    PermissionParamsExtractor,
    SimplePermissionParamsExtractor,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import (
    CollaboratorLockService,
)
from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
    CollaboratorService,
)
from agentclaw.community.core.bot_collaborator.services.member_management_capability import (
    MemberManagementCapabilityService,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class CollaboratorPermissionInterceptor:
    """协作者权限检查拦截器。

    检查当前用户是否有操作指定 Bot 的权限，支持操作日志记录和锁检查。

    权限检查逻辑：
    1. 没有协作者时：
       - owner 直接放行
       - 非owner 检查协作者权限（通常无权限）
    2. 有协作者时：
       - skip_lock_check=True 或 persist_audit_log=False：只检查协作者权限，跳过锁检查
       - 两者都为 False（默认）：
         - 无锁：拒绝（需先获取锁）
         - 自己持锁：放行（需有协作者权限）
         - 他人持锁：拒绝 423 并返回持有者信息

    支持三种使用方式：

    1. 简单模式：直接指定路径表达式
       CollaboratorPermissionInterceptor(
           bot_id="$request.bot_id",
           owner_id="$request.owner_id",
       )

    2. 表达式提参 + 回调查库：
       CollaboratorPermissionInterceptor(
           params_extractor=extract_from_publish_id,
           extractor_params={
               "publish_id": "$request.publish_id",
           },
       )

    3. 兼容旧方式（只传回调函数）：
       CollaboratorPermissionInterceptor(
           params_extractor=extract_from_publish_id,
       )

    Attributes:
        required_level: 需要的权限级别，默认 ADMIN
        skip_lock_check: 是否跳过锁检查，默认 False
        name: 拦截器名称
    """

    name = "collaborator_permission"

    def __init__(
        self,
        required_level: PermissionLevel = PermissionLevel.ADMIN,
        # 方式一：表达式参数
        bot_id: str = "bot_id",
        owner_id: str = "owner_id",
        # 方式二/三：回调函数
        params_extractor: Callable | PermissionParamsExtractor | None = None,
        extractor_params: dict[str, str] | None = None,
        # 审计入库控制
        persist_audit_log: bool = True,
        # 锁检查控制
        skip_lock_check: bool = False,
    ):
        """初始化拦截器。

        Args:
            required_level: 需要的权限级别，默认 ADMIN
            bot_id: bot_id 参数表达式，如 "$request.bot_id"
            owner_id: owner_id 参数表达式，如 "$request.owner_id"
            params_extractor: 自定义提取函数
            extractor_params: 表达式参数映射，如 {"publish_id": "$request.publish_id"}
                配合 params_extractor 使用，表达式解析后的值会作为参数传给回调函数
            persist_audit_log: 是否将操作审计记录到数据库，默认 True。
                设置为 False 时，同时跳过锁检查（用于不需要审计的高频操作）。
            skip_lock_check: 是否跳过锁检查，默认 False。
                设置为 True 时，只检查协作者权限，不检查锁状态（用于抢锁等特殊操作）。
        """
        self.required_level = required_level
        self.bot_id_expr = bot_id
        self.owner_id_expr = owner_id
        self.params_extractor = params_extractor
        self.extractor_params = extractor_params
        self.persist_audit_log = persist_audit_log
        self.skip_lock_check = skip_lock_check
        self.resolver = ExpressionResolver()

        # 简单模式的提取器
        self._simple_extractor = SimplePermissionParamsExtractor(
            bot_id=bot_id,
            owner_id=owner_id,
        )

    async def before(self, ctx: InterceptorContext) -> InterceptorContext | None:
        """前置拦截：检查用户权限和锁状态。

        权限检查逻辑：
        1. 获取当前用户 ID，失败则返回 400
        2. 提取 bot_id 和 owner_id
        3. 获取锁信息（包含是否有协作者）
        4. 没有协作者时：
           - owner 直接放行
           - 非owner 检查协作者权限
        5. 有协作者时：
           - 无锁：拒绝（需先获取锁）
           - 自己持锁：放行
           - 他人持锁：拒绝并返回持有者信息

        Args:
            ctx: 拦截器上下文

        Returns:
            - 返回 ctx：权限检查通过
            - 返回 None：权限不足，已设置 ctx.response
        """
        user_id = ctx.user.staffId if ctx.user else None
        if not user_id:
            ctx.response = InterceptedResponse(
                success=False,
                message="无法获取用户信息",
                error_code=400,
            )
            return None

        # 提取权限参数
        params = await self._extract_params(ctx)

        # 缓存日志所需信息（用于 after 阶段）
        ctx.metadata["_log_user_id"] = user_id
        ctx.metadata["_log_bot_id"] = params.bot_id
        ctx.metadata["_log_owner_id"] = params.owner_id

        # 获取 trace_id
        try:
            import opentracing
            scope = opentracing.tracer.scope_manager.active
            if scope and scope.span:
                ctx.metadata["_log_trace_id"] = str(scope.span.context.trace_id)
        except Exception:
            pass

        # 没有 owner_id 时尝试主动解析 Bot 归属，而非直接放行
        # 修复：省略 owner_id 不应绕过鉴权（水平越权漏洞）
        if not params.owner_id:
            resolved_owner_id = self._resolve_owner_id(
                ctx, params.bot_id, user_id,
            )
            if resolved_owner_id is None:
                # 无法解析（BotRepository 不可用或 bot 不存在）：
                # 回退到跳过权限检查，由业务层返回更具体的错误（如 404）。
                # 此路径仅在 DI 环境不完整时命中，生产环境 BotRepository 始终可用。
                ctx.metadata["permission_skipped"] = True
                return ctx
            if resolved_owner_id == user_id:
                # 解析到 owner 即当前用户，等效于"没协作者时 owner 放行"
                ctx.metadata["permission_level"] = PermissionLevel.OWNER.name
                ctx.metadata["owner_id_resolved"] = True
                ctx.metadata["_log_owner_id"] = resolved_owner_id
                return ctx
            # 解析到 owner 不是当前用户，构造完整 params 继续走后续鉴权
            params = PermissionParams(
                bot_id=params.bot_id,
                owner_id=resolved_owner_id,
            )
            ctx.metadata["_log_owner_id"] = resolved_owner_id
            ctx.metadata["owner_id_resolved"] = True

        # 获取锁信息（包含是否有协作者）
        lock_info = None
        if params.bot_id:
            lock_service = self._get_lock_service(ctx)
            if lock_service:
                try:
                    lock_info = lock_service.get_lock_info(
                        bot_id=params.bot_id,
                        owner_id=params.owner_id,
                        user_id=user_id,
                    )
                except Exception as e:
                    logger.warning("[before] Failed to get lock info: %s", e)

        has_collaborators = lock_info.has_collaborators if lock_info else False

        # 没有协作者时的逻辑
        if not has_collaborators:
            # owner 直接放行
            if user_id == params.owner_id:
                ctx.metadata["permission_level"] = PermissionLevel.OWNER.name
                return ctx
            # 非 owner 没有权限
            ctx.response = InterceptedResponse(
                success=False,
                message="权限不足：需要 OWNER 权限",
                error_code=403,
            )
            return None

        # 有协作者时检查权限
        if has_collaborators:
            # coding 应用（应用成员场景）不使用 bot 级编辑锁：成员复用协作者表，
            # 一旦写入即 has_collaborators=True，但 coding 应用的并发控制走会话级锁，
            # 不应被 bot 级锁拦截。因此对 coding 应用等效 skip_lock_check。
            # 仅在「本会强制锁」时才查 bot，避免给 Service Bot 写路径平添开销。
            skip_lock = self.skip_lock_check or not self.persist_audit_log
            if not skip_lock and self._is_coding_app(ctx, params.bot_id, params.owner_id):
                skip_lock = True

            # 跳过锁检查（抢锁/禁用审计/coding 应用）：只校验协作者权限
            if skip_lock:
                # owner 已经在上面放行
                if user_id != params.owner_id and params.bot_id:
                    service = self._get_collaborator_service(ctx)
                    if service:
                        try:
                            result = service.check_collaborator_permission(
                                bot_id=params.bot_id,
                                owner_id=params.owner_id,
                                user_id=user_id,
                                required_level=self.required_level,
                            )
                            if not result["has_permission"]:
                                ctx.response = InterceptedResponse(
                                    success=False,
                                    message=f"权限不足：需要 {self.required_level.name} 权限",
                                    error_code=403,
                                )
                                return None
                            ctx.metadata["permission_level"] = result["level"]
                        except Exception as e:
                            # Bot 不存在或其他错误，放行让业务处理
                            ctx.metadata["permission_check_error"] = str(e)
                return ctx

            # 检查锁状态（有协作者时必须持锁才能操作）
            if not lock_info or not lock_info.lock:
                # 无锁：拒绝（需先获取锁）
                ctx.response = InterceptedResponse(
                    success=False,
                    message="Bot 正在被其他用户编辑，请先获取编辑锁",
                    error_code=423,
                    data={"locked": False, "lock": None},
                )
                return None

            if lock_info.lock.holder_user_id != user_id:
                # 他人持锁：拒绝并返回持有者信息
                ctx.response = InterceptedResponse(
                    success=False,
                    message="Bot 正在被其他用户编辑",
                    error_code=423,
                    data={
                        "locked": True,
                        "lock": {
                            "holder_user_id": lock_info.lock.holder_user_id,
                            "holder_name": lock_info.holder_name,
                        },
                    },
                )
                return None

            # 自己持锁，检查权限（owner 已经在上面放行）
            if user_id != params.owner_id and params.bot_id:
                service = self._get_collaborator_service(ctx)
                if service:
                    try:
                        result = service.check_collaborator_permission(
                            bot_id=params.bot_id,
                            owner_id=params.owner_id,
                            user_id=user_id,
                            required_level=self.required_level,
                        )
                        if not result["has_permission"]:
                            ctx.response = InterceptedResponse(
                                success=False,
                                message=f"权限不足：需要 {self.required_level.name} 权限",
                                error_code=403,
                            )
                            return None
                        ctx.metadata["permission_level"] = result["level"]
                    except Exception as e:
                        # Bot 不存在或其他错误，放行让业务处理
                        ctx.metadata["permission_check_error"] = str(e)

        return ctx

    async def after(self, ctx: InterceptorContext) -> None:
        """后置拦截：记录操作审计日志（不影响主流程）。"""
        # 如果禁用审计入库，直接返回
        if not self.persist_audit_log:
            return

        # 获取方法全路径
        func_path = f"{ctx.func.__module__}.{ctx.func.__qualname__}" if ctx.func else "unknown"

        # 跳过权限检查或没有权限则不记录日志
        permission_level = ctx.metadata.get("permission_level")
        permission_skipped = ctx.metadata.get("permission_skipped")
        user_id = ctx.metadata.get("_log_user_id")
        bot_id = ctx.metadata.get("_log_bot_id")
        owner_id = ctx.metadata.get("_log_owner_id")
        if permission_skipped:
            # 没有 owner_id 且无法解析，跳过权限检查，不记录日志
            logger.info("[after] 跳过权限检查（无 owner_id），不插入操作日志, user_id=%s, bot_id=%s, owner_id=%s, func=%s",
                        user_id, bot_id, owner_id, func_path)
            return
        if not permission_level:
            # 权限检查未通过，打印日志但不插入操作记录
            logger.info("[after] 没有权限，不插入操作日志, user_id=%s, bot_id=%s, owner_id=%s, func=%s",
                        user_id, bot_id, owner_id, func_path)
            return
        if permission_level == "OWNER":
            # owner 操作只打印日志，不插入操作记录
            logger.info("[after] owner 操作，不插入操作日志, user_id=%s, bot_id=%s, owner_id=%s, func=%s",
                        user_id, bot_id, owner_id, func_path)
            return

        try:
            # 捕获 injector 引用，供异步日志任务使用
            # （asyncio.to_thread 在线程内执行，ctx 不应跨线程共享）
            injector = ctx.injector

            # 异步执行日志记录，不阻塞主流程
            def _do_log():
                # 序列化请求参数
                params = {}
                for key, value in ctx.route_kwargs.items():
                    try:
                        if key == "user":
                            continue
                        if hasattr(value, 'model_dump'):
                            params[key] = value.model_dump()
                        elif isinstance(value, (str, int, float, bool, type(None))):
                            params[key] = value
                    except Exception:
                        pass

                log_repo = self._get_log_repository(injector)
                if not log_repo:
                    return

                logger.info("[after] Logged: user_id=%s, bot_id=%s, owner_id=%s, func=%s, params=%s",
                            user_id, bot_id, owner_id, func_path, json.dumps(params))

                log_repo.insert({
                    "bot_id": bot_id,
                    "owner_id": owner_id or "",
                    "detail": json.dumps({
                        "func": func_path,
                        "trace_id": ctx.metadata.get("_log_trace_id"),
                    }),
                    "operator_id": user_id,
                })

            asyncio.create_task(asyncio.to_thread(_do_log))
        except Exception as e:
            logger.warning("[after] Failed to create log task: %s", e)

    async def _extract_params(self, ctx: InterceptorContext) -> PermissionParams:
        """提取权限参数。"""
        if self.params_extractor is None:
            # 简单模式：使用表达式解析
            return await self._simple_extractor.extract(ctx)

        if self.extractor_params:
            # 方式二：表达式提参 + 回调
            kwargs = {}
            for param_name, expr in self.extractor_params.items():
                kwargs[param_name] = self.resolver.resolve(expr, ctx.route_kwargs)

            # 根据函数签名决定是否传入 ctx
            sig = inspect.signature(self.params_extractor)
            if 'ctx' in sig.parameters:
                kwargs['ctx'] = ctx

            # 调用回调函数
            result = self.params_extractor(**kwargs)
            if asyncio.iscoroutine(result):
                return await result
            return result

        # 方式三：兼容旧方式
        if isinstance(self.params_extractor, PermissionParamsExtractor):
            return await self.params_extractor.extract(ctx)

        # 普通函数
        result = self.params_extractor(ctx)
        if asyncio.iscoroutine(result):
            return await result
        return result

    def _resolve_owner_id(
        self,
        ctx: InterceptorContext,
        bot_id: str | None,
        user_id: str,
    ) -> str | None:
        """当请求未提供 owner_id 时，主动解析 Bot 归属。

        解析策略：
        1. bot_id 缺失或为 "default" → 返回当前 user_id（语义=我的 bot）
        2. bot_id 有值 → 通过 BotRepository.get_by_id 查询 bot 记录，
           返回 bot.owner_id
        3. BotRepository 不可用 或 bot 不存在 → 返回 None（回退到 skip）

        返回 None 表示无法解析，调用方应回退到跳过权限检查
        （让业务层返回更具体的错误如 404，而非拦截器一刀切 403）。
        """
        if not bot_id or bot_id == "default":
            return user_id

        if ctx.injector is None:
            logger.warning(
                "[_resolve_owner_id] injector 不可用，bot_id=%s", bot_id,
            )
            return None

        try:
            from agentclaw.community.core.bot_management.repository.protocol import (
                BotRepository,
            )
            repo = ctx.injector.get(BotRepository)
            bot = repo.get_by_id(bot_id)
            if (
                bot
                and isinstance(bot, dict)
                and isinstance(bot.get("owner_id"), str)
                and bot["owner_id"]
            ):
                return bot["owner_id"]
            logger.warning(
                "[_resolve_owner_id] bot 未找到或无 owner_id, bot_id=%s", bot_id,
            )
            return None
        except Exception as e:
            logger.warning(
                "[_resolve_owner_id] 查询 bot 失败: %s, bot_id=%s", e, bot_id,
            )
            return None

    def _get_collaborator_service(
        self, ctx: InterceptorContext
    ) -> CollaboratorService | None:
        """获取 CollaboratorService 实例。

        通过 ``ctx.injector`` 解析，避免直接依赖全局注入器。
        ``ctx.injector`` 在 ``with_interceptors`` 装饰器中通过
        ``request.app.state.injector`` 注入。
        """
        if ctx.injector is None:
            return None
        try:
            return ctx.injector.get(CollaboratorService)
        except Exception:
            return None

    def _is_coding_app(
        self, ctx: InterceptorContext, bot_id: str | None, owner_id: str | None
    ) -> bool:
        """判断目标 Bot 是否使用应用/成员管理语义。

        具体规则通过 ``MemberManagementCapabilityService`` 协调：通用模板开关
        加各引擎自己的 capability 实现。失败时保守返回 False（即按 Service Bot
        原逻辑走 bot 级锁）。
        """
        if not bot_id or not owner_id or ctx.injector is None:
            return False
        try:
            from agentclaw.community.core.bot_collaborator.protocols import (
                BotServiceProtocol,
            )

            bot_service = ctx.injector.get(BotServiceProtocol)
            bot = bot_service.get_bot(bot_id, owner_id)
            capability_service = ctx.injector.get(MemberManagementCapabilityService)
            return capability_service.uses_member_management_semantics(bot, bot_id)
        except Exception as e:
            logger.warning("[_is_coding_app] check failed: %s", e)
            return False

    def _get_lock_service(
        self, ctx: InterceptorContext
    ) -> CollaboratorLockService | None:
        """获取 CollaboratorLockService 实例。

        通过 ``ctx.injector`` 解析，用于检查锁状态。
        """
        if ctx.injector is None:
            return None
        try:
            return ctx.injector.get(CollaboratorLockService)
        except Exception:
            return None

    def _get_log_repository(self, injector):
        """获取 BotCollabLogRepository 实例。

        ``injector`` 由 ``after()`` 在创建线程任务前从 ``ctx.injector``
        捕获，避免跨线程共享 ``ctx`` 对象。
        """
        if injector is None:
            return None
        try:
            from agentclaw.community.core.bot_collaborator.repository.protocol import BotCollabLogRepositoryProtocol
            return injector.get(BotCollabLogRepositoryProtocol)
        except Exception:
            return None
