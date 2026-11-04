"""
Bot get/list/search queries and the desktop live-status merge (including the
display-status class constant) — moved verbatim from ``bot_service.py``.

Part of the mechanical split of ``bot_service.py``: ``BotQueriesMixin`` members were
moved here verbatim and the facade composes them onto ``BotService``.
"""

from typing import Any, Dict, List, Optional

from agentclaw.community.core.desktop_bot.status_mapping import map_baas_to_display

from .common import (
    BotNotFoundError,
    logger,
)


class BotQueriesMixin:
    """Bot get/list/search queries and the desktop live-status merge (including the
    display-status constant ``_MERGE_SKIP_LOCAL_STATUSES``)."""

    def get_bot_by_address(self, bot_id: str, user_id: str, *, source_bot_pk: int | None = None) -> Dict[str, Any]:
        """Internal resource lookup; user_id is a legacy entity/Owner locator.

        This method does not authorize the actor. Callers must authorize separately;
        get_bot retains its strict current-Owner lookup contract.
        Publication callers provide source_bot_pk so historical Owner values do
        not have to match a current Owner or entity. Ordinary locators omit it.
        """
        from agentclaw.community.core.bot_management.addressing import BotAddressResolver
        from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError

        try:
            resolver = BotAddressResolver(self._repository)
            if source_bot_pk is not None:
                address = resolver.resolve_publish_source(
                    source_bot_pk=source_bot_pk, source_bot_id=bot_id, legacy_owner_id=user_id,
                )
            else:
                address = resolver.resolve(bot_id=bot_id, legacy_owner_id=user_id)
        except BotAddressNotFoundError as exc:
            raise BotNotFoundError(f"Bot not found: {bot_id}") from exc
        return self.get_bot(bot_id=bot_id, user_id=address.current_owner_id)

    def get_bot(self, bot_id: str, user_id: str) -> Dict[str, Any]:
        """
        Get bot by ID.

        Args:
            bot_id: Bot ID
            user_id: User ID for permission check (must be the owner)

        Returns:
            Bot record

        Raises:
            BotNotFoundError: If bot not found
        """
        # Use get_by_id_and_owner to ensure we only get the owner's bot
        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        # Also fetch binding info from ac_entity_device_binding if exists
        binding_id = bot.get("binding_id")
        if binding_id:
            try:
                service = self._device_service_provider()
                binding = service.get_device(binding_id=binding_id)
                if binding:
                    bot["device_binding"] = binding.to_dict()
            except Exception as e:
                logger.warning(f"[bot_service.get_bot] Failed to get device binding {binding_id}: {e}")

            # NOTE: teclaw bots no longer read status through to baas here — the
            # TeclawPublishTaskHandler persists the resolved status onto the stored
            # column post-provision, so the bot row is authoritative for all
            # engines (see the durable Teclaw publish task lifecycle).

        # Fetch template info if exists
        try:
            template = self._template_service.get_template(bot_id)
            if template:
                bot["template_config"] = template.get("ext")
        except Exception as e:
            logger.warning(f"[bot_service.get_bot] Failed to get template for bot {bot_id}: {e}", exc_info=True)

        return bot

    def get_bot_pk(self, bot_id: str, user_id: str) -> int:
        """Return ``ac_bots.id`` for the owner-scoped bot, or raise.

        The narrow sibling of :meth:`get_bot`, for callers that need only the
        primary key downstream tables are keyed on (``ac_bot_collaborator``,
        ``ac_bot_publish.source_bot_pk``). ``get_bot`` also fetches the device
        binding and the template — a provider call and a second query — and a
        caller that wants an int should not pay for either.

        Resolved through the same ``get_by_id_and_owner`` :meth:`get_bot` uses,
        so it returns the primary key of the row that method would have
        returned. It states no new identity: a caller re-reading the key of a
        bot it already resolved gets the same row back, not a wider match.

        Returns the key the row carries, which is ``0`` only if the projection
        lost it — ``ac_bots.id`` is a non-nullable autoincrement column, so a
        live row always has one. The refusal for a keyless row belongs to the
        caller, whose own answer for "no safe key" is already established:
        ``engine_runtime.stage.resolve_stage_bind_id`` raises
        ``DeviceNotBoundError`` before it queries anything. Raising a different
        error here would change that caller-visible answer.

        Raises:
            BotNotFoundError: no live bot with this ``(bot_id, user_id)`` —
                the bot is gone, which is not the same as a row whose key did
                not survive projection.
        """
        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        return int(bot.get("id") or 0)

    def list_coding_bots_by_architect(self, architect_bot_id: str) -> List[Dict[str, Any]]:
        """List application coding bots associated with a domain architect bot.

        A domain architect bot is identified by ac_bots.ext.is_domain_bot == true.
        Application coding bots are linked to the architect bot via
        ac_templates.ext.architect_bot_id.

        Args:
            architect_bot_id: The architect bot's bot_id

        Returns:
            List of bot records that are application coding bots
            associated with the given architect bot
        """
        templates = self._template_service.list_templates_by_architect_bot_id(architect_bot_id)
        if not templates:
            return []

        # Collect bot_ids from templates and fetch bot records
        # Build a mapping from bot_id to template ext (template_config)
        template_ext_map = {}
        for template in templates:
            bot_id = template.get("bot_id")
            if bot_id:
                template_ext_map[bot_id] = template.get("ext")

        coding_bots = []
        for template in templates:
            bot_id = template.get("bot_id")
            if not bot_id:
                continue
            try:
                # list_by_conditions with bot_id returns (total, [items])
                total, items = self._repository.list_by_conditions(bot_id=bot_id, page=1, page_size=1)
                if items:
                    bot = items[0]
                    # Attach template_config (ext field from ac_templates) to bot record
                    ext = template_ext_map.get(bot_id)
                    if ext is not None:
                        bot["template_config"] = ext
                    # Attach bot member (collaborator) information. The owner
                    # is intentionally NOT included here: it lives on the bot
                    # record itself (owner_id / owner_name); only added admin/
                    # member collaborators are returned. Failure is non-fatal:
                    # an empty list is attached so enrichment never breaks the
                    # coding-bots list.
                    bot["members"] = self._list_bot_members(
                        bot_id=bot_id,
                        owner_id=bot.get("owner_id"),
                    )
                    coding_bots.append(bot)
            except Exception as e:
                logger.warning(
                    "[bot_service.list_coding_bots_by_architect] Failed to get bot %s: %s",
                    bot_id, e,
                )
                continue

        return coding_bots

    def _attach_template_configs_to_bots(self, items: List[Dict[str, Any]]) -> None:
        """Attach ac_templates.ext as template_config for bot list items in batch.

        create/get/update paths store template details in ac_templates.ext while
        list repository methods read only ac_bots fields.  Keep list APIs
        consistent with get_bot() by enriching template-backed bots here.
        Best-effort: template lookup failure must not break bot lists.
        """
        if not items:
            return

        bot_ids = list(dict.fromkeys(
            str(bot.get("bot_id"))
            for bot in items
            if bot.get("bot_id") and bot.get("template_type")
        ))
        if not bot_ids:
            return

        try:
            templates = self._template_service.list_templates_by_bot_ids(bot_ids)
            ext_by_bot_id = {
                str(template.get("bot_id")): template.get("ext")
                for template in templates
                if template.get("bot_id") and template.get("ext") is not None
            }
            if not ext_by_bot_id:
                return
            for bot in items:
                bot_id = bot.get("bot_id")
                if bot_id is None:
                    continue
                ext = ext_by_bot_id.get(str(bot_id))
                if ext is not None:
                    bot["template_config"] = ext
        except Exception as e:
            logger.warning(
                "[bot_service._attach_template_configs_to_bots] Failed to attach template configs: %s",
                e, exc_info=True,
            )

    def list_bots(
        self,
        entity_id: Optional[str] = None,
        entity_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """
        List bots with pagination.

        Args:
            entity_id: Filter by entity ID (optional)
            entity_type: Filter by entity type (optional)
            page: Page number (1-based)
            page_size: Items per page

        Returns:
            Dictionary with 'total' and 'items' keys
        """
        total, items = self._repository.list_by_entity(
            entity_id=entity_id,
            entity_type=entity_type,
            page=page,
            page_size=page_size,
        )

        # Note: Device binding info is stored in ac_entity_device_binding table
        # To avoid N+1 queries, we don't fetch binding info for list operations
        # Use get_bot() to get detailed info including device_binding
        self._attach_template_configs_to_bots(items)

        return {
            "total": total,
            "items": items,
        }

    def list_bots_by_conditions(
        self,
        public: Optional[str] = None,
        bot_name: Optional[str] = None,
        owner_name: Optional[str] = None,
        bot_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        engine: Optional[str] = None,
        status: Optional[str] = None,
        space_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
        bot_ids: Optional[List[str]] = None,
        attach_templates: bool = True,
    ) -> Dict[str, Any]:
        """
        List bots by conditions with pagination.

        Args:
            public: Filter by public status ("0" or "1")
            bot_name: Filter by bot name (fuzzy search)
            owner_name: Filter by owner name
            bot_id: Filter by bot ID (exact match)
            owner_id: Filter by owner id (exact match) — scopes to one owner
            space_id: Filter by numeric business-space id (exact match)
            engine: Filter by active engine (exact match)
            status: Filter by lifecycle status (exact match)
            page: Page number (1-based)
            page_size: Items per page
            bot_ids: Restrict to this explicit set. ``None`` means unrestricted;
                an empty list means none. The distinction is load-bearing —
                treating empty as unrestricted would show a caller entitled to
                nothing everything.
            attach_templates: Attach ac_templates.ext as template_config to
                each returned bot (the get/list consistency contract). Pass
                False only when the caller provably never reads that field.

        Returns:
            Dictionary with 'total' and 'items' keys
        """
        total, items = self._repository.list_by_conditions(
            public=public,
            bot_name=bot_name,
            owner_name=owner_name,
            bot_id=bot_id,
            owner_id=owner_id,
            engine=engine,
            status=status,
            space_id=space_id,
            page=page,
            page_size=page_size,
            bot_ids=bot_ids,
        )
        # Callers that never surface template_config (the bot inventory pulls
        # every matching row) pay one batched template read per page for data
        # they drop; they opt out here. Default stays True for the established
        # list/detail consistency contract.
        if attach_templates:
            self._attach_template_configs_to_bots(items)
        return {
            "total": total,
            "items": items,
        }

    def get_bot_by_id(self, bot_id: str) -> Optional[Dict[str, Any]]:
        """按 bot_id 单查 bot 详情(owner_id / bot_name 等),不限 caller/owner;查不到返 ``None``。

        供内部读侧用——例如 dashboard 给任务节点 ``assignee`` 附加归属(owner_id)/名称(bot_name),
        不需 owner 权限校验。复用 ``list_bots_by_conditions(bot_id=...)`` 精确单查,取 ``items[0]``。"""
        page = self.list_bots_by_conditions(bot_id=bot_id, page=1, page_size=1)
        items = (page or {}).get("items") or []
        return items[0] if items else None

    def get_bot_classification(
        self, bot_id: str, entity_id: Optional[str] = None
    ) -> Optional[Dict[str, str]]:
        """Return the minimal authenticated-public classification for a Bot.

        ``entity_id`` addresses legacy Bot IDs such as ``default`` precisely.
        When it is omitted, the repository's unique lookup keeps failing closed
        if a tenant contains multiple live records for the same external
        ``bot_id``.
        """
        bot = (
            self._repository.get_by_id_and_entity(bot_id, entity_id)
            if entity_id is not None
            else self._repository.get_unique_by_id(bot_id)
        )
        if bot is None:
            return None

        # COSEC: Keep this as an explicit allowlist. Returning the repository
        # record would expose owner, runtime, template, and credential metadata
        # to callers who intentionally do not need owner permission.
        return {
            "bot_id": str(bot["bot_id"]),
            "bot_type": str(bot.get("bot_type") or "personal"),
        }

    def list_bots_by_owner_bot_pairs(
        self,
        *,
        pairs: List[tuple[str, str]],
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """Return display-source records for exact Bot and owner pairs."""
        from agentclaw.community.core.bot_management.bcn_metadata import resolve_metadata_pairs

        pairs = resolve_metadata_pairs(self._repository, pairs)
        total, items = self._repository.list_bots_by_owner_bot_pairs(
            pairs,
            page=page,
            page_size=page_size,
        )
        return {"total": total, "items": items}

    def list_bots_by_search(
        self,
        public: Optional[str] = None,
        search: Optional[str] = None,
        page: int | None = 1,
        page_size: int | None = 20,
    ) -> Dict[str, Any]:
        """
        List bots with search and pagination.

        Args:
            public: Filter by public status ("0" or "1")
            search: Fuzzy search by owner_name or bot_name
            page: Page number (1-based)
            page_size: Items per page

        Returns:
            Dictionary with 'total' and 'items' keys
        """
        total, items = self._repository.list_by_search(public=public, search=search, page=page, page_size=page_size)
        self._attach_template_configs_to_bots(items)
        return {"total": total, "items": items}

    def list_domain_bots(
        self,
        page: int | None = None,
        page_size: int | None = None,
        keyword: str | None = None,
    ) -> Dict[str, Any]:
        """
        List domain bots (bots with ext.is_domain_bot=true).

        Args:
            page: Page number (1-based), omit for all results
            page_size: Items per page, omit for all results
            keyword: Keyword to fuzzy-match on bot_name

        Returns:
            Dictionary with 'total' and 'items' keys
        """
        total, items = self._repository.list_domain_bots(
            page=page, page_size=page_size, keyword=keyword,
        )
        return {"total": total, "items": items}

    def search_bots(
        self,
        key: Optional[str] = None,
        bot_status: Optional[str] = None,
        public: Optional[str] = None,
        owner_id: Optional[str] = None,
        service_status_list: Optional[List[str]] = None,
        bot_type: Optional[str] = None,
        active_engine: Optional[str] = None,
        collaborator_user_id: Optional[str] = None,
        bot_id: Optional[str] = None,
        provider: Optional[str] = None,
        template_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """
        搜索 bots，关联发布记录。

        Args:
            key: 模糊搜索 bot_name 或 owner_name
            bot_status: ac_bots.status 过滤
            public: ac_bots.public 过滤
            owner_id: ac_bots.owner_id 过滤
            service_status_list: 服务状态列表过滤（不影响 bot 返回）
            bot_type: ac_bots.bot_type 过滤（如 "personal" 或 "service"）
            active_engine: ac_bots.active_engine 过滤（如 "openclaw"、"claude_code"、"aicoding"）
            collaborator_user_id: 协作者用户 ID，用于过滤该用户参与的 bot
            bot_id: ac_bots.bot_id 精确过滤
            provider: device_provider 过滤（如 "arca"、"daas"、"local"、"baas"）
            template_type: ac_bots.template_type 过滤
            page: 页码
            page_size: 每页数量

        Returns:
            Dictionary with 'total' and 'items' keys, items 中每条记录包含 bot 和 publish 字段，
            当 collaborator_user_id 存在时，每条记录还包含 user_role 字段
        """
        total, items = self._repository.search_bots(
            key=key,
            bot_status=bot_status,
            public=public,
            owner_id=owner_id,
            service_status_list=service_status_list,
            bot_type=bot_type,
            active_engine=active_engine,
            collaborator_user_id=collaborator_user_id,
            bot_id=bot_id,
            provider=provider,
            template_type=template_type,
            page=page,
            page_size=page_size,
        )

        # 为每个 bot 添加 can_delete_bot 和 can_upgrade_publish 字段
        try:
            publish_service = self._bot_publish_provider()

            for bot in items:
                publish = bot.get("publish")

                # 只有服务型 bot 才计算这些字段
                if bot.get("bot_type") == "service" and publish:
                    publish_id = publish.get("id")
                    if publish_id:
                        try:
                            bot["can_delete_bot"] = publish_service.can_delete_bot(publish_id)
                            bot["can_upgrade_publish"] = publish_service.can_upgrade_publish(publish_id)
                        except Exception as e:
                            logger.warning(f"[bot_service.search_bots] Failed to get can_delete/can_upgrade for publish {publish_id}: {e}")
                            bot["can_delete_bot"] = False
                            bot["can_upgrade_publish"] = False
                    else:
                        bot["can_delete_bot"] = False
                        bot["can_upgrade_publish"] = False
                else:
                    # 非服务型 bot 或无发布单，默认为 False
                    bot["can_delete_bot"] = False
                    bot["can_upgrade_publish"] = False
        except Exception as e:
            logger.warning(f"[bot_service.search_bots] Failed to add can_delete_bot/can_upgrade_publish: {e}")

        self._attach_template_configs_to_bots(items)

        return {
            "total": total,
            "items": items,
        }

    def list_bots_by_owner(
        self,
        owner_id: str,
        page: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        """
        List bots by owner_id with pagination.

        Args:
            owner_id: Owner user ID
            page: Page number (1-based)
            page_size: Items per page

        Returns:
            Dictionary with 'total' and 'items' keys
        """
        total, items = self._repository.list_by_owner(
            owner_id=owner_id,
            page=page,
            page_size=page_size,
        )

        # Note: Device binding info is stored in ac_entity_device_binding table
        # To avoid N+1 queries, we don't fetch binding info for list operations
        # Use get_bot() to get detailed info including device_binding

        # 为服务型 bot 添加 can_edit_bot 字段
        try:
            publish_service = self._bot_publish_provider()

            for bot in items:
                # 只有服务型 bot 才计算 can_edit_bot
                if bot.get("bot_type") == "service":
                    bot_id = bot.get("bot_id")
                    if bot_id:
                        try:
                            bot["can_edit_bot"] = publish_service.can_edit_bot(bot_id, owner_id)
                        except Exception as e:
                            logger.warning(f"[bot_service.list_bots_by_owner] Failed to get can_edit_bot for bot {bot_id}: {e}")
                            bot["can_edit_bot"] = False
                    else:
                        bot["can_edit_bot"] = False
                else:
                    # 非服务型 bot，默认为 True
                    bot["can_edit_bot"] = True
        except Exception as e:
            logger.warning(f"[bot_service.list_bots_by_owner] Failed to add can_edit_bot: {e}")

        self._attach_template_configs_to_bots(items)

        return {
            "total": total,
            "items": items,
        }

    def list_bots_by_owner_or_collaborator(
        self,
        owner_id: str,
        page: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        """List bots owned by the user or collaboratively managed by the user."""
        total, items = self._repository.list_by_owner_or_collaborator(
            owner_id=owner_id,
            page=page,
            page_size=page_size,
        )

        try:
            publish_service = self._bot_publish_provider()

            for bot in items:
                if bot.get("bot_type") == "service":
                    bot_id = bot.get("bot_id")
                    if bot_id:
                        try:
                            bot["can_edit_bot"] = publish_service.can_edit_bot(bot_id, bot["owner_id"])
                        except Exception as e:
                            logger.warning(f"[bot_service.list_bots_by_owner_or_collaborator] Failed to get can_edit_bot for bot {bot_id}: {e}")
                            bot["can_edit_bot"] = False
                    else:
                        bot["can_edit_bot"] = False
                else:
                    bot["can_edit_bot"] = True
        except Exception as e:
            logger.warning(f"[bot_service.list_bots_by_owner_or_collaborator] Failed to add can_edit_bot: {e}")

        # List reads use the persisted status. Live desktop status is reconciled
        # outside this request path so a slow BaaS cannot delay first paint.
        self._attach_template_configs_to_bots(items)

        return {
            "total": total,
            "items": items,
        }

    # Transient/process states where BaaS is NOT yet an authoritative source of
    # truth: while a desktop bot is creating (PENDING) or releasing (RELEASING),
    # the container/process may be mid-transition and BaaS reports ALL_OFFLINE
    # even though the bot is progressing normally. The backend's own status is
    # the trustworthy view during these phases; the periodic scan advances them
    # to a steady state. The list only consumes BaaS LIVE status once the bot is
    # already in a steady state (ACTIVE / OFFLINE).
    _MERGE_SKIP_LOCAL_STATUSES = frozenset({"PENDING", "RELEASING"})

    def resolve_desktop_live_status(self, bot: dict) -> str | None:
        """Resolve one desktop bot's BaaS live status; ``None`` to trust DB.

        Single source of truth for "is this a desktop bot that should consume
        BaaS right now", shared by the list-merge and the single-bot upload
        gate so the two never drift on what counts as desktop / steady-state.

        Returns the display status string when ALL hold:
        - ``bot_type == 'desktop'``
        - non-empty ``device_id``
        - local status is NOT a process state (PENDING / RELEASING) — BaaS is
          unreliable mid-transition, so the backend status is authoritative.

        Returns ``None`` otherwise (non-desktop, no device, process state, BaaS
        unmapped status, or any query failure) — the caller keeps the DB status.
        Best-effort and read-only: never writes the DB.
        """
        if bot.get("bot_type") != "desktop" or not bot.get("device_id"):
            return None
        if bot.get("status") in self._MERGE_SKIP_LOCAL_STATUSES:
            return None
        try:
            display = map_baas_to_display(
                self._device_status_client.query_device_status(bot["device_id"])
            )
            return display or None
        except Exception as e:
            logger.warning(
                "[resolve_desktop_live_status] bot=%s BaaS query failed: %s",
                bot.get("bot_id"), e,
            )
            return None
