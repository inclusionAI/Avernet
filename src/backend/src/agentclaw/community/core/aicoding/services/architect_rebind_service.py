"""Architect-bot rebind service (aicoding-creation specific).

Moved out of the generic ``BotService`` because one-click rebind of
application-coding bots onto a domain architect bot is an aicoding-creation
concern, not general bot lifecycle. The ownership/behaviour contract is
unchanged from the original ``BotService`` implementation:

- owner boundary lives on the **architect** side (owner-scoped 403);
- per-coding-bot helper only validates existence + ``template_type``;
- batch flow returns a per-item ``error_code`` map, single failure does not
  abort the rest, and success items never leak the template/token ciphertext.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from injector import inject

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotPermissionError,
    BotServiceError,
)
from agentclaw.community.core.bot_management.services.template_service import TemplateService
from agentclaw.community.log import get_logger

logger = get_logger()


class ArchitectRebindService:
    """Bind a batch of application-coding bots to a domain architect bot."""

    @inject
    def __init__(
        self,
        repository: BotRepository,
        template_service: TemplateService,
    ) -> None:
        self._repository = repository
        self._template_service = template_service

    def _get_architect_domain_or_raise(self, architect_bot_id: str, operator_id: str) -> Dict[str, Any]:
        """校验并返回 domain architect bot（owner-scoped，不存在/非本人所有 -> 403）。"""
        architect = self._repository.get_by_id_and_owner(architect_bot_id, operator_id)
        if not architect:
            raise BotPermissionError(f"架构师 Bot 不存在或非本人所有: {architect_bot_id}")
        arch_ext = architect.get("ext") or {}
        if isinstance(arch_ext, str):
            try:
                arch_ext = json.loads(arch_ext)
            except (ValueError, TypeError):
                arch_ext = {}
        if not (isinstance(arch_ext, dict) and arch_ext.get("is_domain_bot") is True):
            raise BotServiceError(
                f"目标 Bot 不是架构师 Bot (is_domain_bot != true): {architect_bot_id}"
            )
        return architect

    def _rebind_coding_bot_to_architect(
        self, coding_bot_id: str, architect_bot_id: str, operator_id: str,
    ) -> Dict[str, Any]:
        """换绑单个应用 coding bot 的核心逻辑（owner/架构师校验由调用方负责）。

        Args:
            coding_bot_id: template_type == "applicationCoding" 的 bot_id
            architect_bot_id: 目标架构师 bot（调用方已校验为 domain + owner）
            operator_id: 工号，仅用于审计日志

        Returns:
            {bot_id, architect_bot_id, previous_architect_bot_id, changed, template}

        Raises:
            BotNotFoundError: coding bot 不存在
            BotServiceError: 非 applicationCoding 或模板缺失
        """
        coding_bot = self._repository.get_by_id(coding_bot_id)
        if not coding_bot:
            raise BotNotFoundError(f"应用 Coding Bot 不存在: {coding_bot_id}")
        if coding_bot.get("template_type") != "applicationCoding":
            raise BotServiceError(
                f"目标 Bot 不是应用 Coding Bot (template_type != applicationCoding): {coding_bot_id}"
            )

        template = self._template_service.get_template(coding_bot_id)
        if not template:
            raise BotServiceError(f"应用 Coding Bot 模板不存在 (ac_templates): {coding_bot_id}")
        ext = template.get("ext")
        if not isinstance(ext, dict):
            ext = {}
        previous_architect_bot_id = ext.get("architect_bot_id")
        if previous_architect_bot_id == architect_bot_id:
            logger.info(
                "[architect_rebind_service.rebind_architect_bot] no-op, coding bot %s already bound to %s",
                coding_bot_id, architect_bot_id,
            )
            return {
                "bot_id": coding_bot_id,
                "architect_bot_id": architect_bot_id,
                "previous_architect_bot_id": previous_architect_bot_id,
                "changed": False,
            }

        new_ext = dict(ext)
        new_ext["architect_bot_id"] = architect_bot_id
        updated_template = self._template_service.update_template(
            coding_bot_id, new_ext, template_type="applicationCoding",
        )
        logger.info(
            "[architect_rebind_service.rebind_architect_bot] coding bot %s rebind %s -> %s by %s",
            coding_bot_id, previous_architect_bot_id, architect_bot_id, operator_id,
        )
        return {
            "bot_id": coding_bot_id,
            "architect_bot_id": architect_bot_id,
            "previous_architect_bot_id": previous_architect_bot_id,
            "changed": True,
            "template": updated_template,
        }

    def rebind_architect_bot(
        self, coding_bot_id: str, architect_bot_id: str, operator_id: str,
    ) -> Dict[str, Any]:
        """把单个应用 coding bot 绑定到指定架构师 bot。

        Args:
            coding_bot_id: applicationCoding bot 的 bot_id
            architect_bot_id: domain 架构师 bot 的 bot_id
            operator_id: 工号

        Returns:
            {bot_id, architect_bot_id, previous_architect_bot_id, changed, template}

        Raises:
            BotPermissionError: 架构师 bot 不存在或非本人所有
            BotNotFoundError: coding bot 不存在
            BotServiceError: bot 类型不符或模板缺失
        """
        if not coding_bot_id or not architect_bot_id:
            raise BotServiceError("coding_bot_id 与 architect_bot_id 不能为空")
        if coding_bot_id == architect_bot_id:
            raise BotServiceError("coding_bot_id 与 architect_bot_id 不能相同")
        self._get_architect_domain_or_raise(architect_bot_id, operator_id)
        return self._rebind_coding_bot_to_architect(coding_bot_id, architect_bot_id, operator_id)

    def rebind_architect_bot_batch(
        self, coding_bot_ids: List[str], architect_bot_id: str, operator_id: str,
    ) -> Dict[str, Any]:
        """把多个应用 coding bot 批量绑定到同一架构师 bot（去重保序，单条失败不影响其余）。

        Args:
            coding_bot_ids: applicationCoding bot 的 bot_id 列表
            architect_bot_id: domain 架构师 bot 的 bot_id
            operator_id: 工号

        Returns:
            {architect_bot_id, results[], total, succeeded, failed}；result 不含 token。

        Raises:
            BotPermissionError: 架构师 bot 不存在或非本人所有（整批拒绝）
            BotServiceError: 架构师非 domain 或参数非法（整批拒绝）
        """
        if not architect_bot_id:
            raise BotServiceError("architect_bot_id 不能为空")
        if not coding_bot_ids:
            raise BotServiceError("coding_bot_ids 不能为空")

        seen = set()
        uniq_ids: List[str] = []
        for bid in coding_bot_ids:
            if isinstance(bid, str) and bid and bid not in seen:
                seen.add(bid)
                uniq_ids.append(bid)
        if not uniq_ids:
            raise BotServiceError("coding_bot_ids 不能为空")
        if architect_bot_id in seen:
            raise BotServiceError("coding_bot_id 不能与 architect_bot_id 相同")

        self._get_architect_domain_or_raise(architect_bot_id, operator_id)

        results = []
        succeeded = 0
        failed = 0
        for coding_bot_id in uniq_ids:
            try:
                one = self._rebind_coding_bot_to_architect(coding_bot_id, architect_bot_id, operator_id)
                results.append({
                    "bot_id": coding_bot_id,
                    "success": True,
                    "changed": one.get("changed"),
                    "previous_architect_bot_id": one.get("previous_architect_bot_id"),
                    "architect_bot_id": architect_bot_id,
                })
                succeeded += 1
            except BotNotFoundError as e:
                failed += 1
                results.append({
                    "bot_id": coding_bot_id, "success": False,
                    "error_code": "not_found", "message": str(e),
                })
            except BotPermissionError as e:
                failed += 1
                results.append({
                    "bot_id": coding_bot_id, "success": False,
                    "error_code": "forbidden", "message": str(e),
                })
            except BotServiceError as e:
                failed += 1
                results.append({
                    "bot_id": coding_bot_id, "success": False,
                    "error_code": "invalid", "message": str(e),
                })
            except Exception as e:
                logger.error(
                    "[architect_rebind_service.rebind_architect_bot_batch] %s failed: %s",
                    coding_bot_id, e, exc_info=True,
                )
                failed += 1
                results.append({
                    "bot_id": coding_bot_id, "success": False,
                    "error_code": "error", "message": str(e),
                })

        logger.info(
            "[architect_rebind_service.rebind_architect_bot_batch] architect=%s total=%d succeeded=%d failed=%d by %s",
            architect_bot_id, len(uniq_ids), succeeded, failed, operator_id,
        )
        return {
            "architect_bot_id": architect_bot_id,
            "results": results,
            "total": len(uniq_ids),
            "succeeded": succeeded,
            "failed": failed,
        }
