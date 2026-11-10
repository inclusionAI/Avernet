"""Bot public service implementation.

纯业务逻辑层，不包含任何 HTTP 关注点。
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from time import perf_counter
from typing import Any, Dict, List, Optional

from agentclaw.community.utils.avernet_tenant import (
    bind_current_avernet_tenant,
)
from agentclaw.community.core.bot_public.repository.models import BotFriendQueryKey, BotFriendStatus, ApprovalStatus, ApprovalType
from agentclaw.community.core.bot_public.catalog_metadata import (
    BotCatalogAddress,
    BotCatalogCaller,
    BotCatalogMetadata,
    BotCatalogMetadataUnavailableError,
    BotCatalogProfileNotFoundError,
    BotCatalogSearchFilters,
    BotCatalogSearchUnavailableError,
)

from .public_support import (
    logger,
    BotNotPublicError,
    BotPublicServiceError,
    BotNotFoundError,
)

class FriendCatalogActions:
    def create_friend_request_approval(
        self, bot_id: str, owner_id: str, operator_id: str, operator_name: str,
        approval_context: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Create friend request approval for a public bot.

        Args:
            bot_id: Bot ID
            owner_id: Owner ID
            operator_id: Operator user ID (applicant)
            operator_name: Operator user name (nick name)
            approval_context: Optional additional context for approval

        Returns:
            Approval result with success, puid, approval_url, error_msg

        Raises:
            BotPublicServiceError: If bot not found
        """
        logger.info(f"[create_friend_request_approval] Starting: bot_id={bot_id}, owner_id={owner_id}, operator_id={operator_id}")
        bot = self._bot_repository.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise BotPublicServiceError(f"Bot not found: bot_id={bot_id}, owner_id={owner_id}")

        # 获取bot的ext中的friend_approval配置，默认为0（不需要审批）
        bot_ext = bot.get("ext") or {}
        friend_approval = bot_ext.get("friend_approval", "0")

        # 先查询是否已存在好友关系
        existing_record = self._bot_friend_repo.get_by_entity_ids(
            requester_entity_id=operator_id,
            target_entity_id=owner_id,
            target_bot_id=bot_id
        )

        # 构建好友申请数据
        friend_data = {
            "requester_entity_id": operator_id,
            "requester_name": operator_name,
            "target_entity_id": owner_id,
            "target_name": bot.get("bot_name"),
            "target_bot_id": bot_id,
            "target_owner_name": bot.get("owner_name"),
        }

        if friend_approval == "0":
            # 无需审批，直接创建或更新好友关系
            if existing_record:
                # 已存在记录，更新状态为 ACCEPTED（如果之前不是）
                record = existing_record
                if existing_record.get("status") != BotFriendStatus.ACCEPTED:
                    record = self._bot_friend_repo._update_status_and_ext(
                        existing_record["id"],
                        BotFriendStatus.ACCEPTED,
                        existing_record.get("ext") or {}
                    )
                    logger.info(f"[create_friend_request_approval] Updated existing record to accepted: record_id={record.get('id')}")
                else:
                    logger.info(f"[create_friend_request_approval] Already accepted: record_id={record.get('id')}")
            else:
                # 不存在，插入新记录
                friend_data["status"] = BotFriendStatus.ACCEPTED
                record = self._bot_friend_repo.insert(friend_data)
                logger.info(f"[create_friend_request_approval] Auto accepted: bot_id={bot_id}, operator_id={operator_id}, record_id={record.get('id')}")

            return {
                "success": True,
                "auto_accepted": True,
                "bot_friend_id": record.get("id"),
                "message": "Friend request auto accepted",
            }
        else:
            # 需要审批，创建或更新记录
            if existing_record:
                # 已存在记录，更新状态为 PENDING 并清空审批单
                bot_friend_id = existing_record["id"]
                ext = existing_record.get("ext") or {}
                ext["approvals"] = []
                record = self._bot_friend_repo._update_status_and_ext(
                    bot_friend_id,
                    BotFriendStatus.PENDING,
                    ext
                )
                bot_friend_id = record.get("id")
                logger.info(f"[create_friend_request_approval] Updated existing record to pending and cleared approvals: bot_friend_id={bot_friend_id}")
            else:
                # 不存在，插入新记录
                friend_data["status"] = BotFriendStatus.PENDING
                record = self._bot_friend_repo.insert(friend_data)
                bot_friend_id = record.get("id")
                logger.info(f"[create_friend_request_approval] Created pending record: bot_friend_id={bot_friend_id}")

            # 创建审批单
            approval_result = self._create_friend_request_approval(
                bot, operator_id, operator_name, bot_friend_id, approval_context
            )

            if approval_result.get("success"):
                # 将审批信息存入BotFriend的ext属性
                approval_info = {
                    "uuid": approval_result.get("puid"),
                    "approval_type": ApprovalType.MANUAL,
                    "require_approval": True,
                    "approval_url": approval_result.get("approval_url"),
                    "status": ApprovalStatus.PENDING,
                }
                self._bot_friend_repo.create_approval(bot_friend_id, approval_info)
                logger.info(f"[create_friend_request_approval] Approval created: bot_friend_id={bot_friend_id}, puid={approval_result.get('puid')}")
            else:
                # 审批创建失败，记录错误日志
                logger.error(f"[create_friend_request_approval] Approval creation failed: bot_friend_id={bot_friend_id}, error={approval_result.get('error_msg')}")

            approval_result["bot_friend_id"] = bot_friend_id
            return approval_result

    def _create_friend_request_approval(
        self, bot: Dict[str, Any], operator_id: str, operator_name: str,
        bot_friend_id: int,
        approval_context: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Create friend request approval for a public bot.

        Args:
            bot: Bot object with 'id', 'public', 'owner_id' fields
            operator_id: Operator user ID (applicant)
            operator_name: Operator user name (nick name)
            bot_friend_id: Bot friend record ID
            approval_context: Optional additional context for approval

        Returns:
            Approval result with success, puid, approval_url, error_msg

        Raises:
            BotNotPublicError: If bot is not public
            BotPublicServiceError: If failed to create approval
        """
        bot_id = bot.get("bot_id", "unknown")
        owner_id = bot.get("owner_id")
        logger.info(f"[_create_friend_request_approval] Starting: bot_id={bot_id}, owner_id={owner_id}, operator_id={operator_id}, bot_friend_id={bot_friend_id}")

        # Check if bot is public
        if bot.get("public", "0") != "1":
            logger.warning(f"[_create_friend_request_approval] Bot is not public: bot_id={bot_id}, owner_id={owner_id}")
            raise BotNotPublicError(bot_id=bot_id)

        # Get required fields from bot
        bot_pk = bot.get("id")
        if not bot_pk:
            raise BotPublicServiceError(f"Bot missing required field 'id': bot_id={bot_id}, owner_id={owner_id}")
        if not owner_id:
            raise BotPublicServiceError(f"Bot missing required field 'owner_id': bot_id={bot_id}")

        # Build approval context and create approval
        owner_name = bot.get("owner_name") or owner_id
        bot_name = bot.get("bot_name") or bot_id
        bot_desc = bot.get("bot_desc")
        bot_name_with_desc = f"{bot_name}:{bot_desc}" if bot_desc else bot_name
        context = {
            "bot_friend_id": str(bot_friend_id),
            "bot_public_owner_audit": f"w[{owner_id}]",
            "owner_name": owner_name,
            "bot_name": bot_name_with_desc,
            "operator_name": operator_name or operator_id,
        }
        if approval_context:
            context.update(approval_context)

        try:
            approval_result = self._process_service.start_approval(
                applicant=operator_id,
                biz_id=f"{str(bot_friend_id)}{datetime.now().strftime('%Y%m%d%H%M%S')}",
                biz_type="botpublicfriends",
                process_code="teamclaw_bot_public_friends",
                context=context,
            )
            logger.info(f"[_create_friend_request_approval] Completed: bot_id={bot_id}, owner_id={owner_id}, success={approval_result.get('success')}")
            return approval_result
        except Exception as e:
            logger.error(f"[_create_friend_request_approval] Failed: {e}", exc_info=True)
            raise BotPublicServiceError(f"Failed to create approval: {e}")

    def handle_friend_request_approval_callback(
        self, puid: str, last_operate: str, bot_friend_id: int,
    ) -> Dict[str, Any]:
        """Handle friend request approval callback.

        Args:
            puid: Process unique ID
            last_operate: Last operation (AGREE/DISAGREE/CANCEL)
            bot_friend_id: Bot friend record ID

        Returns:
            Callback result with success and message
        """
        logger.info(f"[handle_friend_request_approval_callback] Received: puid={puid}, last_operate={last_operate}, bot_friend_id={bot_friend_id}")

        # 查询BotFriend记录
        friend_record = self._bot_friend_repo.get_by_id(bot_friend_id)
        if not friend_record:
            logger.error(f"[handle_friend_request_approval_callback] BotFriend record not found: bot_friend_id={bot_friend_id}")
            return {"success": False, "message": f"BotFriend record not found: bot_friend_id={bot_friend_id}"}

        last_operate_upper = (last_operate or "").upper()

        if last_operate_upper == "AGREE":
            # 接受好友申请
            updated_record = self._bot_friend_repo.accept(bot_friend_id, approval_uuid=puid)
            if updated_record:
                self._create_auth_relationship_for_approval(friend_record, bot_friend_id)
                logger.info(f"[handle_friend_request_approval_callback] Approval agreed: bot_friend_id={bot_friend_id}")
                return {"success": True, "message": "Approval agreed", "bot_friend": updated_record}
            else:
                logger.error(f"[handle_friend_request_approval_callback] Failed to accept: bot_friend_id={bot_friend_id}")
                return {"success": False, "message": "Failed to update friend record"}
        elif last_operate_upper == "DISAGREE":
            # 拒绝好友申请
            updated_record = self._bot_friend_repo.reject(bot_friend_id, approval_uuid=puid)
            if updated_record:
                logger.info(f"[handle_friend_request_approval_callback] Approval disagreed: bot_friend_id={bot_friend_id}")
                return {"success": True, "message": "Approval disagreed", "bot_friend": updated_record}
            else:
                logger.error(f"[handle_friend_request_approval_callback] Failed to reject: bot_friend_id={bot_friend_id}")
                return {"success": False, "message": "Failed to update friend record"}
        elif last_operate_upper == "CANCEL":
            # 取消好友申请
            updated_record = self._bot_friend_repo.cancel(bot_friend_id, approval_uuid=puid)
            if updated_record:
                logger.info(f"[handle_friend_request_approval_callback] Approval canceled: bot_friend_id={bot_friend_id}")
                return {"success": True, "message": "Approval canceled", "bot_friend": updated_record}
            else:
                logger.error(f"[handle_friend_request_approval_callback] Failed to cancel: bot_friend_id={bot_friend_id}")
                return {"success": False, "message": "Failed to update friend record"}
        else:
            logger.warning(f"[handle_friend_request_approval_callback] Unknown operation: {last_operate}")
            return {"success": False, "message": f"Unknown operation: {last_operate}"}

    def _create_auth_relationship_for_approval(self, friend_record: dict[str, Any], bot_friend_id: int) -> None:
        """为审批通过的好友申请创建授权关系，失败直接传播。

        流程：
        1. 查 bot 并确认当前是 RESTRICTED 模式（friend_approval=1）。
           OPEN 模式跳过，因为公开 Bot 不需要显式授权。
        2. 取 agent_code 并调用授权关系服务创建单条授权关系。

        仅 OPEN 模式无需显式授权，其他失败都会阻断审批回调完成。
        """
        bot_id = friend_record.get("target_bot_id")
        operator_id = friend_record.get("requester_entity_id")
        if not bot_id or not operator_id:
            raise BotPublicServiceError(
                "好友审批缺少授权关系所需字段: "
                f"bot_friend_id={bot_friend_id}"
            )

        bot = self._bot_repository.get_by_id_and_owner(
            bot_id,
            friend_record.get("target_entity_id"),
        )
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        # 判断 Bot 当前是否为 RESTRICTED 模式，OPEN 模式不需要授权关系
        bot_ext = bot.get("ext") or {}
        if isinstance(bot_ext, str):
            bot_ext = json.loads(bot_ext)
        friend_approval = bot_ext.get("friend_approval", "0")
        if friend_approval != "1":
            logger.info(f"[_create_auth_relationship_for_approval] Skipped (OPEN mode): bot_id={bot_id}, operator_id={operator_id}")
            return

        from agentclaw.community.core.bot_management.utils import resolve_agent_code
        agent_code = resolve_agent_code(
            bot=bot,
            passport_plugin=self._passport_plugin,
        ) or None
        if not agent_code:
            raise BotPublicServiceError(
                f"agent_code 为空，无法创建授权关系: bot_id={bot_id}"
            )

        owner_id = bot.get("owner_id")
        owner_name = bot.get("owner_name")
        result = self._auth_relationship_plugin.create_relationship(
            work_no=operator_id,
            agent_code=agent_code,
            description="Authorized by TeamClaw via friend request approval",
            operator_work_no=owner_id,
            operator_name=owner_name,
        )
        if result:
            if result.get("already_exists"):
                logger.info(f"[_create_auth_relationship_for_approval] Already exists: bot_id={bot_id}, operator_id={operator_id}")
            else:
                logger.info(f"[_create_auth_relationship_for_approval] Created: bot_id={bot_id}, operator_id={operator_id}, auth_id={result.get('auth_id')}")
        else:
            raise BotPublicServiceError(
                "授权关系服务返回失败: "
                f"bot_id={bot_id}, operator_id={operator_id}"
            )

    def search_public_bots_by_keyword(
        self,
        user_id: str | None = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """根据关键词分页查询公开 Bot。

        Args:
            user_id: 当前用户ID
            search: 搜索关键词，模糊匹配 owner_name 或 bot_name (可选)
            page: 页码，从1开始
            page_size: 每页数量

        Returns:
            分页结果 {"total": int, "items": list}，每个 item 可能包含:
            - friend_record_approval: 好友申请记录 (有申请记录时)
        """
        logger.info(f"[search_public_bots_by_keyword] user_id={user_id}, search={search}, page={page}, page_size={page_size}")
        public_bot_result = self._bot_service.list_bots_by_search(public="1", search=search, page=page, page_size=page_size)

        items = public_bot_result.get("items", [])
        if not items:
            return public_bot_result

        # Sanitize sensitive fields to avoid leaking them through the public search API.
        EXT_SENSITIVE_KEYS = {"iam_token"}
        for bot in items:
            # Redact top-level device_id
            if "device_id" in bot:
                bot["device_id"] = None
            # Redact sensitive fields inside ext
            ext = bot.get("ext")
            if isinstance(ext, str):
                try:
                    ext = json.loads(ext)
                except json.JSONDecodeError:
                    ext = {}
            if isinstance(ext, dict):
                if isinstance(ext.get("passport"), dict) and "token" in ext["passport"]:
                    ext["passport"]["token"] = None
                for key in EXT_SENSITIVE_KEYS:
                    if key in ext:
                        ext[key] = None
                bot["ext"] = ext

        # 查询好友申请记录。公开 catalog 不携带用户作用域，因此不读取关系数据。
        if not user_id:
            return public_bot_result

        query_keys: list[BotFriendQueryKey] = []
        for bot in items:
            bot_id = bot.get("bot_id")
            if bot_id and bot.get("owner_id"):
                query_keys.append(BotFriendQueryKey(
                    requester_entity_id=user_id,
                    target_entity_id=bot.get("owner_id"),
                    target_bot_id=bot_id,
                ))

        if query_keys:
            try:
                friend_records = self._bot_friend_repo.get_by_entity_ids_batch(keys=query_keys)
                friend_map = {f"{r['requester_entity_id']}:{r['target_bot_id']}:{r['target_entity_id']}": r for r in friend_records}
                for bot in items:
                    bot["friend_record_approval"] = friend_map.get(f"{user_id}:{bot.get('bot_id')}:{bot.get('owner_id')}")
            except Exception as e:
                logger.warning(f"[search_public_bots_by_keyword] Failed to query friend records: {e}")

        return public_bot_result

    def search_catalog_public_bots_by_keyword(
        self,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        *,
        caller: BotCatalogCaller,
        request_id: str,
        filters: BotCatalogSearchFilters | None = None,
    ) -> Dict[str, Any]:
        from .catalog_pagination import online_catalog_page

        return online_catalog_page(
            self._search_catalog_page, search=search, page=page, page_size=page_size,
            caller=caller, request_id=request_id, filters=filters,
        )

    def _search_catalog_page(
        self,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        *,
        caller: BotCatalogCaller,
        request_id: str,
        filters: BotCatalogSearchFilters | None = None,
    ) -> Dict[str, Any]:
        """Return the current BCS catalog page enriched with Backend Bot data."""
        try:
            metadata_kwargs: dict[str, Any] = {
                "search": search,
                "page": page,
                "page_size": page_size,
                "caller": caller,
                "request_id": request_id,
            }
            if filters is not None:
                metadata_kwargs["filters"] = filters
            metadata_page = self._catalog_metadata_service.search_public_bot_metadata(
                **metadata_kwargs
            )
            metadata = metadata_page.items
            addresses = self._validated_catalog_addresses(metadata)
            metadata_by_address = {item.address: item for item in metadata}
        except BotCatalogMetadataUnavailableError as exc:
            logger.warning(
                "[BotPublicService.catalog_search] request_id=%s "
                "failure=metadata_unavailable",
                request_id,
            )
            raise BotCatalogSearchUnavailableError() from exc
        except Exception as exc:  # noqa: BLE001 - metadata is fail-closed
            logger.warning(
                "[BotPublicService.catalog_search] request_id=%s failure=invalid_metadata",
                request_id,
            )
            raise BotCatalogSearchUnavailableError() from exc
        join_started = perf_counter()
        backend_bots = self._bot_repository.list_catalog_bots_by_owner_bot_pairs(
            [(address.bot_id, address.entity_id) for address in addresses],
        )
        join_done = perf_counter()
        bots_by_address = {
            address: bot
            for bot in backend_bots
            if (address := self._catalog_address(bot)) in addresses
        }
        items = []
        bcs_only_count = 0
        description_fallback_count = 0
        entity_fallback_count = 0
        for address in addresses:
            bot = bots_by_address.get(address)
            metadata_item = metadata_by_address[address]
            if bot is None:
                entity_id = metadata_item.created_by or address.entity_id
                bot = {
                    "bot_id": address.bot_id,
                    "bot_uuid": metadata_item.bot_uuid,
                    "entity_id": entity_id,
                    "owner_id": entity_id,
                    "bot_type": "",
                    "bot_name": metadata_item.name or "",
                    "bot_desc": metadata_item.summary or "",
                    "owner_name": None,
                    "active_engine": "",
                    "status": metadata_item.status or "",
                }
                bcs_only_count += 1
            else:
                backend_description = bot.get("bot_desc")
                if (
                    metadata_item.summary is not None
                    and (
                        not isinstance(backend_description, str)
                        or not backend_description.strip()
                    )
                ):
                    bot["bot_desc"] = metadata_item.summary
                    description_fallback_count += 1
                backend_entity_id = bot.get("entity_id")
                if not isinstance(backend_entity_id, str) or not backend_entity_id.strip():
                    bot["entity_id"] = metadata_item.created_by or address.entity_id
                    entity_fallback_count += 1
            # COSEC: The adapter validated this public BCS address; request
            # identity is never used as a Bot owner fallback.
            bot["bot_uuid"] = metadata_item.bot_uuid or (
                f"{address.bot_id}:{address.entity_id}"
            )
            for field_name in (
                "is_friend",
                "visibility",
                "is_online",
                "actor_kind",
                "friend_ext",
                "friend_check_in_strategy",
                "user_visibility",
            ):
                value = getattr(metadata_item, field_name)
                if value is not None:
                    bot[field_name] = value
            items.append(bot)
        # Sanitize sensitive fields before pagination and public projection.
        EXT_SENSITIVE_KEYS = {"iam_token"}
        for bot in items:
            if "device_id" in bot:
                bot["device_id"] = None
            ext = bot.get("ext")
            if isinstance(ext, str):
                try:
                    ext = json.loads(ext)
                except json.JSONDecodeError:
                    ext = {}
            if isinstance(ext, dict):
                if isinstance(ext.get("passport"), dict) and "token" in ext["passport"]:
                    ext["passport"]["token"] = None
                for key in EXT_SENSITIVE_KEYS:
                    if key in ext:
                        ext[key] = None
                bot["ext"] = ext
        total = metadata_page.total
        logger.info(
            "[BotPublicService.catalog_search] request_id=%s bcs_count=%s "
            "result_count=%s bcs_only_count=%s description_fallback_count=%s "
            "entity_fallback_count=%s total=%s join_ms=%.1f projection_ms=%.1f",
            request_id,
            len(addresses),
            len(items),
            bcs_only_count,
            description_fallback_count,
            entity_fallback_count,
            total,
            (join_done - join_started) * 1000,
            (perf_counter() - join_done) * 1000,
        )
        return {"total": total, "items": items}

    def get_catalog_public_bot_profile(
        self,
        bot_uuid: str,
        *,
        caller: BotCatalogCaller,
        request_id: str,
    ) -> Dict[str, Any]:
        """Return the public profile of one catalog Bot for the friend-request dialog.

        公开性由 BCS catalog 精确裁决（``bot_uuids`` 命中才可见）；名称/引擎等
        摘要 Backend 侧记录补充，已公开能力取 Bot 能力集配置的 MCP 列表。
        """
        normalized_uuid = (bot_uuid or "").strip()
        if not normalized_uuid:
            raise BotCatalogProfileNotFoundError(bot_uuid)
        try:
            metadata_page = self._catalog_metadata_service.search_public_bot_metadata(
                search=None,
                page=1,
                page_size=1,
                bot_uuids=(normalized_uuid,),
                caller=caller,
                request_id=request_id,
            )
        except BotCatalogMetadataUnavailableError as exc:
            logger.warning(
                "[BotPublicService.catalog_profile] request_id=%s failure=metadata_unavailable",
                request_id,
            )
            raise BotCatalogSearchUnavailableError() from exc
        except Exception as exc:  # noqa: BLE001 - catalog metadata is fail-closed
            logger.warning(
                "[BotPublicService.catalog_profile] request_id=%s failure=invalid_metadata",
                request_id,
            )
            raise BotCatalogSearchUnavailableError() from exc
        if not metadata_page.items:
            raise BotCatalogProfileNotFoundError(normalized_uuid)
        metadata = metadata_page.items[0]
        address = metadata.address
        _, backend_bots = self._bot_repository.list_bots_by_owner_bot_pairs(
            [(address.bot_id, address.entity_id)],
            page=1,
            page_size=1,
        )
        bot = backend_bots[0] if backend_bots else None
        capabilities = self._published_capabilities(
            bot=bot,
            bot_id=address.bot_id,
            entity_id=address.entity_id,
        )
        skills = self._published_skills(
            bot=bot,
            bot_id=address.bot_id,
            entity_id=address.entity_id,
        )
        profile = {
            "bot_id": address.bot_id,
            "bot_uuid": normalized_uuid,
            # Backend 记录优先，BCS 元数据兜底（与 catalog search 的回退口径一致）。
            "bot_name": str(
                (bot and bot.get("bot_name")) or metadata.name or address.bot_id
            ),
            "owner_name": (bot and bot.get("owner_name")) or None,
            "description": str(
                (bot and bot.get("bot_desc") or None) or metadata.summary or ""
            ),
            "engine_name": str((bot and bot.get("active_engine")) or ""),
            "capabilities": capabilities,
            "skills": skills,
        }
        logger.info(
            "[BotPublicService.catalog_profile] request_id=%s bot_uuid=%s "
            "capability_count=%s skill_count=%s backend_join=%s",
            request_id,
            normalized_uuid,
            len(capabilities),
            len(skills),
            bot is not None,
        )
        return profile

    def _published_capabilities(
        self,
        *,
        bot: Optional[Dict[str, Any]],
        bot_id: str,
        entity_id: str,
    ) -> List[Dict[str, str]]:
        """已公开能力 = Bot 能力集配置的 MCP 列表（capability_id + display_name，去重）。

        能力集服务失败只降级为空列表：能力清单纯展示用途，不允许阻断画像返回。
        """
        if bot is None:
            # Backend 无记录（BCS-only）：能力数据在 Backend 能力集表中，无源可查。
            return []
        owner_id = str(
            bot.get("owner_id") or bot.get("entity_id") or entity_id or ""
        ).strip()
        bot_key = str(bot.get("bot_id") or bot_id or "").strip()
        if not owner_id or not bot_key:
            return []
        try:
            skill_set_service = self._skill_set_service_factory.create(
                entity_id=entity_id or owner_id, bot_id=bot_key
            )
            mcp_sets = skill_set_service.get_all_skill_sets_with_mcps(
                user_id=owner_id, bolt_id=bot_key
            )
        except Exception as exc:  # noqa: BLE001 - capability list is display-only
            logger.warning(
                "[BotPublicService.catalog_profile] capabilities degraded "
                "bot_id=%s owner_id=%s: %s",
                bot_key,
                owner_id,
                exc,
            )
            return []
        capabilities: List[Dict[str, str]] = []
        seen: set[str] = set()
        if not isinstance(mcp_sets, list):
            return []
        for mcp_set in mcp_sets:
            if not isinstance(mcp_set, dict):
                continue
            mcps = mcp_set.get("mcps")
            if not isinstance(mcps, list):
                continue
            for mcp in mcps:
                if not isinstance(mcp, dict):
                    continue
                display_name = str(mcp.get("name") or "").strip()
                capability_id = str(
                    mcp.get("id") or mcp.get("server_code") or display_name
                ).strip()
                if not display_name or not capability_id:
                    continue
                if capability_id in seen:
                    continue
                seen.add(capability_id)
                capabilities.append(
                    {
                        "capability_id": capability_id,
                        "display_name": display_name,
                    }
                )
        return capabilities

    def _published_skills(
        self,
        *,
        bot: Optional[Dict[str, Any]],
        bot_id: str,
        entity_id: str,
    ) -> List[Dict[str, str]]:
        """已公开技能 = Bot 能力集配置的技能列表（skill_id + display_name，去重）。

        与 MCP 清单同口径：所有能力集（激活/非激活）并集去重，数据源为
        ``get_all_skill_sets_with_skills``（每个能力集的 ``skills`` 列表）。
        能力集服务失败只降级为空列表：技能清单纯展示用途，不允许阻断画像返回。
        """
        if bot is None:
            # Backend 无记录（BCS-only）：技能数据在 Backend 能力集表中，无源可查。
            return []
        owner_id = str(
            bot.get("owner_id") or bot.get("entity_id") or entity_id or ""
        ).strip()
        bot_key = str(bot.get("bot_id") or bot_id or "").strip()
        if not owner_id or not bot_key:
            return []
        try:
            skill_set_service = self._skill_set_service_factory.create(
                entity_id=entity_id or owner_id, bot_id=bot_key
            )
            skill_sets = skill_set_service.get_all_skill_sets_with_skills(
                user_id=owner_id, bolt_id=bot_key
            )
        except Exception as exc:  # noqa: BLE001 - skill list is display-only
            logger.warning(
                "[BotPublicService.catalog_profile] skills degraded "
                "bot_id=%s owner_id=%s: %s",
                bot_key,
                owner_id,
                exc,
            )
            return []
        skills: List[Dict[str, str]] = []
        seen: set[str] = set()
        if not isinstance(skill_sets, list):
            return []
        for skill_set in skill_sets:
            if not isinstance(skill_set, dict):
                continue
            entries = skill_set.get("skills")
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                display_name = str(entry.get("name") or "").strip()
                skill_id = str(entry.get("id") or display_name).strip()
                if not display_name or not skill_id:
                    continue
                if skill_id in seen:
                    continue
                seen.add(skill_id)
                skills.append(
                    {
                        "skill_id": skill_id,
                        "display_name": display_name,
                    }
                )
        return skills

    @staticmethod
    def _catalog_address(bot: dict[str, Any]) -> BotCatalogAddress | None:
        bot_id = bot.get("bot_id")
        entity_id = bot.get("entity_id") or bot.get("owner_id")
        if not isinstance(bot_id, str) or not bot_id.strip():
            return None
        if not isinstance(entity_id, str) or not entity_id.strip():
            return None
        return BotCatalogAddress(bot_id=bot_id, entity_id=entity_id)

    @staticmethod
    def _validated_catalog_addresses(
        metadata: Any,
    ) -> tuple[BotCatalogAddress, ...]:
        addresses: list[BotCatalogAddress] = []
        seen: set[BotCatalogAddress] = set()
        for item in metadata:
            if not isinstance(item, BotCatalogMetadata) or item.kind != "bot":
                raise BotCatalogMetadataUnavailableError()
            address = item.address
            if (
                not isinstance(address, BotCatalogAddress)
                or not address.bot_id.strip()
                or not address.entity_id.strip()
                or address in seen
            ):
                raise BotCatalogMetadataUnavailableError()
            seen.add(address)
            addresses.append(address)
        return tuple(addresses)

    def list_my_bot_friends(
        self,
        user_id: str,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """查询当前用户的审批通过的好友 Bot 列表。

        Args:
            user_id: 当前用户ID
            search: 搜索关键词，模糊匹配 owner_name 或 bot_name (可选)
            page: 页码，从1开始
            page_size: 每页数量

        Returns:
            分页结果 {"total": int, "items": list}
        """
        logger.info(f"[list_my_bot_friends] user_id={user_id}, search={search}, page={page}, page_size={page_size}")

        # 查询用户作为申请方的审批通过的好友记录
        # search 同时匹配 target_name（目标方姓名/bot名称）和 target_owner_name（拥有者花名）
        total, records = self._bot_friend_repo.list_by_requester(
            requester_entity_id=user_id,
            status=BotFriendStatus.ACCEPTED,
            target_name=search,
            target_owner_name=search,
            page=page,
            page_size=page_size,
        )

        # 根据 owner_id (target_entity_id) 和 bot_id (target_bot_id) 并发查询 bot 信息
        if records:
            # 收集唯一的 (owner_id, bot_id) 组合
            unique_keys = set()
            for record in records:
                owner_id = record.get("target_entity_id")
                bot_id = record.get("target_bot_id")
                unique_keys.add((owner_id, bot_id))

            # 并发查询 bot 信息
            bot_map = {}
            # ThreadPoolExecutor workers don't copy context vars; bind the
            # request tenant so these BotRepository reads stay tenant-scoped.
            _get_bot = bind_current_avernet_tenant(
                self._bot_repository.get_by_id_and_owner
            )
            with ThreadPoolExecutor(max_workers=10) as executor:
                future_to_key = {
                    executor.submit(_get_bot, bot_id, owner_id): (owner_id, bot_id)
                    for owner_id, bot_id in unique_keys
                }
                for future in as_completed(future_to_key):
                    owner_id, bot_id = future_to_key[future]
                    try:
                        bot = future.result()
                        bot_map[f"{owner_id}:{bot_id}"] = bot
                    except Exception as e:
                        logger.warning(f"[list_my_bot_friends] Failed to get bot: owner_id={owner_id}, bot_id={bot_id}, error={e}")

            # 将 bot 信息添加到记录中
            for record in records:
                owner_id = record.get("target_entity_id")
                bot_id = record.get("target_bot_id")
                record["bot"] = bot_map.get(f"{owner_id}:{bot_id}")

        return {
            "total": total,
            "items": records,
        }

    def get_friend_record(
        self,
        user_id: str,
        target_entity_id: str,
        target_bot_id: str,
    ) -> Optional[Dict[str, Any]]:
        """查询当前用户与目标 Bot 的好友关系记录。

        返回结果中额外包含目标 Bot 的公开状态信息（bot_access_info），
        方便前端判断该 Bot 的访问模式（公开/私人/受限访问）。

        Args:
            user_id: 当前用户ID（申请方）
            target_entity_id: 目标方实体ID
            target_bot_id: 目标Bot ID

        Returns:
            好友记录信息，额外包含 bot_access_info 字段；不存在则返回 None
        """
        logger.info(f"[get_friend_record] user_id={user_id}, target_entity_id={target_entity_id}, target_bot_id={target_bot_id}")

        friend_record = self._bot_friend_repo.get_by_entity_ids(
            requester_entity_id=user_id,
            target_entity_id=target_entity_id,
            target_bot_id=target_bot_id,
        )
        if not friend_record:
            logger.info(f"[get_friend_record] No friend record found: user_id={user_id}, target_entity_id={target_entity_id}, target_bot_id={target_bot_id}")
            return None

        # 补充目标 Bot 的公开状态信息
        bot = self._bot_repository.get_by_id_and_owner(target_bot_id, target_entity_id)
        if not bot:
            raise ValueError(f"Bot not found: bot_id={target_bot_id}, owner_id={target_entity_id}")

        try:
            bot_ext = bot.get("ext") or {}
            if isinstance(bot_ext, str):
                bot_ext = json.loads(bot_ext)
            friend_record["bot_access_info"] = {
                "public": bot.get("public", "0"),
                "friend_approval": bot_ext.get("friend_approval", "0"),
            }
            # 返回整个 bot 对象
            friend_record["bot"] = bot
            logger.info(f"[get_friend_record] Bot access info attached: bot_id={target_bot_id}, owner_id={target_entity_id}, public={friend_record['bot_access_info']['public']}, friend_approval={friend_record['bot_access_info']['friend_approval']}")
        except Exception as e:
            logger.error(f"[get_friend_record] Failed to build bot access info: bot_id={target_bot_id}, owner_id={target_entity_id}, error={e}")
            raise

        return friend_record
