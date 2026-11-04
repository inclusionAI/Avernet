"""
Backup preparation, instance materialization, public-approval archive and
owner-handoff release — moved verbatim from ``bot_service.py``.

Part of the mechanical split of ``bot_service.py``: ``BotPublicationMixin`` members were
moved here verbatim and the facade composes them onto ``BotService``.
"""

import shutil

from pathlib import Path
from typing import Any, Dict, List, Optional

from agentclaw.community.core.devices.models import (
    DeviceBindingStatus,
    OperatorContext,
    SynlinkMappingInfo,
)
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.utils.avernet_tenant import bind_current_avernet_tenant

from .common import (
    BotInstanceCreationError,
    BotNotFoundError,
    BotServiceError,
    _compose_operator_context,
    _copy_tree_fast,
    logger,
)


class BotPublicationMixin:
    """Backup preparation, instance materialization, public-approval archive and
    owner-handoff release."""

    def prepare_bot_backup(
        self,
        source_bot_id: str,
        bot_id_with_version: str,
        owner_id: str,
    ) -> Path:
        """
        准备bot备份：从source_bot_dir复制到version_bot_dir。

        该方法用于上层在发布前预先准备版本目录，复制源bot数据到版本目录。
        使用 rsync 优化 NAS 场景下的复制性能。

        Args:
            source_bot_id: 原bot ID（源bot）
            bot_id_with_version: 带版本号的bot ID（如 default_v001）
            owner_id: 原bot的owner_id

        Returns:
            Path: 版本目录路径 version_bot_dir

        Raises:
            BotNotFoundError: 如果原bot不存在
            BotInstanceCreationError: 如果复制失败
        """
        from agentclaw.community.core.workspace.path_factory import get_bot_dir

        logger.info(f"[bot_service.prepare_bot_backup] Starting: source_bot_id={source_bot_id}, "
                    f"bot_id_with_version={bot_id_with_version}, owner_id={owner_id}")

        try:
            # Step 1: 获取原bot信息，校验权限
            source_bot = self._repository.get_by_id_and_owner(source_bot_id, owner_id)
            if not source_bot:
                raise BotNotFoundError(f"Source bot not found: {source_bot_id}")

            entity_id = source_bot.get("entity_id")
            entity_type = source_bot.get("entity_type", "staff")

            if not entity_id:
                raise BotServiceError(f"Source bot {source_bot_id} has no associated entity_id")

            source_bot_dir = get_bot_dir(entity_id, source_bot_id, entity_type)
            version_bot_dir = get_bot_dir(entity_id, bot_id_with_version, entity_type)

            # Step 2: source -> version 复制
            if source_bot_dir.exists():
                logger.info(f"[bot_service.prepare_bot_backup] Syncing to version dir: {source_bot_dir} -> {version_bot_dir}")
                _copy_tree_fast(source_bot_dir, version_bot_dir, symlinks=True)
                logger.info(f"[bot_service.prepare_bot_backup] Version dir ready: {version_bot_dir}")
            else:
                logger.warning(f"[bot_service.prepare_bot_backup] Source directory does not exist: {source_bot_dir}")

            return version_bot_dir

        except BotNotFoundError:
            raise
        except BotServiceError:
            raise
        except Exception as e:
            logger.error(f"[bot_service.prepare_bot_backup] Unexpected error: {e}")
            raise BotInstanceCreationError(f"准备bot备份失败: {e}")

    def create_bot_instances(
        self,
        source_bot_id: str,
        bot_id_with_version: str,
        pub_bot_id: str,
        owner_id: str,
        instance_count: int,
        operator: OperatorContext,
        copy_from_source: bool = True,
    ) -> List[Any]:
        """
        创建bot实例并申请设备。

        服务bot发布申请设备的接口。支持两种复制模式：
        1. copy_from_source=True：source_bot_dir -> version_bot_dir -> pub_bot_dir
        2. copy_from_source=False：version_bot_dir -> pub_bot_dir（跳过从source拷贝）

        注意：设备与发布单的绑定关系由调用方（如 BotPublishService）管理，
        该方法只负责申请设备，不感知发布单上下文。

        Args:
            source_bot_id: 原bot ID（源bot）
            bot_id_with_version: 带版本号的bot ID（如 default_v001）
            pub_bot_id: 发布用的bot ID（如 default_p001），设备申请时使用此ID
            owner_id: 原bot的owner_id
            instance_count: 实例数量（需要申请的设备数量）
            operator: 操作者上下文
            copy_from_source: 是否从source_bot_dir拷贝到version_bot_dir，默认为True

        Returns:
            List[DeviceBindingResponse]: 申请的设备binding对象列表

        Raises:
            BotNotFoundError: 如果原bot不存在
            BotInstanceCreationError: 如果创建实例失败
        """
        import concurrent.futures
        from agentclaw.community.core.workspace.path_factory import get_bot_dir
        logger.info(f"[bot_service.create_bot_instances] Starting: source_bot_id={source_bot_id}, "
                   f"bot_id_with_version={bot_id_with_version}, pub_bot_id={pub_bot_id}, "
                   f"copy_from_source={copy_from_source}, owner_id={owner_id}, instance_count={instance_count}")

        try:
            # Step 1: 获取原bot信息，校验权限
            source_bot = self._repository.get_by_id_and_owner(source_bot_id, owner_id)
            if not source_bot:
                raise BotNotFoundError(f"Source bot not found: {source_bot_id}")

            entity_id = source_bot.get("entity_id")
            entity_type = source_bot.get("entity_type", "staff")
            active_engine = source_bot.get("active_engine", DEFAULT_ENGINE_TYPE)
            source_template_type = source_bot.get("template_type")
            source_bot_type = source_bot.get("bot_type", "")

            if not entity_id:
                raise BotServiceError(f"Source bot {source_bot_id} has no associated entity_id")

            source_bot_dir = get_bot_dir(entity_id, source_bot_id, entity_type)
            version_bot_dir = get_bot_dir(entity_id, bot_id_with_version, entity_type)
            pub_bot_dir = get_bot_dir(entity_id, pub_bot_id, entity_type)

            # Step 2: 处理目录复制
            if copy_from_source:
                # copy_from_source=True: 直接从 source 复制到 pub
                if not source_bot_dir.exists():
                    raise BotInstanceCreationError(f"源bot目录不存在: {source_bot_dir}")
                logger.info(f"[bot_service.create_bot_instances] Syncing source to publish dir: {source_bot_dir} -> {pub_bot_dir}")
                _copy_tree_fast(source_bot_dir, pub_bot_dir, symlinks=True)
                logger.info(f"[bot_service.create_bot_instances] Publish dir ready: {pub_bot_dir}")
            else:
                # copy_from_source=False: 从 version 复制到 pub（需要预先调用prepare_bot_backup准备好version目录）
                if not version_bot_dir.exists():
                    raise BotInstanceCreationError(f"版本目录不存在: {version_bot_dir}，请先调用prepare_bot_backup")
                logger.info(f"[bot_service.create_bot_instances] Syncing version to publish dir: {version_bot_dir} -> {pub_bot_dir}")
                _copy_tree_fast(version_bot_dir, pub_bot_dir, symlinks=True)
                logger.info(f"[bot_service.create_bot_instances] Publish dir ready: {pub_bot_dir}")

            # Step 3: 异步申请n台设备（使用pub_bot_id）
            logger.info(f"[bot_service.create_bot_instances] Applying for {instance_count} devices for pub_bot_id={pub_bot_id}")

            # DRM: 判断新建 bot 是否走 NAS
            force_nas = self._is_new_bot_use_nas()

            # 生成软链配置
            symlink_mappings: List[SynlinkMappingInfo] = []
            try:
                skill_set_service = self._skill_set_factory.create(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    bot_id=pub_bot_id,
                    engine_type=active_engine if active_engine == "claude_code" else None,
                )
                symlink_mappings = skill_set_service.get_symlink_mappings(
                    user_id=entity_id,
                    bolt_id=pub_bot_id
                )
                logger.info(f"[bot_service.create_bot_instances] Generated symlink_mappings: {len(symlink_mappings)}")
            except Exception as e:
                logger.warning(f"[bot_service.create_bot_instances] Failed to get symlink_mappings: {e}")

            device_results: list = []
            errors: List[str] = []

            # 服务型 bot 实例化时，复用 source bot 的 admin 协作者作为沙箱启动 admins
            instance_admins = self._query_admin_worknos(bot_id=pub_bot_id, owner_id=owner_id)

            # Read template_config from ac_templates (not ac_bots).
            # source_bot comes from ac_bots which has no template_config column;
            # the value lives in ac_templates.ext.  Without this lookup,
            # create_bot_instances would always pass template_config=None,
            # losing sandbox overrides (image, command, envs, resource_spec).
            instance_template_config = None
            try:
                instance_template_config = self._template_service.get_template_config(source_bot_id)
            except Exception as e:
                logger.warning(
                    "[bot_service.create_bot_instances] Failed to get template for bot %s: %s",
                    source_bot_id, e,
                )
            # 路由到具体 provider 前，先把 template_uid 上下文带给 device 层。
            # 解析失败先记下来；只有后续真正走 BaaS 时才需要 fail-fast。
            device_template_config = self._attach_template_uid_context(
                bot_id=pub_bot_id,
                user_id=owner_id,
                bot_type=source_bot_type,
                engine_type=active_engine,
                template_type=source_template_type,
                template_config=instance_template_config,
            )

            def apply_single_device(index: int) -> tuple[int, Optional[Any], Optional[str]]:
                """申请单台设备，返回(index, result, error)"""
                try:
                    service = self._device_service_provider()
                    result = service.apply_device(
                        apply_reason=f"Create bot instance: {pub_bot_id} (#{index + 1}/{instance_count})",
                        entity_id=entity_id,
                        entity_type=entity_type,
                        operator=operator,
                        bot_id=pub_bot_id,
                        engine=active_engine,
                        bot_type=source_bot_type,
                        owner_id=owner_id,  # 使用传入的 owner_id（发布单中的 owner_id）
                        symbol=symlink_mappings,
                        force_nas=force_nas,
                        admins=instance_admins,
                        template_type=source_template_type,
                        template_config=device_template_config,
                    )
                    return index, result, None
                except Exception as e:
                    logger.error(f"[bot_service.create_bot_instances] Device allocation failed for instance #{index + 1}: {e}")
                    return index, None, str(e)

            # 使用线程池并行申请设备
            # ThreadPoolExecutor threads do NOT copy context vars, so bind the
            # request's tenant onto the submitted callable (captured now, in the
            # request thread) — each worker then runs under the right tenant.
            _apply_single_device = bind_current_avernet_tenant(apply_single_device)
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(instance_count, 10)) as executor:
                # 提交所有任务
                future_to_index = {
                    executor.submit(_apply_single_device, i): i for i in range(instance_count)
                }

                # 收集结果
                for future in concurrent.futures.as_completed(future_to_index):
                    index, result, error = future.result()
                    if error:
                        errors.append(f"Instance #{index + 1}: {error}")
                    elif result:
                        device_results.append(result)

            # 检查是否有申请失败的设备
            if len(device_results) == 0:
                # 全部失败，清理已创建的目录
                logger.error(f"[bot_service.create_bot_instances] All {instance_count} device allocations failed")
                try:
                    shutil.rmtree(pub_bot_dir)
                    logger.info(f"[bot_service.create_bot_instances] Cleaned up directory: {pub_bot_dir}")
                except Exception as e:
                    logger.warning(f"[bot_service.create_bot_instances] Failed to cleanup directory: {e}")
                raise BotInstanceCreationError(f"所有设备申请都失败: {'; '.join(errors)}")

            # 部分失败，记录警告
            if len(device_results) < instance_count:
                logger.warning(
                    f"[bot_service.create_bot_instances] Partial success: "
                    f"{len(device_results)}/{instance_count} devices allocated. "
                    f"Errors: {'; '.join(errors)}"
                )

            logger.info(f"[bot_service.create_bot_instances] Successfully created {len(device_results)} instances "
                       f"for bot {pub_bot_id}")

            return device_results

        except BotNotFoundError:
            raise
        except BotServiceError:
            raise
        except Exception as e:
            logger.error(f"[bot_service.create_bot_instances] Unexpected error: {e}")
            raise BotInstanceCreationError(f"创建bot实例失败: {e}")

    def _archive_public_approval(self, ext: Dict[str, Any], bot_id: str) -> None:
        if "public_approval" not in ext:
            return
        from datetime import datetime
        old_approval = ext.pop("public_approval")
        old_approval["processed_at"] = datetime.now().isoformat()
        history = ext.get("public_approval_history", [])
        history.append(old_approval)
        ext["public_approval_history"] = history[-5:]
        logger.info(f"[bot_service.public_bot] Moved existing approval to history: bot_id={bot_id}, old_puid={old_approval.get('puid')}")

    def _build_public_approval_context(self, bot: Dict[str, Any], operator: Optional[OperatorContext] = None) -> Dict[str, str]:
        """构建公开审批上下文信息。

        Args:
            bot: Bot 对象字典
            operator: 操作者上下文，用于 owner_name 为空时获取操作人名称

        Returns:
            包含 publishHint、botSkills、botMcps 的字典
        """
        owner_name = bot.get("owner_name") or ""
        if not owner_name and operator:
            owner_name = operator.nick_name or operator.operator_name or operator.staff_id or ""
        bot_name = bot.get("bot_name") or bot.get("bot_id") or ""
        owner_id = bot.get("owner_id") or ""
        bot_id = bot.get("bot_id") or ""
        entity_id = bot.get("entity_id") or ""
        # owner_id 就是 user_id
        user_id = owner_id
        publish_hint = f"\"{owner_name}\"同学正在发布\"{bot_name}\"服务bot至AI工作台服务市场，审批通过后平台全体成员均可基于\"{owner_name}\"权限进行服务调用。在服务发布前，请您审核：该Agent是否涉及敏感数据处理或核心业务操作；如涉及，为避免造成由于权限扩散造成的不必要风险，建议您退回并重新选择为Caller 模式后发布。"
        # 获取 bot skills 并格式化为字符串
        bot_skills_str = ""
        try:
            skill_set_service = self._skill_set_factory.create(
                entity_id=entity_id, bot_id=bot_id
            )
            skill_sets = skill_set_service.get_all_skill_sets_with_skills(user_id=user_id, bolt_id=bot_id)
            skill_parts = []
            for skill_set in skill_sets:
                # 过滤掉默认技能集
                if skill_set.get("is_default"):
                    continue
                is_active = skill_set.get("is_active", False)
                set_name = skill_set.get("name", "")
                skills = skill_set.get("skills", [])
                if skills:
                    active_marker = "[激活]" if is_active else "[未激活]"
                    skill_parts.append(f"{set_name}{active_marker}:")
                    for s in skills:
                        name = s.get("name", "")
                        if not name:
                            continue
                        # 根据 is_public 决定拼接内容（兼容 bool 和 str）
                        is_public_val = s.get("is_public")
                        is_public = is_public_val in (True, "1", 1) if is_public_val is not None else False
                        if is_public:
                            # 公开：只显示 name
                            skill_parts.append(f"  - {name}")
                        else:
                            # 私有：显示 name 和 description
                            description = s.get("description", "")
                            skill_parts.append(f"  - {name}: {description}" if description else f"  - {name}")
            bot_skills_str = "\n".join(skill_parts) if skill_parts else "无"
        except Exception as e:
            logger.warning(f"[_build_public_approval_context] Failed to get skill sets for bot {bot_id}: {e}")
            bot_skills_str = "获取失败"
        # 获取 bot mcps 并格式化为字符串
        bot_mcps_str = ""
        try:
            skill_set_service = self._skill_set_factory.create(
                entity_id=entity_id, bot_id=bot_id
            )
            mcp_sets = skill_set_service.get_all_skill_sets_with_mcps(user_id=user_id, bolt_id=bot_id)
            mcp_parts = []
            for mcp_set in mcp_sets:
                is_active = mcp_set.get("is_active", False)
                set_name = mcp_set.get("name", "")
                mcps = mcp_set.get("mcps", [])
                mcp_names = [
                    m.get("name", "")
                    for m in mcps
                    if m.get("name")
                ]
                if mcp_names:
                    active_marker = "[激活]" if is_active else "[未激活]"
                    mcp_parts.append(f"{set_name}{active_marker}:")
                    for name in mcp_names:
                        mcp_parts.append(f"  - {name}")
            bot_mcps_str = "\n".join(mcp_parts) if mcp_parts else "无"
        except Exception as e:
            logger.warning(f"[_build_public_approval_context] Failed to get mcp sets for bot {bot_id}: {e}")
            bot_mcps_str = "获取失败"
        return {"publishHint": publish_hint, "botSkills": bot_skills_str, "botMcps": bot_mcps_str}

    def release_bot_for_others(
        self,
        target_user_id: str,
        target_bot_id: str,
        caller_user_id: str,
        caller_nick_name: str,
    ) -> Dict[str, Any]:
        """
        释放目标用户的指定 bot（管理员接口）。

        复用释放设备的逻辑，更新bot状态为FAILED。

        Args:
            target_user_id: 目标用户ID
            target_bot_id: 目标bot ID
            caller_user_id: 调用者用户ID（用于权限检查）
            caller_nick_name: 调用者昵称

        Returns:
            操作结果字典

        Raises:
            BotNotFoundError: 如果目标用户没有该 bot
            BotServiceError: 如果释放失败
        """
        logger.info(f"[bot_service.release_bot_for_others] Releasing bot {target_bot_id} for target {target_user_id}, caller {caller_user_id}")

        # 检查目标用户是否有该 bot
        if not self._repository.exists_by_owner_and_bot_id(target_user_id, target_bot_id):
            raise BotNotFoundError(f"目标用户 {target_user_id} 没有 bot '{target_bot_id}'")

        # 获取目标 bot
        total, items = self._repository.list_by_owner(target_user_id, page=1, page_size=100)
        target_bot = None
        for bot in items:
            if bot.get("bot_id") == target_bot_id:
                target_bot = bot
                break

        if not target_bot:
            raise BotNotFoundError(f"无法获取目标用户 {target_user_id} 的 bot '{target_bot_id}'")

        bot_id = target_bot.get("bot_id")
        binding_id = target_bot.get("binding_id")
        bot_status = target_bot.get("status", "UNKNOWN")

        logger.info(f"[bot_service.release_bot_for_others] Found bot {bot_id} with status {bot_status}, binding_id {binding_id}")

        # 【关键】无论bot状态如何，只要有binding且不是RELEASED，都应该尝试释放设备
        if binding_id:
            service = self._device_service_provider()
            binding = service.get_device(binding_id=binding_id)
            if binding and binding.status != DeviceBindingStatus.RELEASED.value:
                # ACTIVE、PENDING、FAILED 都需要释放，只有RELEASED跳过
                operator = _compose_operator_context(caller_user_id, caller_nick_name)
                try:
                    service.release_device(
                        binding_id=binding_id,
                        release_reason=f"Admin {caller_user_id} released bot {bot_id} for user {target_user_id}",
                        operator=operator,
                    )
                    logger.info(f"[bot_service.release_bot_for_others] Released device for binding {binding_id}")
                except Exception as e:
                    logger.error(f"[bot_service.release_bot_for_others] Failed to release device for binding {binding_id}: {e}")
                    raise BotServiceError(f"释放设备失败: {e}")
            else:
                logger.info(f"[bot_service.release_bot_for_others] Binding {binding_id} already RELEASED or not found, skipping device release")

        # 如果bot已经是FAILED状态，直接返回（设备已释放，不再重复更新状态）
        if bot_status == "FAILED":
            logger.info(f"[bot_service.release_bot_for_others] Bot {bot_id} is already FAILED, device released (if any), skipping status update")
            return {
                "action": "released",
                "bot_id": bot_id,
                "status": "FAILED",
                "target_user_id": target_user_id,
                "previous_status": bot_status,
                "message": "设备已释放，Bot已是FAILED状态",
            }

        # 更新bot状态为FAILED（调用者不是bot的owner，使用update_by_owner传入target_user_id）
        try:
            update_data = {
                "status": "FAILED",
                "binding_id": None,
                "device_id": None,
                "modifier_id": caller_user_id,
            }
            # 使用update_by_owner，传入target_user_id作为owner_id
            updated_bot = self._repository.update_by_owner(bot_id, target_user_id, update_data)
            if not updated_bot:
                raise BotNotFoundError(f"Bot not found or already released: {bot_id}")

            logger.info(f"[bot_service.release_bot_for_others] Successfully released bot {bot_id} for user {target_user_id}")

            return {
                "action": "released",
                "bot_id": bot_id,
                "status": "FAILED",
                "target_user_id": target_user_id,
                "previous_status": bot_status,
                "message": "Bot已成功释放",
            }

        except BotNotFoundError:
            raise
        except Exception as e:
            logger.error(f"[bot_service.release_bot_for_others] Failed to update bot status: {e}")
            raise BotServiceError(f"更新Bot状态失败: {e}")
