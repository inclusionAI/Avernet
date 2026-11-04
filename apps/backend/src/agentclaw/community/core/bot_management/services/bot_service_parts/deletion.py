"""
Config/work/engine paths, the delete flow with its purge/sweep companions and
name-existence check — moved verbatim from ``bot_service.py``.

Part of the mechanical split of ``bot_service.py``: ``BotDeletionMixin`` members were
moved here verbatim and the facade composes them onto ``BotService``.
"""

from pathlib import Path
from typing import Any, Dict, Optional

from agentclaw.community.core.bot_management.bcn_identity import provider_address_kwargs
from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.core.workspace.path_factory import (
    get_bot_engine_config_dir,
    get_bot_engine_dir,
)
from agentclaw.community.plugin_api.passport import PassportError

from .common import (
    BotNotFoundError,
    BotOperationNotAllowedError,
    BotServiceError,
    _compose_operator_context,
    logger,
)


class BotDeletionMixin:
    """Config/work/engine paths, the delete flow with its purge/sweep companions and
    the name-existence check."""

    def get_bot_config_path(
        self,
        bot_id: str,
        user_id: str,
        engine_type: str = DEFAULT_ENGINE_TYPE
    ) -> Path:
        """
        Get bot configuration directory path by bot_id.

        Path structure: {aidesktop_root}/aidesktop_{env}/bolt_data/{entity_id}/{bot_id}/{engine_type}_conf

        Args:
            bot_id: Bot ID
            user_id: User ID for permission check (must be the owner)
            engine_type: Engine type (default: "moltis")

        Returns:
            Path: Bot configuration directory path

        Raises:
            BotNotFoundError: If bot not found
        """
        # Get bot by bot_id and owner_id (user_id)
        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        entity_id = bot.get("entity_id")
        entity_type = bot.get("entity_type", "staff")
        if not entity_id:
            raise BotServiceError(f"Bot {bot_id} has no associated entity_id")

        # Get config path from config module
        config_path = get_bot_engine_config_dir(entity_id, str(bot_id), engine_type, entity_type)
        logger.info(f"[bot_service.get_bot_config_path] Bot {bot_id} config path: {config_path}")

        return config_path

    def get_bot_work_path(
        self,
        bot_id: str,
        user_id: str,
        engine_type: str = DEFAULT_ENGINE_TYPE
    ) -> Path:
        """
        Get bot working directory path by bot_id.

        Path structure: {aidesktop_root}/aidesktop_{env}/bolt_data/{entity_id}/{bot_id}/{engine_type}

        Args:
            bot_id: Bot ID
            user_id: User ID for permission check (must be the owner)
            engine_type: Engine type (default: "moltis")

        Returns:
            Path: Bot working directory path

        Raises:
            BotNotFoundError: If bot not found
        """
        # Get bot by bot_id and owner_id (user_id)
        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        entity_id = bot.get("entity_id")
        entity_type = bot.get("entity_type", "staff")
        if not entity_id:
            raise BotServiceError(f"Bot {bot_id} has no associated entity_id")

        # Get work path from config module
        work_path = get_bot_engine_dir(entity_id, str(bot_id), engine_type, entity_type)
        logger.info(f"[bot_service.get_bot_work_path] Bot {bot_id} work path: {work_path}")

        return work_path

    def get_engine_paths(
        self,
        entity_id: str,
        bot_id: str,
        engine_types: list[str],
        entity_type: str = "staff",
    ) -> dict[str, str]:
        """Get bot working directories for all engines.

        Handles sqlite/prod mode switching internally.

        Args:
            entity_id: Entity ID
            bot_id: Bot ID
            engine_types: List of engine types
            entity_type: Entity type (default: "staff")

        Returns:
            Dict mapping engine_type to path string
        """
        return {
            engine: str(
                self._path_factory.get_engine_workspace_data_dir(
                    entity_id, bot_id, engine, entity_type
                )
            )
            for engine in engine_types
        }

    def validate_delete_bot(self, bot_id: str, user_id: str) -> Dict[str, Any]:
        """Validate current ownership and deletion policy before external cleanup."""
        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        if (bot.get("ext") or {}).get("digital_employee"):
            raise BotOperationNotAllowedError("已绑定数字员工的 Bot 不支持删除")

        # 保护 owner 名下最早创建的 bot 不能删除（等价于旧 "default" bot 不可删语义）。
        # 含 owner 仅一只的情形（earliest 即该只 → 拒），自然保留 ≥1。
        # 必须在 release_device / destroy_passport 之前拦截，否则会误销毁
        # agent 许可证 (Passport) 并重置引擎配置 (openclaw.json)。
        # 用 BotOperationNotAllowedError（BotServiceError 子类）表达"这是客户端
        # 不支持的操作"，而不是服务端故障：重试永远不会成功。内部路由的 except 链没有
        # 这一分支，仍落到 `except BotServiceError` → 500，行为不变；公共 API 则按
        # 4xx 映射。
        _total_owner_bots, owner_bot_items = self._repository.list_by_owner(user_id, 1, 1000)
        if owner_bot_items:
            # gmt_create可能是 datetime 或 ISO 字符串;统一成字符串排序,避免类型混比。
            # 空值兜底为空串(ISO 字符串排序下排最前,等同"最早")。
            earliest = min(
                owner_bot_items,
                key=lambda b: str(b.get("gmt_create") or ""),
            )
            earliest_bot_id = earliest.get("bot_id")
            if earliest_bot_id and bot_id == earliest_bot_id:
                raise BotOperationNotAllowedError("不能删除首个创建的 Bot，该 Bot 受保护")

        return bot

    def delete_bot(self, bot_id: str, user_id: str, nick_name: Optional[str] = None) -> bool:
        """
        Soft delete a bot.

        Args:
            bot_id: Bot ID
            user_id: User ID for permission check and releasing device (required)
            nick_name: Nick name for releasing device (optional, defaults to bot owner)

        Returns:
            True if deleted, False if not found

        Raises:
            BotNotFoundError: If bot not found
            BotServiceError: If deletion fails
        """
        # Check user_id is provided
        if not user_id:
            raise BotServiceError("User ID is required for deleting bot")

        try:
            bot = self.validate_delete_bot(bot_id, user_id)
            # Resolve before grant withdrawal, device release or soft deletion.
            provider_address = provider_address_kwargs(bot)
            from agentclaw.community.core.bot_management.addressing import stable_resource_owner_id
            resource_owner_id = (
                stable_resource_owner_id(bot)
                if bot.get("bot_type") == "service" and bot_id != "default"
                else user_id
            )

            # Withdraw every application authorization standing against this
            # bot — whoever delegated it — before anything destructive happens.
            # This is the first of two sweeps; the second runs after the soft
            # delete, and the pair is what actually closes the gap. See there.
            #
            # Ordering is the whole point. Placed after the device release and
            # the passport destruction, a failure here would leave a bot that is
            # already unusable with live grants against it: applications still
            # authorized to reach something that no longer works, and no
            # deletion to trigger the sweep again. Placed here, a failure aborts
            # while the bot is still intact, and the worst outcome is grants
            # withdrawn from a bot that survived — recoverable by re-granting.
            #
            # Failures propagate deliberately. Swallowing this would reintroduce
            # exactly the gap it closes, and quietly.
            revoked = self._bot_app_grant_provider().revoke_all_for_bot(
                bot_id=bot_id, owner_id=resource_owner_id
            )
            if revoked:
                logger.info(
                    "[bot_service.delete_bot] withdrew %s app authorization(s) "
                    "on bot %s before deleting it",
                    revoked,
                    bot_id,
                )

            # Drop the stored startup script, here and not in the post-delete
            # cleanup, for the same reason the grant sweep is here: a failure
            # aborts while the bot is still intact.
            #
            # It is not filed with the skills and skill sets below because it is
            # not the same kind of leftover. Those are inert metadata; this is
            # the user's script in plaintext, which should not outlive the bot
            # it was written for.
            #
            # Failures propagate, matching how this feature already treats a
            # failed script read (`_resolve_startup_script`): swallowing a
            # failure against the script store buys a silently wrong state, not
            # resilience. The wedged-bot risk is smaller than it looks — this
            # write and the soft delete hit the same database, so a purge that
            # cannot write almost certainly precedes a deletion that would have
            # failed anyway.
            #
            # The `default` bot is exempt on the same grounds its skills and
            # config are: that delete is a restart, and the script is meant to
            # survive it.
            if bot_id != "default":
                self._purge_startup_script(bot_id, bot)

            # Release device if binding exists (包括 ACTIVE 和 PENDING 状态)
            binding_id = bot.get("binding_id")
            if binding_id:
                service = self._device_service_provider()
                # Get device binding from ac_entity_device_binding table
                binding = service.get_device(binding_id=binding_id)
                if binding and binding.status in [
                    DeviceBindingStatus.ACTIVE.value,
                    DeviceBindingStatus.PENDING.value,
                    DeviceBindingStatus.FAILED.value,
                    DeviceBindingStatus.STOPPED.value,
                ]:
                    # Release the device
                    operator = _compose_operator_context(
                        user_id,
                        nick_name or user_id
                    )
                    try:
                        service.release_device(
                            binding_id=binding_id,
                            release_reason=f"Bot {bot_id} deleted",
                            reset=True,  # 删除设备室，强制重置引擎配置(删除openclaw.json)
                            operator=operator,
                        )
                        logger.info(f"[bot_service.delete_bot] Released device for binding {binding_id}")
                    except Exception as e:
                        # 释放失败，阻断删除，抛出异常
                        logger.error(f"[bot_service.delete_bot] Failed to release device for binding {binding_id}: {e}")
                        raise BotServiceError(f"设备释放失败，无法删除 Bot: {e}")

            # Step 2: 通知 Passport 服务销毁 Passport（阻塞流程）
            try:
                self._passport_plugin.destroy_passport(bot_id, resource_owner_id)
                logger.info(f"[bot_service.delete_bot] destroyPassport success: bot_id={bot_id}, owner_workno={user_id}")
            except PassportError as e:
                logger.error(f"[bot_service.delete_bot] destroyPassport failed: bot_id={bot_id}, owner_workno={user_id}, error={e}")
                raise BotServiceError(f"销毁 Passport 失败: {e}")

            # Soft delete the bot (use soft_delete_by_owner to ensure we only delete the owner's bot)
            result = self._repository.soft_delete_by_owner(bot_id, user_id)
            if not result:
                raise BotNotFoundError(f"Bot not found: {bot_id}")

            self._sweep_grants_that_raced_the_deletion(bot_id, resource_owner_id)

            if bot_id != "default":
                self._sweep_startup_script_that_raced_the_deletion(bot_id, bot)

            self._sync_provider_bot_delete_to_bcn(bot_id, user_id, **provider_address)

            # 清理关联的脏数据（仅限非 default bot）
            # default bot 是用户的默认 Bot，删除它通常是"重启"逻辑，应保留技能和配置
            if bot_id != "default":
                try:
                    self._cleanup_bot_associated_data(bot_id, resource_owner_id)
                    logger.info(f"[bot_service.delete_bot] Cleaned up associated data for bot {bot_id}")
                except Exception as cleanup_error:
                    # 清理失败不影响删除结果，只记录日志
                    logger.warning(f"[bot_service.delete_bot] Failed to cleanup associated data for bot {bot_id}: {cleanup_error}")
            else:
                logger.info(f"[bot_service.delete_bot] Skipping cleanup for default bot {bot_id} (restart scenario)")

            logger.info(f"[bot_service.delete_bot] Bot {bot_id} deleted successfully")
            return True
        except BotNotFoundError:
            raise
        except BotOperationNotAllowedError:
            # 必须在 catch-all 之前放行：否则"不允许删除 default bot"这类客户端错误
            # 会被重新包成通用 BotServiceError，调用方看到 500 并可能无谓重试。
            raise
        except Exception as e:
            logger.error(f"[bot_service.delete_bot] Failed to delete bot {bot_id}: {e}")
            raise BotServiceError(f"Failed to delete bot: {e}")

    def _sweep_grants_that_raced_the_deletion(
        self, bot_id: str, user_id: str
    ) -> None:
        """Withdraw grants that landed while the deletion was in flight.

        The first sweep commits, and then the deletion spends time in the device
        release and the Passport destruction — both remote calls. A collaborator
        granting in that window inserts a row *after* the sweep read, and it
        would outlive the bot.

        **This narrows the window; it does not close it, and an earlier
        revision of this docstring wrongly claimed it did.** The claim was that
        once ``soft_delete_by_owner`` commits no further grant can be created,
        because every way the delegation gate resolves a bot filters
        ``is_delete == 0``. Those filters are real, but they guard *resolution*,
        which happens early in the request — the row is written later, in
        ``BotAppGrantService.grant``. A request that had already resolved the
        bot can still insert behind this sweep.

        What remains is bounded on the other side by
        :class:`~...bot_app_grant.errors.GrantBotNotLiveError`: the grant path
        rechecks liveness at the write, so the surviving gap is between that
        check and its insert rather than the whole request. Closing even that
        would mean locking the bot row across every grant write, to prevent a
        row that grants nothing — every read filters bots by liveness, and every
        request re-adjudicates against a bot that cannot be resolved. Hygiene,
        not the boundary. The boundary is that nothing is trusted from the row.

        **Failures propagate, like the first sweep.** An earlier revision made
        this best-effort, reasoning that the bot is already deleted so raising
        reports a failure for an operation that succeeded. That reasoning is
        real but it loses: `AGENTS.md` is explicit that persistence write
        failures are propagated and never swallowed into a success, and a sweep
        that could not commit is exactly a failed write. Reporting success
        while the authorization table still holds rows for this bot makes the
        two disagree with no signal that they do.

        The cost is worth naming rather than hiding: the deletion really has
        happened by then, so a caller who retries is answered "no such bot".
        That is a confusing sequence, but it is an honest one — where silent
        success is a wrong answer that no one can later discover.

        A non-zero count here means the race actually happened, which is worth
        seeing in the log rather than inferring later from an orphan row.
        """
        raced = self._bot_app_grant_provider().revoke_all_for_bot(
            bot_id=bot_id, owner_id=user_id
        )
        if raced:
            logger.warning(
                "[bot_service.delete_bot] withdrew %s app authorization(s) "
                "created on bot %s while it was being deleted",
                raced,
                bot_id,
            )

    def _sync_provider_bot_delete_to_bcn(
        self, bot_id: str, user_id: str, *, provider_bot_ref: str | None = None,
    ) -> None:
        """Best-effort sync of local bot deletion to BCN provider bot deletion."""
        try:
            address = {} if provider_bot_ref is None else {"provider_bot_ref": provider_bot_ref}
            result = self._bcn_service.delete_provider_bot(
                teamclaw_bot_uuid=bot_id,
                owner_workno=user_id,
                **address,
            )
            logger.info(
                f"[bot_service._sync_provider_bot_delete_to_bcn] "
                f"provider bot delete synced: bot_id={bot_id}, "
                f"owner_workno={user_id}, result={result}"
            )
        except Exception as e:
            logger.error(
                f"[bot_service._sync_provider_bot_delete_to_bcn] "
                f"Failed to sync provider bot delete, ignored: "
                f"bot_id={bot_id}, owner_workno={user_id}, error={e}",
                exc_info=True,
            )

    def _sweep_startup_script_that_raced_the_deletion(
        self, bot_id: str, bot: Dict[str, Any]
    ) -> None:
        """Second purge, after the soft delete. Paired with the first, and the
        pairing is what makes the set of orderings complete.

        The first purge runs early, while the bot is still intact, so a failure
        aborts the deletion rather than stranding the row. But it is a long way
        from the soft delete: ``release_device`` and ``destroy_passport`` — the
        latter unconditional and a blocking call to an external plugin — both
        sit in between. A ``PUT`` whose existence check, write **and** post-write
        re-check all fall inside that gap sees a live bot at every point and
        leaves its row behind. Neither the first purge nor the re-check catches
        that ordering; only a purge on the far side of ``soft_delete_by_owner``
        does.

        With this in place the orderings are, for a write W, its re-check R, the
        first purge P1, the soft delete S and this sweep P2:

        * W before P1 — P1 removes it;
        * W between P1 and S, R also before S — R sees a live bot and answers
          200, and P2 removes the row;
        * W between P1 and S, R after S — R sees the bot gone and withdraws;
        * W after S — R is later still, so R withdraws, and P2 covers it too.

        That assumes R reads committed state; a re-check served stale would fall
        back on P2 for the third case.

        Unconditional, and it needs no owner stamp. ``uk_bot_id_entity_id_env``
        means the identifier cannot be handed to another bot, so whatever sits
        at this key belongs to the bot being deleted.

        Failures propagate, like the first purge and like the grant sweep. The
        deletion has really happened by then, so a caller who retries is told
        "no such bot" — confusing, but honest, where reporting success over a
        row that survived is a wrong answer nobody can later discover.
        """
        if self._purge_startup_script(bot_id, bot):
            logger.warning(
                "[bot_service.delete_bot] removed a startup script written on "
                "bot %s while it was being deleted",
                bot_id,
            )

    def _purge_startup_script(self, bot_id: str, bot: Dict[str, Any]) -> bool:
        """Delete the bot's stored startup script. Failures propagate.

        Keyed by ``entity_id``, not the owner id. The two are usually equal —
        this file says so in several places — but under a team entity they are
        not, and using the owner would look correct in every single-owner case
        while missing the row for every team-owned bot.

        A bot with no ``entity_id`` never had a script: the write path requires
        both halves of the key. There is nothing to delete and no id to invent.

        Two call sites, and their failure semantics differ because the bot is in
        a different state at each. The first runs before the soft delete, while
        the bot is still intact, so a failure aborts the deletion and leaves it
        retryable. The second is
        :meth:`_sweep_startup_script_that_raced_the_deletion`, after the soft
        delete, catching a write that landed in the gap between the two; a
        failure there surfaces as well, but the bot is already gone by then, so
        it cannot abort anything.
        """
        entity_id = str(bot.get("entity_id") or "")
        if not entity_id:
            return False
        return self._cleanup_service.purge_startup_script(
            entity_id=entity_id, bot_id=bot_id
        )

    def _cleanup_bot_associated_data(self, bot_id: str, user_id: str) -> Dict[str, Any]:
        """
        清理 Bot 关联的脏数据（技能、技能集、资源等）

        注意：此方法仅应在确认 Bot 真正被删除时调用（非 default bot 的重启场景）

        启动脚本不在此列：它在软删*之前*由 ``_purge_startup_script`` 单独删除，
        失败要阻断删除而不是记录后继续。见那里的说明。

        Args:
            bot_id: Bot ID
            user_id: 用户ID

        Returns:
            清理结果统计
        """
        logger.info(f"[bot_service._cleanup_bot_associated_data] Cleaning up data for bot {bot_id}, user {user_id}")

        result = {
            "skills_deleted": 0,
            "skill_sets_deleted": 0,
            "resources_deleted": 0,
            "skill_installations_deleted": 0,
            "mcp_installations_deleted": 0,
            "errors": []
        }

        try:
            # 使用 cleanup_service 清理单个 bot 的数据
            cleanup_result = self._cleanup_service.cleanup_single_bot_data(bot_id, user_id)

            result["skills_deleted"] = cleanup_result.get("skills_deleted", 0)
            result["skill_sets_deleted"] = cleanup_result.get("skill_sets_deleted", 0)
            result["resources_deleted"] = cleanup_result.get("resources_deleted", 0)
            result["skill_installations_deleted"] = cleanup_result.get(
                "skill_installations_deleted", 0
            )
            result["mcp_installations_deleted"] = cleanup_result.get(
                "mcp_installations_deleted", 0
            )
            result["errors"].extend(cleanup_result.get("errors", []))

            if result["errors"]:
                logger.warning(
                    "[bot_service._cleanup_bot_associated_data] Cleanup incomplete "
                    "for bot %s: errors=%s",
                    bot_id,
                    result["errors"],
                )
            else:
                logger.info(
                    f"[bot_service._cleanup_bot_associated_data] Cleanup completed for bot {bot_id}: "
                    f"skills={result['skills_deleted']}, skill_sets={result['skill_sets_deleted']}, "
                    f"resources={result['resources_deleted']}"
                )

        except Exception as e:
            error_msg = f"Cleanup error for bot {bot_id}: {e}"
            logger.error(f"[bot_service._cleanup_bot_associated_data] {error_msg}")
            result["errors"].append(error_msg)

        return result

    def check_bot_name_exists(self, bot_name: str) -> bool:
        """
        Check if a bot with specific bot_name exists globally.

        Args:
            bot_name: Bot name to check

        Returns:
            True if exists, False otherwise
        """
        if not bot_name or not bot_name.strip():
            return False

        return self._repository.exists_by_bot_name(bot_name.strip())
