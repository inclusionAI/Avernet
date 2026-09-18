"""Serialize employee association changes on the existing Bot aggregate row."""
import json
from typing import Any

from injector import inject

from agentclaw.community.core.digital_employee.contracts import DigitalEmployeeError, DigitalEmployeeRepositoryProtocol
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.env_utils import get_current_env


def _ext(row: BotModel) -> dict[str, Any]:
    result = json.loads(row.ext) if row.ext else {}
    if not isinstance(result, dict):
        raise DigitalEmployeeError("Bot metadata is invalid")
    return result


def _bot(row: BotModel) -> dict[str, Any]:
    return {**{column.name: getattr(row, column.name) for column in row.__table__.columns},
            "ext": _ext(row)}


class DigitalEmployeeRepository(DigitalEmployeeRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def _query(self, session, bot_pk: int):
        return session.query(BotModel).filter(
            BotModel.id == bot_pk, BotModel.env == get_current_env(),
            BotModel.is_delete == 0,
        )

    def get_bot(self, bot_pk: int) -> dict[str, Any]:
        with self._db.orm_session() as session:
            row = self._query(session, bot_pk).one_or_none()
            if row is None:
                raise DigitalEmployeeError("Bot not found")
            return _bot(row)

    def list_service_bots(self, creator_no: str) -> list[dict[str, Any]]:
        with self._db.orm_session() as session:
            rows = session.query(BotModel).filter(
                BotModel.creator_id == creator_no, BotModel.bot_type == "service",
                BotModel.env == get_current_env(), BotModel.is_delete == 0,
            ).order_by(BotModel.id).all()
            return [_bot(row) for row in rows]

    def begin_binding(self, bot_pk: int, work_no: str, event_id: str,
                      detail: dict[str, Any]) -> dict[str, Any]:
        with self._db.transactional_orm_session() as session:
            row = self._query(session, bot_pk).with_for_update().one()
            if row.bot_type != "service":
                raise DigitalEmployeeError("Only service Bots may bind digital employees")
            ext = _ext(row)
            current = ext.get("digital_employee")
            if current:
                if current.get("work_no") != work_no:
                    raise DigitalEmployeeError("Bot already has a different employee binding")
                return current
            binding = {"work_no": work_no, "event_id": event_id, "status": "BINDING",
                       "phase": "DETAIL_READY", "name": detail.get("name", ""),
                       "platform_code": detail["platformCode"], "agent_id": detail["agentId"]}
            ext["digital_employee"] = binding
            row.ext = json.dumps(ext, ensure_ascii=False)
            session.flush()
            return binding

    def change_binding_phase(self, bot_pk: int, work_no: str, expected: str,
                             phase: str) -> bool:
        with self._db.transactional_orm_session() as session:
            row = self._query(session, bot_pk).with_for_update().one()
            ext = _ext(row)
            binding = ext.get("digital_employee") or {}
            if binding.get("work_no") != work_no or binding.get("phase") != expected:
                return False
            binding["phase"] = phase
            if phase == "ACTIVE":
                binding["status"] = "ACTIVE"
            row.ext = json.dumps(ext, ensure_ascii=False)
            session.flush()
            return True
