"""Shared transaction primitives for guarded Device lifecycle writes."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func

from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
from agentclaw.community.plugin_api.models import BotModel


_PENDING = "PENDING"
_ACTIVE = "ACTIVE"


def _normalize_isolation_level(value: str) -> str:
    return " ".join(
        value.replace("_", " ").replace("-", " ").upper().split()
    )


def begin_guarded_transaction(db: Any, *, purpose: str) -> None:
    """Start the explicit transaction required by guarded lifecycle writes."""
    if db.in_transaction():
        raise RuntimeError(
            f"{purpose} requires a fresh ORM Session before selecting "
            "transaction isolation"
        )
    dialect_name = db.get_bind().dialect.name
    isolation_level = "SERIALIZABLE" if dialect_name == "sqlite" else "READ COMMITTED"
    connection = db.connection(
        execution_options={"isolation_level": isolation_level}
    )
    actual_isolation = _normalize_isolation_level(connection.get_isolation_level())
    expected_isolation = _normalize_isolation_level(isolation_level)
    if actual_isolation != expected_isolation:
        db.rollback()
        raise RuntimeError(
            f"{purpose} transaction isolation mismatch: expected "
            f"{expected_isolation}, got {actual_isolation}"
        )
    if dialect_name == "sqlite":
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def load_device_props(raw: Any) -> dict[str, Any]:
    """Decode a Binding JSON value without trusting historical row shape."""
    try:
        props = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        return {}
    return props if isinstance(props, dict) else {}


class BaasDesktopRestartRepositoryMixin:
    """Atomic persistence seam for the Desktop BaaS restart boundary."""

    _db: Any

    def _bot_env(self): ...

    def prepare_baas_desktop_restart(
        self,
        *,
        binding_id: int,
        bot_id: str,
        owner_id: str,
        bot_ext_patch: dict[str, Any],
        binding_props_patch: dict[str, Any],
    ) -> bool:
        with self._db.orm_session() as db:
            try:
                begin_guarded_transaction(db, purpose="BaaS Desktop restart prepare")
                binding = (
                    db.query(EntityDeviceBinding)
                    .filter(EntityDeviceBinding.id == binding_id)
                    .with_for_update()
                    .one_or_none()
                )
                if (
                    binding is None
                    or binding.device_provider != "baas"
                    or binding.status not in {_PENDING, _ACTIVE}
                ):
                    db.rollback()
                    return False
                bot = (
                    db.query(BotModel)
                    .filter(
                        BotModel.bot_id == bot_id,
                        BotModel.owner_id == owner_id,
                        BotModel.binding_id == binding_id,
                        BotModel.is_delete == 0,
                        self._bot_env(),
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if bot is None:
                    db.rollback()
                    return False

                bot_ext = load_device_props(bot.ext)
                bot_ext.update(bot_ext_patch)
                binding_props = load_device_props(binding.device_props)
                binding_props.update(binding_props_patch)
                bot.ext = json.dumps(bot_ext, ensure_ascii=False)
                bot.status = _PENDING
                bot.gmt_modified = func.now()
                binding.device_props = json.dumps(
                    binding_props, ensure_ascii=False
                )
                binding.status = _PENDING
                binding.gmt_modified = func.now()
                db.commit()
                return True
            except Exception:
                db.rollback()
                raise
