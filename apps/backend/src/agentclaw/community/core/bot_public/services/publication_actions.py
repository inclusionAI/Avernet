"""Bot public service implementation.

纯业务逻辑层，不包含任何 HTTP 关注点。
"""
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from agentclaw.community.core.operator_context import OperatorContext

from agentclaw.community.plugin_api.bot_publish_approval import (
    BotPublishCallbacks,
)
from agentclaw.community.utils.env_utils import get_current_env

from .public_support import (
    logger,
    _resolve_access_mode,
    _is_workflow_unavailable,
    _BCS_PUBLISH_SCENE,
    _BCS_VISIBILITY_FIELD_BY_SCOPE,
    _BCS_APPROVAL_KEY_BY_SCOPE,
    _BCS_VIEW_SCOPE_DEPS_KEY_BY_SCOPE,
    BotPublicServiceError,
    BotNotFoundError,
)

class PublicationActions:
    def public_bot(self, bot_id: str, owner_id: str, public: str, permission_owner: str, friend_approval: str, operator: OperatorContext, *, approval_biz_id: str | None = None, risk_check_id: str | None = None, on_approval_started: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
        """
        公开/取消公开 Bot。

        根据 permission_owner 决定是否需要审批：
        - permission_owner="owner": 需要发起审批，审批信息保存在 ext 中，等待回调后更新 public 字段
        - permission_owner="caller": 直接更新 public 字段

        Args:
            bot_id: Bot ID
            owner_id: Owner ID（用于权限检查）
            public: 公开标志位 "0"=私有, "1"=公开
            permission_owner: 权限所有者 "caller"=直接更新, "owner"=需要审批
            friend_approval: 好友审批标志位 "0"=不需要审批, "1"=需要审批
            operator: 操作者上下文

        Returns:
            包含操作结果的字典
            - 直接更新: {"success": True, "action": "direct_update", "public": "0|1"}
            - 需要审批: {"success": True, "action": "approval_created", "puid": "xxx", "approval_url": "xxx"}

        Raises:
            BotNotFoundError: 如果 bot 不存在
            BotPermissionError: 如果用户不是 owner
            BotServiceError: 如果操作失败
        """
        if not owner_id:
            raise BotPublicServiceError("Owner ID is required")
        if public not in ("0", "1"):
            raise BotPublicServiceError(f"Invalid public value: {public}, must be '0' or '1'")
        if permission_owner not in ("caller", "owner"):
            raise BotPublicServiceError(f"Invalid permission_owner: {permission_owner}, must be 'caller' or 'owner'")
        if risk_check_id and (not approval_biz_id or not on_approval_started or self._risk_approval_guard is None):
            raise BotPublicServiceError("risk approval guard or stable handoff unavailable")
        bot = self._bot_repository.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        operator_id = operator.staff_id if operator else owner_id
        ext = bot.get("ext") or {}

        if public == "1" and ext.get("digital_employee"):
            raise BotPublicServiceError("已绑定数字员工的 Bot 不支持发布到广场")

        # Compute access mode and update passport
        access_mode = _resolve_access_mode(public, friend_approval)

        if public == "0":
            self._invalidate_observed_risk(bot, "legacy")
            return self._publish_directly(
                bot=bot, ext=ext, bot_id=bot_id, owner_id=owner_id,
                operator_id=operator_id, public="0",
                permission_owner=None, friend_approval=friend_approval,
                access_mode=access_mode,
                log_tag="unpublish",
            )

        # Skip the approval workflow when the owner is the same as the
        # caller — by-design, no third-party approval needed.
        if permission_owner == "caller":
            return self._publish_directly(
                bot=bot, ext=ext, bot_id=bot_id, owner_id=owner_id,
                operator_id=operator_id, public=public,
                permission_owner=permission_owner,
                friend_approval=friend_approval, access_mode=access_mode,
                log_tag="caller permission",
            )

        # permission_owner == "owner": delegate the approval flow to the
        # injected strategy plugin. Local impl publishes directly (no
        # antprocess); prod impl starts the antprocess workflow and the
        # bot stays in PENDING until the callback fires. Rule 14 — mode
        # never reaches this service.
        risk_args = {"risk_check_id": risk_check_id, "approval_biz_id": approval_biz_id,
                     "on_approval_started": on_approval_started} if risk_check_id else {}
        return self._publish_approval_plugin.publish(
            bot=bot, ext=ext, bot_id=bot_id, owner_id=owner_id,
            operator_id=operator_id, operator=operator,
            public=public, permission_owner=permission_owner,
            friend_approval=friend_approval, access_mode=access_mode,
            callbacks=self._publish_callbacks(),
            **risk_args,
        )

    def public_bcs_bot(
        self,
        bot_uid: str,
        owner_id: str,
        public_scope: str,
        operator: OperatorContext,
        view_depts: Optional[List[Dict[str, str]]] = None,
        visibility: Optional[str] = None,
        *,
        approval_biz_id: Optional[str] = None,
        on_approval_started: Optional[Callable[[Dict[str, Any]], None]] = None,
        risk_check_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """New-version publish-to-users (BCS-delegated) — parallel to public_bot.

        ``bot_uid`` is the **BCS bot identity**, not ``ac_bots.bot_id``: the
        legacy ``_bot_repository.get_by_id_and_owner`` reverse-lookup does not
        apply, and no ``access_mode`` / ``permission_owner`` / ``friend_approval``
        branch is taken — a BCS publish opens an approval ticket (unless
        ``visibility="private"``, which patch_attrs BCS directly without approval).

        Starts the approval workflow (``ApprovalWorkflowPlugin`` — the
        "AntProcessService") directly (bypassing the publish strategy, so no
        ``ac_bots`` write). The ticket ``context`` mirrors the normal-bot publish
        — ``_build_public_approval_context`` (``publishHint`` / ``botSkills`` /
        ``botMcps``) — plus two new-version fields: ``public_scope`` (drives the
        callback's BCS-vs-legacy branch) and ``viewFriendDeps`` (``view_depts``
        deptName values joined by ``,``; empty when ``view_depts`` is empty/None).
        """
        if not owner_id:
            raise BotPublicServiceError("Owner ID is required")
        if bool(approval_biz_id) != bool(on_approval_started):
            raise BotPublicServiceError("stable approval ID and handoff callback must be supplied together")
        if risk_check_id and (not approval_biz_id or self._risk_approval_guard is None):
            raise BotPublicServiceError("risk approval guard or stable handoff unavailable")
        if not public_scope:
            raise BotPublicServiceError("public_scope is required")
        if public_scope not in ("user", "agent"):
            raise BotPublicServiceError(
                f"public_scope must be 'user' or 'agent', got: {public_scope!r}"
            )
        if view_depts is not None and not isinstance(view_depts, list):
            raise BotPublicServiceError("view_depts must be a list or None")
        if view_depts:
            for dept in view_depts:
                if not isinstance(dept, dict) or not dept.get("deptNo") or not dept.get("deptName"):
                    raise BotPublicServiceError(
                        "each view_depts item must be a dict with deptNo and deptName"
                    )
        if visibility is not None and visibility not in ("public", "protected", "private"):
            raise BotPublicServiceError(
                f"visibility must be 'public'/'protected'/'private' or None, got: {visibility!r}"
            )
        # visibility=private 直连: 不走审批, 直接 PATCH BCS 属性 (值 "private");
        # 更新的字段随 public_scope 联动 (agent→visibility, user→user_visibility)。
        if visibility == "private":
            if self._risk_repository is not None:
                backend_bot_id, separator, entity_id = bot_uid.rpartition(":")
                if separator and backend_bot_id and entity_id:
                    bot = self._bot_repository.get_by_id_and_entity(backend_bot_id, entity_id)
                else:
                    # Preserve the legacy bare-BCS-identity path for callers
                    # outside the collaboration Bot addressing contract.
                    bot = self._bot_repository.get_by_id_and_owner(bot_uid, owner_id)
                if bot:
                    self._invalidate_observed_risk(bot, public_scope)
            return self._apply_visibility_direct(
                bot_uid=bot_uid, public_scope=public_scope
            )
        # bot_uid is the BCS bot identity ("{backend_bot_id}:{entity_id}"); the
        # bot / skills lookup keys on the backend bot_id, so split on ':'.
        backend_bot_id = bot_uid.split(":")[0]
        if risk_check_id:
            backend_bot_id, separator, entity_id = bot_uid.rpartition(":")
            if not separator or not backend_bot_id or not entity_id:
                raise BotPublicServiceError("risk publication requires full bot identity")
            bot = self._bot_repository.get_by_id_and_entity(backend_bot_id, entity_id)
            if bot and str(bot.get("owner_id")) != owner_id:
                raise BotNotFoundError(f"Bot not found: {bot_uid}")
        else:
            bot = self._bot_repository.get_by_id_and_owner(backend_bot_id, owner_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_uid}")
        operator_id = operator.staff_id if operator else owner_id
        # Context mirrors the normal-bot publish (publishHint/botSkills/botMcps
        # via _build_public_approval_context) plus the new-version fields.
        # All values are str -> stays dict[str, str] for the antprocess echo.
        context: Dict[str, str] = self._build_public_approval_context(bot, operator)
        # BCS publish uses its own approval prompt (发布→开放, scene resolved
        # from public_scope) instead of the normal-bot publishHint.
        context["publishHint"] = self._build_bcs_publish_hint(bot, operator, public_scope)
        context["public_scope"] = public_scope
        context["viewFriendDeps"] = self._build_view_friend_deps(view_depts)
        # bot_id (the full BCS identity "{backend_bot_id}:{entity_id}", used
        # verbatim as bot_uuid by the callback's _apply_callback_decision) and
        # owner_id ride the context so antprocess echoes them back (snake→camel:
        # bot_id→botId, owner_id→ownerId) on the /callback POST — without
        # bot_id the callback can't resolve the bot and the AGREE visibility
        # flip is a no-op.
        context["bot_id"] = bot_uid
        context["owner_id"] = owner_id
        if risk_check_id:
            context["risk_check_id"] = risk_check_id
        approval_result = self._process_service.start_approval(
            applicant=operator_id,
            # biz_id flows into the approval service's puid, which only accepts
            # alphanumeric + hyphen. bot_uid is the BCS identity
            # "{backend_bot_id}:{entity_id}" and may carry ":" and "_" (the
            # backend bot id has its own underscores) — both rejected by the
            # approval service's PARAM_CHECKED. Sanitize every non-alphanumeric
            # char to "-" so the puid carries the identity without the colon/_
            # that would break its "-" / "_" field delimiters.
            biz_id=approval_biz_id or f"{re.sub(r'[^a-zA-Z0-9-]', '-', bot_uid)}{datetime.now().strftime('%Y%m%d%H%M%S')}",
            biz_type="botpublic",
            context=context,
        )
        # The protocol promises no-workflow callers fall through to direct
        # publish (NOT a failure). In the community build NoApprovalWorkflow
        # (and local LocalAntProcessService) report "no approval capability" —
        # distinguished from a real rejection by the NO_WORKFLOW_MARKER prefix
        # on error_msg. We treat that as auto-approved (AGREE): synthesize a
        # COMPLETED result so the persist + inline-callback path below drives
        # the BCS friend_ext update (status AGREE + visibility flip) exactly
        # like corp's auto-completed tenant, instead of raising (which the old
        # code did for any success=False — turning every community publish into
        # a 500). No real ticket was created, so puid stays None;
        # _apply_callback_decision keys the BCS write off public_scope, not
        # puid. The returned state is reported SKIPPED (the approval step was
        # skipped) even though the BCS update did run — see the
        # ``workflow_unavailable`` handling before the return.
        workflow_unavailable = False
        if not approval_result.get("success"):
            if not _is_workflow_unavailable(approval_result):
                raise BotPublicServiceError(
                    f"创建审批失败: {approval_result.get('error_msg')}"
                )
            workflow_unavailable = True
            approval_result = {
                "success": True,
                "state": "COMPLETED",
                "lastOperate": "AGREE",
                "puid": None,
                "approval_url": None,
                "error_msg": approval_result.get("error_msg"),
            }
        # The risk caller must persist the association before any inline
        # callback can apply the approval. Failure must stop this path.
        if on_approval_started is not None:
            on_approval_started(approval_result)
        if risk_check_id and not approval_result.get("puid"):
            raise BotPublicServiceError("risk approval has no associated PUID")
        if risk_check_id and str(approval_result.get("state", "")).upper() == "COMPLETED":
            deferred = self._risk_approval_guard.check(
                get_current_env(), risk_check_id, owner_id, bot["id"], approval_result["puid"],
                str(approval_result.get("lastOperate", "")).upper(), public_scope,
            )
            if deferred != "DEFERRED":
                raise BotPublicServiceError("risk inline approval could not be persisted")
        # 记录工单信息到 BCS friend_ext.public_user_approval (PROCESSING):
        # GET→merge→PATCH (Provider 管理 API, 同 BcnService 鉴权). 失败不中断已建工单.
        try:
            persisted = self._persist_public_approval(
                bot_uid=bot_uid,
                public_scope=public_scope,
                puid=approval_result.get("puid"),
                approval_url=approval_result.get("approval_url"),
                view_depts=view_depts,
                visibility=visibility,
            )
            if risk_check_id and persisted is not True:
                raise BotPublicServiceError("risk approval target was not persisted")
        except Exception as exc:  # noqa: BLE001 — must not abort a started approval
            if risk_check_id:
                raise BotPublicServiceError("risk approval association could not be persisted") from exc
            logger.warning(
                "[public_bcs_bot] BCS friend_ext persist failed: %s", exc
            )
        # Mirror the legacy botpublish flow: if the approval auto-completed, run
        # the public_scope callback inline so the caller gets the final state in one
        # round-trip. The public_scope path logs (BCS update pending) and writes no
        # ac_bots. viewFriendDeps rides the ticket context only — it is NOT
        # forwarded to the callback (no reverse round-trip needed).
        replayed = False
        if risk_check_id:
            self._risk_approval_guard.mark_target_ready(get_current_env(), risk_check_id, approval_result["puid"])
            replayed = self._risk_approval_guard.replay(
                get_current_env(), risk_check_id,
                lambda puid, decision: self.handle_public_approval_callback(
                    bot_id=bot_uid, owner_id=owner_id, puid=puid, last_operate=decision,
                    public_scope=public_scope, risk_check_id=risk_check_id,
                ),
            ) is True
        if not risk_check_id and not replayed and str(approval_result.get("state", "")).upper() == "COMPLETED":
            try:
                self.handle_public_approval_callback(
                    bot_id=bot_uid,
                    owner_id=owner_id,
                    puid=approval_result.get("puid"),
                    last_operate=str(approval_result.get("lastOperate", "")).lower(),
                    public_scope=public_scope,
                    **({"risk_check_id": risk_check_id} if risk_check_id else {}),
                )
            except Exception as exc:  # noqa: BLE001 — must not abort a started approval
                logger.warning(
                    "[public_bcs_bot] COMPLETED inline callback failed: %s", exc
                )
        # The no-workflow fall-through reused the persist + AGREE inline-callback
        # path above to drive the BCS update; surface the *approval* outcome as
        # SKIPPED (no approval step ran) rather than the synthesized COMPLETED.
        if workflow_unavailable:
            return {
                "success": True,
                "state": "SKIPPED",
                "puid": None,
                "approval_url": None,
                "last_operate": "agree",
                "error_msg": approval_result.get("error_msg"),
            }
        return approval_result

    def _invalidate_observed_risk(self, bot: Dict[str, Any], public_scope: str) -> None:
        """Stop the observed risk request before withdrawing its publication.

        Compare identity and version to avoid withdrawing a replacement. If a
        worker changes the same record concurrently, reread and retry; database
        errors and repeated contention propagate instead of silently proceeding.
        """
        repository = self._risk_repository
        if repository is None:
            return
        env = get_current_env()
        bot_pk = bot["id"]
        row = repository.get_current(env=env, bot_pk=bot_pk, public_scope=public_scope)
        if row is None:
            return
        observed_id = row.risk_check_id
        for _ in range(3):
            if repository.invalidate_if_current(
                env=env, risk_check_id=observed_id, row_version=row.row_version
            ):
                return
            row = repository.get_current(env=env, bot_pk=bot_pk, public_scope=public_scope)
            if row is None or row.risk_check_id != observed_id:
                return
        raise BotPublicServiceError("could not invalidate current public risk request")

    def _publish_callbacks(self) -> BotPublishCallbacks:
        """Bind the publish-approval plugin's callback bag to this
        service's bound methods.

        TODO(architecture): see ``plugins/bot_publish_approval.py``'s
        module docstring for the long-term plan to remove this.
        """
        return BotPublishCallbacks(
            publish_directly=self._publish_directly,
            archive_approval=self._archive_public_approval,
            update_with_notification=self._update_bot_with_notification,
            handle_approval_callback=self.handle_public_approval_callback,
            refetch_bot=self._bot_repository.get_by_id_and_owner,
            build_approval_context=self._build_public_approval_context,
            replay_risk_approval=self._replay_risk_approval,
            mark_risk_target_ready=lambda risk_id, puid: self._risk_approval_guard.mark_target_ready(
                get_current_env(), risk_id, puid
            ),
            stage_risk_approval=self._stage_legacy_risk_approval,
        )

    def _stage_legacy_risk_approval(self, risk_id: str, owner_id: str, bot_pk: int,
                                    puid: str, decision: str) -> None:
        if self._risk_approval_guard is None or self._risk_approval_guard.check(
            get_current_env(), risk_id, owner_id, bot_pk, puid, decision.upper(), "legacy"
        ) != "DEFERRED":
            raise BotPublicServiceError("risk inline approval could not be persisted")

    def _replay_risk_approval(self, bot_id: str, owner_id: str, risk_check_id: str) -> bool:
        if self._risk_approval_guard is None:
            raise BotPublicServiceError("risk callback guard unavailable")
        return self._risk_approval_guard.replay(
            get_current_env(), risk_check_id,
            lambda puid, decision: self.handle_public_approval_callback(
                bot_id=bot_id, owner_id=owner_id, puid=puid, last_operate=decision,
                risk_check_id=risk_check_id,
            ),
        )

    def _publish_directly(
        self,
        *,
        bot: Dict[str, Any],
        ext: Dict[str, Any],
        bot_id: str,
        owner_id: str,
        operator_id: str,
        public: str,
        permission_owner: Optional[str],
        friend_approval: str,
        access_mode: str,
        log_tag: str = "direct publish",
    ) -> Dict[str, Any]:
        """Persist the bot's new public/permission state without going
        through an approval workflow.

        Shared by three call sites (un-publish, caller-permission
        publish, and the local strategy's owner-permission fallback).
        ``permission_owner=None`` means "drop the ext field" (the
        un-publish case); otherwise it's set on ext.
        """
        self._archive_public_approval(ext, bot_id)
        if permission_owner is None:
            ext.pop("permission_owner", None)
        else:
            ext["permission_owner"] = permission_owner
        ext["friend_approval"] = friend_approval
        update_data = {"public": public, "modifier_id": operator_id, "ext": ext}
        updated_bot = self._update_bot_with_notification(
            bot_id, owner_id, update_data, bot, notify_device=True,
        )
        if not updated_bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        self._sync_access_mode_and_relations(
            bot_id, owner_id, access_mode, public,
        )
        self._sync_bcsfuse_runtime_state(bot_id, owner_id, public)
        logger.info(
            "[bot_service.public_bot] %s: bot_id=%s public=%s "
            "owner=%s operator=%s",
            log_tag, bot_id, public, owner_id, operator_id,
        )
        return updated_bot

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
        publish_hint = f"\"{owner_name}\"同学正在发布\"{bot_name}\"bot至TeamClaw的Bot广场，审批通过后平台全体成员均可基于\"{owner_name}\"权限进行服务调用。在服务发布前，请您审核：该Agent是否涉及敏感数据处理或核心业务操作；如涉及，为避免造成由于权限扩散造成的不必要风险，请重新考虑是否发布。"
        # 获取 bot skills 并格式化为字符串
        bot_skills_str = ""
        try:
            skill_set_service = self._skill_set_service_factory.create(
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
            skill_set_service = self._skill_set_service_factory.create(
                entity_id=entity_id, bot_id=bot_id
            )
            mcp_sets = skill_set_service.get_all_skill_sets_with_mcps(user_id=user_id, bolt_id=bot_id)
            mcp_parts = []
            for mcp_set in mcp_sets:
                is_active = mcp_set.get("is_active", False)
                set_name = mcp_set.get("name", "")
                mcps = mcp_set.get("mcps", [])
                mcp_names = [m.get("name", "") for m in mcps if m.get("name")]
                if mcp_names:
                    active_marker = "[激活]" if is_active else "[未激活]"
                    mcp_parts.append(f"{set_name}{active_marker}:")
                    for name in mcp_names:
                        mcp_parts.append(f"  - {name}")
            bot_mcps_str = "\n".join(mcp_parts) if mcp_parts else "无"
        except Exception as e:
            logger.warning(f"[_build_public_approval_context] Failed to get mcp sets for bot {bot_id}: {e}")
            bot_mcps_str = "获取失败"
        context = {"publishHint": publish_hint, "botSkills": bot_skills_str, "botMcps": bot_mcps_str}
        return context

    def _build_view_friend_deps(self, view_depts: Optional[List[Dict[str, str]]]) -> str:
        """Join ``view_depts`` deptName values into the ``viewFriendDeps``
        ticket-context field (comma-separated). Empty/None ``view_depts`` -> "".
        Only deptName is carried downstream — deptNo is not (yet) consumed.
        """
        if not view_depts:
            return ""
        names = [
            dept["deptName"]
            for dept in view_depts
            if isinstance(dept, dict) and dept.get("deptName")
        ]
        return ",".join(names)

    def _build_bcs_publish_hint(
        self,
        bot: Dict[str, Any],
        operator: Optional[OperatorContext],
        public_scope: str,
    ) -> str:
        """BCS-publish approval prompt — mirrors the normal-bot publishHint but
        swaps 发布→开放 and the destination is a ``{scene}`` resolved from
        ``public_scope`` (user→加好友场景, agent→群聊场景). Kept separate from
        ``_build_public_approval_context`` so the normal-bot hint is untouched.
        """
        owner_name = bot.get("owner_name") or ""
        if not owner_name and operator:
            owner_name = (
                operator.nick_name or operator.operator_name
                or operator.staff_id or ""
            )
        bot_name = bot.get("bot_name") or bot.get("bot_id") or ""
        scene = _BCS_PUBLISH_SCENE.get(public_scope, public_scope or "")
        return f"{owner_name}同学正在开放{bot_name}bot到{scene}，审批通过后平台全体成员均可基于{owner_name}权限进行服务调用。在服务发布前，请您审核：该Agent是否涉及敏感数据处理或核心业务操作；如涉及，为避免造成由于权限扩散造成的不必要风险，请重新考虑是否发布。"

    def _persist_public_approval(
        self,
        *,
        bot_uid: str,
        public_scope: str,
        puid: Optional[str],
        approval_url: Optional[str],
        view_depts: Optional[List[Dict[str, str]]],
        visibility: Optional[str] = None,
    ) -> bool:
        """工单创建后把工单信息记到 BCS friend_ext.public_user_approval(PROCESSING)。

        GET /providers/{provider_id}/bots/{bot_uid}/attributes 拿当前 friend_ext,
        合并(不破坏现有子键)后 PATCH 整个 friend_ext 回去(协议: friend_ext 顶层对象
        整体替换)。view_friend_deps 用入参 view_depts 原样(空/None → [])。BCS 调用
        跳过(非 prod/pre 或凭据空)时静默返回。
        """
        attrs = self._bcn_service.get_attributes(bot_uuid=bot_uid)
        if attrs.get("skipped"):
            return False
        friend_ext = dict(attrs.get("friend_ext") or {})
        block: Dict[str, Any] = {
            "puid": puid,
            "approval_url": approval_url,
            "view_friend_deps": list(view_depts) if view_depts else [],
            "status": "PROCESSING",
        }
        if visibility:
            block["visibility"] = visibility
        friend_ext[_BCS_APPROVAL_KEY_BY_SCOPE[public_scope]] = block
        result = self._bcn_service.patch_attributes(
            bot_uuid=bot_uid, body={"friend_ext": friend_ext}
        )
        return not (isinstance(result, dict) and result.get("skipped"))

    def _apply_visibility_direct(
        self,
        *,
        bot_uid: str,
        public_scope: str,
    ) -> Dict[str, Any]:
        """visibility=private 直连路径: 不走审批, 直接 PATCH BCS 属性。

        只设 "private"(收回可见性无需审批);更新的字段随 public_scope 联动:
        ``public_scope=agent`` → BCS ``visibility`` 字段;``public_scope=user``
        → BCS ``user_visibility`` 字段(见 _BCS_VISIBILITY_FIELD_BY_SCOPE)。
        public/protected 仍走工单, 见 public_bcs_bot 主流程。
        """
        field = _BCS_VISIBILITY_FIELD_BY_SCOPE[public_scope]
        patched = self._bcn_service.patch_attributes(
            bot_uuid=bot_uid, body={field: "private"}
        )
        # patch_attributes returns {"skipped": True} when community has no BCS
        # provider creds (non prod/pre); report that truthfully as SKIPPED
        # instead of faking COMPLETED.
        skipped = isinstance(patched, dict) and bool(patched.get("skipped"))
        if public_scope == "user" and not skipped:
            self._sync_bcsfuse_worker_runtime_state(bot_uid, bot_uid, "0")
        return {
            "success": True,
            "state": "SKIPPED" if skipped else "COMPLETED",
            "puid": None,
            "approval_url": None,
            "visibility": "private",
            "visibility_field": field,
        }

    def _apply_callback_decision(
        self, *, bot_uid: str, public_scope: str, last_operate: str, expected_puid: str | None = None,
        expected_target: dict | None = None,
        before_apply: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """审批回调落 BCS: 更新 sub-block status; AGREE 时再把 BCS 可见性字段翻转。

        public_scope 非空即新版发布回调 (caller 保证 bot_id == bot_uid):
        - 把 (_BCS_APPROVAL_KEY_BY_SCOPE) 对应的 friend_ext 子块
          (public_user_approval/public_agent_approval) 的 status 写成 last_operate
          (AGREE/DISAGREE/CANCEL)。
        - AGREE 时同时翻 BCS 可见性字段 (_BCS_VISIBILITY_FIELD_BY_SCOPE):
            - public_scope=user → user_visibility = block.visibility (block 存的请求值,
              缺省 protected);
            - public_scope=agent → visibility = "public" if
              BCS friend_check_in_strategy=="OPEN" else "protected" (BCS top-level
              friend_check_in_strategy; agent 要考虑它, user 不考虑)。
        所有变更合一 PATCH (friend_ext + 可见性字段) 回。BCS 跳过 (非 prod/pre 或
        凭据空) 时静默返回。
        """
        attrs = self._bcn_service.get_attributes(bot_uuid=bot_uid)
        if attrs.get("skipped"):
            if expected_puid is not None:
                return {"success": False, "public": None, "message": "risk callback BCS read skipped"}
            return {
                "success": True, "public": None,
                "message": f"public_scope={public_scope} callback skipped (BCS creds)",
            }
        key = _BCS_APPROVAL_KEY_BY_SCOPE.get(public_scope)
        if not key:
            return {
                "success": True, "public": None,
                "message": f"public_scope={public_scope} callback noop (unknown scope)",
            }
        friend_ext = dict(attrs.get("friend_ext") or {})
        block = dict(friend_ext.get(key) or {})
        if expected_puid is not None and block.get("puid") != expected_puid:
            return {"success": False, "public": None, "message": "risk approval PUID mismatch"}
        if expected_puid is not None and block.get("visibility") not in {"public", "protected"}:
            return {"success": False, "public": None, "message": "risk approval target unavailable"}
        if expected_puid is not None:
            try:
                actual_depts = sorted({str(item["deptNo"]) for item in block.get("view_friend_deps", [])})
                expected_depts = sorted({str(item["deptNo"]) for item in expected_target["view_depts"]})
                same_target = block["visibility"] == expected_target["visibility"] and actual_depts == expected_depts
            except (KeyError, TypeError):
                same_target = False
            if not same_target:
                return {"success": False, "public": None, "message": "risk approval target mismatch"}
        block["status"] = (last_operate or "").upper()
        friend_ext[key] = block
        body: Dict[str, Any] = {"friend_ext": friend_ext}
        if block["status"] == "AGREE":
            field = _BCS_VISIBILITY_FIELD_BY_SCOPE.get(public_scope)
            if field:
                if expected_puid is not None:
                    body[field] = block["visibility"]
                elif public_scope == "user":
                    # user: user_visibility 直接取 block 里存的 visibility 字段
                    body[field] = block.get("visibility") or "protected"
                else:  # agent: 由 BCS friend_check_in_strategy 决断
                    strategy = str(attrs.get("friend_check_in_strategy") or "").upper()
                    body[field] = "public" if strategy == "OPEN" else "protected"
            # AGREE 还把 block.view_friend_deps 从 public_*_approval 子块提升到
            # friend_ext 顶层(scope-联动 key: user→view_scope_user_friend_deps;
            # agent→view_scope_agent_friend_deps)。
            friend_ext[_BCS_VIEW_SCOPE_DEPS_KEY_BY_SCOPE[public_scope]] = (
                block.get("view_friend_deps") or []
            )
        if before_apply is not None and before_apply() is not True:
            return {"success": False, "public": None, "message": "risk callback eligibility changed"}
        patched = self._bcn_service.patch_attributes(bot_uuid=bot_uid, body=body)
        skipped = isinstance(patched, dict) and bool(patched.get("skipped"))
        if expected_puid is not None and skipped:
            return {"success": False, "public": None, "message": "risk callback BCS patch skipped"}
        if public_scope == "user" and block["status"] == "AGREE" and not skipped:
            public = "0" if body["user_visibility"] == "private" else "1"
            self._sync_bcsfuse_worker_runtime_state(bot_uid, bot_uid, public)
        return {
            "success": True, "public": None,
            "message": f"public_scope={public_scope} callback status={block['status']}",
        }
