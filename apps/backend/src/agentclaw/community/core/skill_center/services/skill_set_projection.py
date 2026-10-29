"""Legacy SkillSet projection behavior."""

import asyncio
import time
from typing import Any, Optional

from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.core.skill_center.errors import (
    SkillSetRuntimeReconcileError,
)

from .skill_set_context import (
    logger,
)


class SkillSetRuntimeProjection:
    def _sync_symlinks_to_device_if_needed(
        self,
        user_id: Optional[str] = None,
        desired_skills: Optional[list[dict]] = None,
        effective_mcps: Optional[list[dict]] = None,
    ) -> bool:
        """如果需要，同步软链配置到设备。

        将当前激活技能集的软链配置同步到远程设备或本地文件系统。
        即使软链列表为空也会同步（空列表表示清空设备上的软链）。

        Args:
            user_id: 用户ID
            desired_skills: 调用方已解析的技能快照（可选）
            effective_mcps: 调用方已解析的 MCP 集合（可选）。仅整包投递的设备
                会用到它——它会跳过 compose 里重复的那次 DB 读取。

        Returns:
            True if sync attempted, False otherwise
        """
        try:
            # 获取当前激活技能集的软链配置（包括空列表，表示清空）
            mapping_kwargs = {"user_id": user_id, "bolt_id": self.bot_id}
            if desired_skills is not None:
                mapping_kwargs["desired_skills"] = desired_skills
            symlinks = self.get_symlink_mappings(**mapping_kwargs)

            # Resolve and invoke the provider-specific DeviceSync service.
            effective_user_id = user_id or self.entity_id or "default"
            ctx = self._resolver.resolve_for_bot(self.bot_id, self._device_owner_id or effective_user_id)
            device_sync = self._device_sync_dispatcher.dispatch(ctx)

            logger.info(
                f"[_sync_symlinks_to_device_if_needed] Syncing {len(symlinks)} symlinks to device (bot_id={self.bot_id})"
            )

            symlinks_dict = [sm.to_dict() for sm in symlinks]
            # Passed only when the caller actually resolved it, the same way
            # ``desired_skills`` is threaded above: the keyword means something
            # to a whole-artifact device and nothing to the rest, so a
            # DeviceSync implementation that has no use for it never has to
            # grow a parameter to stay callable from here.
            sync_kwargs: dict[str, Any] = {}
            if effective_mcps is not None:
                sync_kwargs["effective_mcps"] = effective_mcps
            if desired_skills is not None:
                sync_kwargs["desired_skills"] = desired_skills
            sync_result = device_sync.sync_symlinks(symlinks_dict, **sync_kwargs)

            if sync_result.get("success"):
                logger.info(
                    f"[_sync_symlinks_to_device_if_needed] Sync successful: {sync_result.get('message')}"
                )
                return True
            else:
                logger.error(
                    f"[_sync_symlinks_to_device_if_needed] Sync failed: {sync_result.get('message')}"
                )
                return False

        except Exception as e:
            logger.warning(
                f"[_sync_symlinks_to_device_if_needed] Failed to sync symlinks: {e}",
                exc_info=True,
            )
            return False

    async def project_skills(
        self,
        *,
        desired_skills: Optional[list[dict]] = None,
        effective_mcps: Optional[list[dict]] = None,
    ) -> bool:
        """Apply one complete resolver-owned skill snapshot to the runtime.

        Async to match ``project_mcps``: the two halves of the capability
        boundary are one contract and should not differ in calling convention
        just because their internals do.

        The blocking work is dispatched here rather than by callers.
        ``_sync_symlinks_to_device_if_needed`` is synchronous and carries
        device resolution (including a blocking ws-info HTTP call), and on a
        whole-artifact engine a full artifact compose and the outbound apply
        request behind it. Owning the ``to_thread`` here makes staying off the
        event loop a property of this method, which no caller can forget —
        the same reason ``sync_mcp_desired_state`` wraps its own device calls.

        ``effective_mcps`` is the caller's already-resolved MCP set, carried
        for the same reason ``desired_skills`` is: on a whole-artifact engine
        the compose behind this call would otherwise re-read state the caller
        has just read. Meaningless to a device that consumes the symlinks
        directly, which is why it is optional and ignored there.
        """
        return await asyncio.to_thread(
            self._sync_symlinks_to_device_if_needed,
            self.user_id or self.entity_id,
            desired_skills,
            effective_mcps,
        )

    def _project_whole_artifact_sync(
        self,
        desired_skills: list[dict],
        effective_mcps: list[dict] | None,
    ) -> bool:
        """Deliver structured desired state without invoking path mapping."""
        try:
            effective_user_id = self.user_id or self.entity_id or "default"
            ctx = self._resolver.resolve_for_bot(self.bot_id, self._device_owner_id or effective_user_id)
            device_sync = self._device_sync_dispatcher.dispatch(ctx)
            result = device_sync.sync_symlinks(
                [],
                desired_skills=desired_skills,
                effective_mcps=effective_mcps,
            )
            if not result.get("success"):
                raise SkillSetRuntimeReconcileError(
                    str(result.get("message") or "Skill set runtime sync failed")
                )
            return True
        except SkillSetRuntimeReconcileError:
            raise
        except Exception as exc:
            logger.warning(
                "[project_whole_artifact] delivery failed: %s", exc, exc_info=True
            )
            return False

    async def project_whole_artifact(
        self,
        *,
        desired_skills: list[dict],
        effective_mcps: list[dict] | None = None,
    ) -> bool:
        return await asyncio.to_thread(
            self._project_whole_artifact_sync,
            desired_skills,
            effective_mcps,
        )

    async def project_mcps(
        self,
        *,
        claimed: frozenset[str],
        released: frozenset[str],
        declared: set[str],
    ) -> bool:
        """Apply one MCP projection to this Bot's device.

        The projector's single MCP entry point. It hands over what the
        mutation changed and what the Bot should end up holding; deciding how
        many device calls that takes, and in what order, belongs here, because
        this service — not the projector — owns device resolution.

        The order is the invariant, not an implementation detail:
        configuration lands before the allow-list cites it, and is withdrawn
        only after the allow-list stops covering it, so the device never
        references an MCP it has no configuration for.

        Both halves must run. A change that only releases still has to
        re-declare the smaller allow-list, and a change that only claims still
        has to declare the larger one — the declaration is a full replacement,
        so skipping it would leave the device's view of the set behind.
        """
        started = time.perf_counter()
        try:
            ctx = await asyncio.to_thread(
                self._resolver.resolve_for_bot,
                self.bot_id,
                self._device_owner_id or self.user_id or self.entity_id or "",
            )
            device_sync = self._device_sync_dispatcher.dispatch(ctx)
        except Exception:
            logger.warning(
                "[project_mcps] device resolution failed bot_id=%s",
                self.bot_id,
                exc_info=True,
            )
            return False
        finally:
            logger.info(
                "[project_mcps] timing stage=resolve_device bot_id=%s duration_ms=%.3f",
                self.bot_id,
                (time.perf_counter() - started) * 1000,
            )
        if claimed and not await self.sync_mcp_delivery(
            claimed=claimed, released=frozenset(), device_sync=device_sync
        ):
            return False
        if not await self.sync_mcp_desired_state(
            server_codes=declared, device_sync=device_sync
        ):
            return False
        if released and not await self.sync_mcp_delivery(
            claimed=frozenset(), released=released, device_sync=device_sync
        ):
            return False
        return True

    async def sync_mcp_delivery(
        self,
        *,
        claimed: frozenset[str],
        released: frozenset[str],
        device_sync: DeviceSync | None = None,
    ) -> bool:
        """Deliver configuration for newly claimed MCPs, withdraw it for released ones.

        The counterpart to ``sync_mcp_desired_state``: that one *declares* the
        complete allow-list, which is overwrite-style and must always carry
        the whole set. This one *delivers*, which is per-MCP and must only
        touch what actually changed — re-pushing an unchanged MCP rewrites its
        device-side configuration from the DB for no reason, and deleting one
        that is still supplied would break it.

        Both sets are declared by the mutation and already guarded against the
        projected set by the caller, so they are as small as the change was:
        one code for an MCP add or remove, the Set's members for an
        activation. ``sync_mcp_details_for_bot`` resolves the device once for
        the batch, so at one entry that is one device write, not a fan-out.
        """
        if not claimed and not released:
            return True
        try:
            entries: list[dict[str, Any]] = []
            for server_code in sorted(claimed):
                detail = self.mcp_center.get_mcp_detail(server_code)
                if not detail:
                    # Only ever a code we are actually installing — an
                    # unrelated catalogue gap cannot reach here and block the
                    # whole projection.
                    logger.error(
                        "[sync_mcp_delivery] no catalogue detail for %s, bot_id=%s",
                        server_code,
                        self.bot_id,
                    )
                    return False
                entries.append(detail)
            if entries:
                logger.info(
                    "[sync_mcp_delivery] pushing MCP configuration: bot_id=%s, "
                    "mcps=%s, codes=%s",
                    self.bot_id,
                    len(entries),
                    sorted(claimed),
                )
                delivery = await self._mcp_sync_service.sync_mcp_details_for_bot(
                    user_id=self.user_id or self.entity_id or "",
                    mcp_entries=entries,
                    bot_id=self.bot_id,
                    entity_id=self.entity_id,
                    engine_type=self.engine_type,
                    **({"device_sync": device_sync} if device_sync is not None else {}),
                )
                if not delivery.get("success"):
                    logger.error(
                        "[sync_mcp_delivery] MCP configuration push failed: "
                        "bot_id=%s, error=%s",
                        self.bot_id,
                        delivery.get("error"),
                    )
                    return False
            for server_code in sorted(released):
                # WARNING, not INFO: this deletes the MCP's stored endpoint,
                # api_key and headers from the device, and nothing here can
                # put them back.
                logger.warning(
                    "[sync_mcp_delivery] removing MCP configuration from device: "
                    "bot_id=%s, server_code=%s",
                    self.bot_id,
                    server_code,
                )
                removal = await self._mcp_sync_service.remove_mcp_detail(
                    server_code=server_code,
                    bot_id=self.bot_id,
                    user_id=self.entity_id or self.user_id or "",
                    **({"device_sync": device_sync} if device_sync is not None else {}),
                )
                if not removal.get("success"):
                    logger.error(
                        "[sync_mcp_delivery] MCP removal failed: bot_id=%s, "
                        "server_code=%s, error=%s",
                        self.bot_id,
                        server_code,
                        removal.get("error"),
                    )
                    return False
            return True
        except Exception:
            logger.warning(
                "[sync_mcp_delivery] MCP delivery failed for bot_id=%s",
                self.bot_id,
                exc_info=True,
            )
            return False

    async def sync_mcp_desired_state(
        self, *, server_codes: set[str], device_sync: DeviceSync | None = None
    ) -> bool:
        """Declare the complete MCP allow-list to the Bot runtime.

        Declaration is total on purpose: ``sync_all_mcp_servers`` is the
        device-level reconciliation command and clears stale entries, so it
        runs even for an empty set.

        It is *only* declaration. Per-MCP configuration delivery is scoped to
        what a mutation actually changed and lives in ``sync_mcp_delivery``,
        which the caller runs first so configuration lands before the
        allow-list cites it. Folding the two together is what made every
        mutation re-push every MCP the Bot had.
        """
        try:
            # resolve_for_bot 与 sync_all_mcp_servers 都是同步阻塞调用(前者含
            # ws-info HTTP,后者是设备侧 HTTP),留在协程里会占住 event loop。
            if device_sync is None:
                ctx = await asyncio.to_thread(
                    self._resolver.resolve_for_bot,
                    self.bot_id,
                    self._device_owner_id or self.user_id or self.entity_id or "",
                )
                device_sync = self._device_sync_dispatcher.dispatch(ctx)
            logger.info(
                "[sync_mcp_desired_state] declaring MCP allow-list: bot_id=%s, mcps=%s",
                self.bot_id,
                len(server_codes),
            )
            return bool(
                await asyncio.to_thread(
                    device_sync.sync_all_mcp_servers,
                    # ``filter_servers`` reads server_code/serverCode off each
                    # entry, so bare strings would silently declare nothing.
                    [{"server_code": code} for code in sorted(server_codes)],
                )
            )
        except Exception:
            logger.warning(
                "[sync_mcp_desired_state] MCP allow-list declaration failed for bot_id=%s",
                self.bot_id,
                exc_info=True,
            )
            return False
