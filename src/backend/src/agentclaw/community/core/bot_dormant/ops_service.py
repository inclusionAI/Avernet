"""Ops-only helpers for manually exercising dormant-bot lifecycle paths."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from injector import inject

from agentclaw.community.core.bot_dormant.service import Candidate, DormantBotService
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugin_api.passport import PassportPlugin


logger = get_logger()


class DormantOpsService:
    """Small operations facade that reuses the production dormant recycle path."""

    @inject
    def __init__(
        self,
        dormant_service: DormantBotService,
        passport_plugin: PassportPlugin,
    ) -> None:
        self._dormant_service = dormant_service
        self._passport = passport_plugin

    def unfreeze_passport_one(
        self,
        *,
        bot_id: str,
        owner_id: str,
        reason: str,
    ) -> dict:
        """Bring one Bot passport online without changing Bot lifecycle state."""
        logger.info(
            "[dormant.ops.unfreeze_passport_one] event=start "
            "bot_id=%s owner_id=%s reason=%s",
            bot_id,
            owner_id,
            reason,
        )
        try:
            self._passport.unfreeze_agent_passport(
                bot_id=bot_id,
                owner_workno=owner_id,
                reason=reason,
            )
        except Exception:
            logger.exception(
                "[dormant.ops.unfreeze_passport_one] event=failed "
                "bot_id=%s owner_id=%s reason=%s",
                bot_id,
                owner_id,
                reason,
            )
            raise
        logger.info(
            "[dormant.ops.unfreeze_passport_one] event=done "
            "bot_id=%s owner_id=%s reason=%s",
            bot_id,
            owner_id,
            reason,
        )
        return {
            "bot_id": bot_id,
            "owner_id": owner_id,
            "status": "passport_online",
        }

    def recycle_one(
        self,
        *,
        bot_id: str,
        owner_id: str,
        dry_run: bool = True,
        reason: str | None = None,
    ) -> dict:
        """Recycle exactly one ACTIVE personal bot through the dormant path."""
        run_id = f"manual-recycle-{uuid.uuid4()}"
        logger.info(
            "[dormant.ops.recycle_one] event=start run_id=%s bot_id=%s "
            "owner_id=%s dry_run=%s reason=%s",
            run_id, bot_id, owner_id, dry_run, reason,
        )
        with self._dormant_service._db.orm_session() as session:
            bot = (
                session.query(BotModel)
                .filter(
                    BotModel.bot_id == bot_id,
                    BotModel.owner_id == owner_id,
                    BotModel.is_delete == 0,
                )
                .order_by(BotModel.gmt_modified.desc(), BotModel.id.desc())
                .first()
            )
            if bot is None:
                logger.warning(
                    "[dormant.ops.recycle_one] event=missing_bot run_id=%s "
                    "bot_id=%s owner_id=%s",
                    run_id, bot_id, owner_id,
                )
                raise ValueError(f"bot not found: bot_id={bot_id} owner_id={owner_id}")

            status = getattr(bot, "status", None)
            bot_type = getattr(bot, "bot_type", None)
            if status != "ACTIVE":
                logger.warning(
                    "[dormant.ops.recycle_one] event=invalid_status run_id=%s "
                    "bot_id=%s owner_id=%s status=%s",
                    run_id, bot_id, owner_id, status,
                )
                raise ValueError(
                    f"only ACTIVE bot can be manually recycled, current: {status}"
                )
            if bot_type != "personal":
                logger.warning(
                    "[dormant.ops.recycle_one] event=invalid_bot_type run_id=%s "
                    "bot_id=%s owner_id=%s bot_type=%s",
                    run_id, bot_id, owner_id, bot_type,
                )
                raise ValueError(
                    f"only personal bot can be manually recycled, current: {bot_type}"
                )

            now = datetime.now(UTC).replace(tzinfo=None)
            gmt_create = getattr(bot, "gmt_create", None) or now
            days_inactive = max((now - gmt_create).days, 0)
            candidate = Candidate(
                bot_id=bot.bot_id,
                entity_id=bot.entity_id,
                owner_id=bot.owner_id,
                bot_name=bot.bot_name,
                gmt_create=gmt_create,
            )
            self._dormant_service._enqueue_recycle(
                session, candidate, days_inactive, dry_run
            )
            self._dormant_service._execute_recycle(candidate, dry_run)
            self._dormant_service._write_audit(
                session,
                run_id=run_id,
                bot_id=bot_id,
                owner_id=owner_id,
                check_result="inactive",
                action_taken="recycled",
                days_inactive=days_inactive,
                dry_run=dry_run,
                source="manual_ops",
            )
        result = {
            "run_id": run_id,
            "bot_id": bot_id,
            "owner_id": owner_id,
            "dry_run": dry_run,
            "status": "dry_run_recycled" if dry_run else "recycled",
        }
        logger.info(
            "[dormant.ops.recycle_one] event=done run_id=%s bot_id=%s "
            "owner_id=%s dry_run=%s status=%s",
            run_id, bot_id, owner_id, dry_run, result["status"],
        )
        return result
