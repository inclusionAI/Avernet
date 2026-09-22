"""Shared transaction primitives for guarded Device lifecycle writes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func

from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
from agentclaw.community.core.devices.protocols import (
    LAYOUT_CONFIRMED_STARTUP_IDENTITY_KEY,
)
from agentclaw.community.core.devices.startup_identity import resolve_startup_identity
from agentclaw.community.plugin_api.models import BotModel


_PENDING = "PENDING"
_ACTIVE = "ACTIVE"
_FAILED = "FAILED"
_DATA_INIT_TRIGGER_CLAIM_TIMEOUT_SECONDS = 10 * 60


def _utc_timestamp_expired(raw: Any, *, timeout_seconds: int) -> bool:
    if not isinstance(raw, str) or not raw:
        return False
    try:
        timestamp = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - timestamp).total_seconds() > timeout_seconds


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


class BaasDesktopLifecycleRepositoryMixin:
    """Atomic persistence seams for guarded Desktop BaaS lifecycle writes."""

    _db: Any

    def _bot_env(self): ...

    def detach_released_baas_desktop_binding_if_matches(
        self,
        *,
        binding_id: int,
        bot_id: str,
        owner_id: str,
        device_id: str,
        entity_id: str,
        env: str,
        expected_client_id: str,
        expected_callback_token: str,
    ) -> bool:
        with self._db.orm_session() as db:
            try:
                begin_guarded_transaction(
                    db, purpose="BaaS Desktop retained binding detach"
                )
                binding = (
                    db.query(EntityDeviceBinding)
                    .filter(EntityDeviceBinding.id == binding_id)
                    .with_for_update()
                    .one_or_none()
                )
                persisted_props = (
                    load_device_props(binding.device_props)
                    if binding is not None
                    else {}
                )
                if (
                    binding is None
                    or binding.status != "RELEASED"
                    or binding.device_provider != "baas"
                    or binding.device_id != device_id
                    or binding.entity_id != entity_id
                    or binding.entity_type != "staff"
                    or binding.env != env
                    or persisted_props.get("client_id") != expected_client_id
                    or persisted_props.get("callback_token")
                    != expected_callback_token
                ):
                    db.rollback()
                    return False
                bot = (
                    db.query(BotModel)
                    .filter(
                        BotModel.bot_id == bot_id,
                        BotModel.owner_id == owner_id,
                        BotModel.binding_id == binding_id,
                        BotModel.device_id == device_id,
                        BotModel.status == _PENDING,
                        BotModel.is_delete == 0,
                        self._bot_env(),
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if bot is None:
                    db.rollback()
                    return False
                bot.binding_id = None
                bot.device_id = None
                bot.gmt_modified = func.now()
                db.commit()
                return True
            except Exception:
                db.rollback()
                raise

    def recover_baas_desktop_creation_binding_if_matches(
        self,
        *,
        binding_id: int,
        bot_id: str,
        owner_id: str,
        device_id: str,
        entity_id: str,
        env: str,
        expected_client_id: str,
        expected_callback_token: str,
        device_props: dict[str, Any],
        apply_reason: str | None,
        applied_by: str,
    ) -> bool:
        with self._db.orm_session() as db:
            try:
                begin_guarded_transaction(
                    db, purpose="BaaS Desktop create binding reuse"
                )
                binding = (
                    db.query(EntityDeviceBinding)
                    .filter(EntityDeviceBinding.id == binding_id)
                    .with_for_update()
                    .one_or_none()
                )
                persisted_props = (
                    load_device_props(binding.device_props)
                    if binding is not None
                    else {}
                )
                if (
                    binding is None
                    or binding.status not in {_PENDING, "RELEASED"}
                    or binding.device_provider != "baas"
                    or binding.device_id != device_id
                    or binding.entity_id != entity_id
                    or binding.entity_type != "staff"
                    or binding.env != env
                    or persisted_props.get("client_id") != expected_client_id
                    or persisted_props.get("callback_token")
                    != expected_callback_token
                ):
                    db.rollback()
                    return False
                bot = (
                    db.query(BotModel)
                    .filter(
                        BotModel.bot_id == bot_id,
                        BotModel.owner_id == owner_id,
                        BotModel.status == "PROVISIONING",
                        BotModel.is_delete == 0,
                        self._bot_env(),
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if (
                    bot is None
                    or bot.binding_id not in {None, binding_id}
                    or bot.device_id not in {None, device_id}
                ):
                    db.rollback()
                    return False
                competing_link = (
                    db.query(BotModel.id)
                    .filter(
                        BotModel.binding_id == binding_id,
                        BotModel.id != bot.id,
                        BotModel.is_delete == 0,
                        self._bot_env(),
                    )
                    .with_for_update()
                    .first()
                )
                if competing_link is not None:
                    db.rollback()
                    return False
                binding.device_props = json.dumps(
                    device_props, ensure_ascii=False
                )
                binding.status = _PENDING
                binding.apply_reason = apply_reason
                binding.applied_by = applied_by
                binding.release_reason = None
                binding.released_by = None
                binding.released_at = None
                binding.last_alive_at = None
                binding.gmt_modified = func.now()
                db.commit()
                return True
            except Exception:
                db.rollback()
                raise

    def claim_baas_desktop_data_init_trigger_if_ready(
        self,
        *,
        binding_id: int,
        device_id: str,
        startup_identity: str,
    ) -> str | None:
        marker_key = "data_init_triggered_startup_identity"
        token_key = "data_init_trigger_claim_token"
        claimed_at_key = "data_init_trigger_claimed_at"
        with self._db.orm_session() as db:
            try:
                begin_guarded_transaction(
                    db, purpose="BaaS Desktop data-init trigger claim"
                )
                binding = (
                    db.query(EntityDeviceBinding)
                    .filter(EntityDeviceBinding.id == binding_id)
                    .with_for_update()
                    .one_or_none()
                )
                props = (
                    load_device_props(binding.device_props)
                    if binding is not None
                    else {}
                )
                if (
                    binding is None
                    or binding.device_id != device_id
                    or binding.device_provider != "baas"
                    or binding.status != _ACTIVE
                    or resolve_startup_identity(props) != startup_identity
                    or str(
                        props.get(LAYOUT_CONFIRMED_STARTUP_IDENTITY_KEY) or ""
                    )
                    != startup_identity
                ):
                    db.rollback()
                    return None
                bot = (
                    db.query(BotModel)
                    .filter(
                        BotModel.binding_id == binding_id,
                        BotModel.is_delete == 0,
                        self._bot_env(),
                    )
                    .with_for_update()
                    .one_or_none()
                )
                bot_ext = load_device_props(bot.ext) if bot is not None else {}
                claim_is_current = (
                    str(props.get(marker_key) or "") == startup_identity
                    and bool(str(props.get(token_key) or ""))
                )
                claim_is_expired = claim_is_current and _utc_timestamp_expired(
                    props.get(claimed_at_key),
                    timeout_seconds=_DATA_INIT_TRIGGER_CLAIM_TIMEOUT_SECONDS,
                )
                data_init_status = bot_ext.get("data_init_status")
                in_progress_is_stale = (
                    data_init_status == "in_progress"
                    and claim_is_expired
                    and _utc_timestamp_expired(
                        bot_ext.get("data_init_started_at"),
                        timeout_seconds=_DATA_INIT_TRIGGER_CLAIM_TIMEOUT_SECONDS,
                    )
                )
                if (
                    bot is None
                    or bot.device_id != device_id
                    or bot.status != _ACTIVE
                    or bot_ext.get("start_status") != "SUCCEEDED"
                    or data_init_status not in {"pending_init", "failed", "in_progress"}
                    or (data_init_status == "in_progress" and not in_progress_is_stale)
                    or (claim_is_current and not claim_is_expired)
                ):
                    db.rollback()
                    return None
                claim_token = uuid4().hex
                props[marker_key] = startup_identity
                props[token_key] = claim_token
                props[claimed_at_key] = datetime.now(timezone.utc).isoformat()
                binding.device_props = json.dumps(props, ensure_ascii=False)
                binding.gmt_modified = func.now()
                db.commit()
                return claim_token
            except Exception:
                db.rollback()
                raise

    def release_baas_desktop_data_init_trigger_if_matches(
        self,
        *,
        binding_id: int,
        device_id: str,
        startup_identity: str,
        claim_token: str,
    ) -> bool:
        marker_key = "data_init_triggered_startup_identity"
        token_key = "data_init_trigger_claim_token"
        claimed_at_key = "data_init_trigger_claimed_at"
        with self._db.orm_session() as db:
            try:
                begin_guarded_transaction(
                    db, purpose="BaaS Desktop data-init trigger release"
                )
                binding = (
                    db.query(EntityDeviceBinding)
                    .filter(EntityDeviceBinding.id == binding_id)
                    .with_for_update()
                    .one_or_none()
                )
                props = (
                    load_device_props(binding.device_props)
                    if binding is not None
                    else {}
                )
                if (
                    binding is None
                    or binding.device_id != device_id
                    or binding.device_provider != "baas"
                    or binding.status != _ACTIVE
                    or resolve_startup_identity(props) != startup_identity
                    or str(
                        props.get(LAYOUT_CONFIRMED_STARTUP_IDENTITY_KEY) or ""
                    )
                    != startup_identity
                    or str(props.get(marker_key) or "") != startup_identity
                    or str(props.get(token_key) or "") != claim_token
                ):
                    db.rollback()
                    return False
                props.pop(marker_key, None)
                props.pop(token_key, None)
                props.pop(claimed_at_key, None)
                binding.device_props = json.dumps(props, ensure_ascii=False)
                binding.gmt_modified = func.now()
                db.commit()
                return True
            except Exception:
                db.rollback()
                raise

    def transition_layout_startup_status_if_matches(
        self,
        *,
        binding_id: int,
        startup_identity: str,
        status: str,
        message: str | None,
    ) -> bool:
        if status not in {"SUCCEEDED", "FAILED"}:
            raise ValueError(f"unsupported layout startup status: {status}")
        with self._db.orm_session() as db:
            try:
                begin_guarded_transaction(db, purpose="layout startup callback")
                binding = (
                    db.query(EntityDeviceBinding)
                    .filter(EntityDeviceBinding.id == binding_id)
                    .with_for_update()
                    .one_or_none()
                )
                if (
                    binding is None
                    or binding.status not in {_PENDING, _ACTIVE}
                    or resolve_startup_identity(
                        load_device_props(binding.device_props)
                    )
                    != startup_identity
                ):
                    db.rollback()
                    return False
                bot = (
                    db.query(BotModel)
                    .filter(
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
                ext = load_device_props(bot.ext)
                ext["start_status"] = status
                if message is not None:
                    ext["start_message"] = message
                bot.ext = json.dumps(ext, ensure_ascii=False)
                bot.gmt_modified = func.now()
                if status == "FAILED":
                    bot.status = "FAILED"
                    binding.status = "FAILED"
                    binding.gmt_modified = func.now()
                db.commit()
                return True
            except Exception:
                db.rollback()
                raise

    def prepare_baas_desktop_restart(
        self,
        *,
        binding_id: int,
        bot_id: str,
        owner_id: str,
        expected_publish_id: str | None,
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
                    or binding.status not in {_PENDING, _ACTIVE, _FAILED}
                ):
                    db.rollback()
                    return False
                binding_props = load_device_props(binding.device_props)
                current_publish_id = binding_props.get(
                    "restart_publish_id"
                ) or binding_props.get("publish_id")
                normalized_current = (
                    str(current_publish_id)
                    if current_publish_id is not None
                    else None
                )
                if normalized_current != expected_publish_id:
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
