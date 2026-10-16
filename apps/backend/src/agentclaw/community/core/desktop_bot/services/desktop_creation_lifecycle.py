"""Desktop creation persistence, retry recovery, and remote hand-off."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agentclaw.community.core.desktop_bot.errors import DesktopBotServiceError
from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.devices import (
    DeviceBindingRepository,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.skills_pool.native_creation import (
    SkillsPoolNativeCreationPolicy,
)
from agentclaw.community.core.workspace.constants import (
    DEFAULT_ENGINE_TYPE,
    SUPPORTED_ENGINE_TYPES,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()

_PROVISIONING_CLAIM_STALE_SECONDS = 5 * 60


def generate_desktop_request_id(
    bot_id: str,
    entity_id: str,
    entity_type: str,
    env: str,
    action: str,
) -> str:
    raw = f"{entity_id}_{entity_type}_{bot_id}_{env}_{action}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class DesktopCreationLifecycleHooks:
    layout_credentials_for_scope: Callable[..., dict[str, str]]
    layout_env: Callable[[dict[str, str]], dict[str, str]]
    build_payload: Callable[..., dict[str, Any]]
    release_claim: Callable[..., None]
    start_publish_polling: Callable[..., None]


class DesktopCreationLifecycle:
    """Run one idempotent Desktop create behind a single internal interface."""

    def __init__(
        self,
        *,
        baas: BaasService,
        binding_repository: DeviceBindingRepository,
        bot_repository: BotRepository,
        skill_set_factory: SkillSetServiceFactory,
        native_creation_policy: SkillsPoolNativeCreationPolicy,
        desktop_template_uuid: str | None,
        hooks: DesktopCreationLifecycleHooks,
    ) -> None:
        self._baas = baas
        self._binding_repo = binding_repository
        self._bot_repo = bot_repository
        self._skill_set_factory = skill_set_factory
        self._native_creation_policy = native_creation_policy
        self._desktop_template_uuid = desktop_template_uuid
        self._hooks = hooks

    def execute(
        self,
        *,
        bot: dict[str, Any],
        user_id: str,
        machine_id: str,
        migration_path: str,
        mount_path: str | None,
        agent_code: str,
        engine_type: str | None,
        existing_bot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        bot_id = bot.get("bot_id", "")
        env = get_current_env()
        request_id = generate_desktop_request_id(
            bot_id=bot_id,
            entity_id=bot["entity_id"],
            entity_type=bot["entity_type"],
            env=env,
            action="create",
        )
        if not self._desktop_template_uuid:
            raise DesktopBotServiceError(
                "desktop_template_uuid is not configured; "
                "set baas.desktop_template_uuid in config"
            )

        if existing_bot is None:
            client_id = f"staff_{user_id}_{bot_id}_{uuid.uuid4().hex}"
            callback_token = secrets.token_urlsafe(32)
            initial_layout = self._native_creation_policy.select(
                env=env,
                owner_id=user_id,
                bot_id=str(bot_id),
                engine_type=str(bot.get("active_engine") or DEFAULT_ENGINE_TYPE),
                bot_type="desktop",
            )
            initial_ext: dict[str, Any] = {
                "passport": {"agent_code": agent_code, "status": "ISSUED"},
                "start_status": "PENDING",
                "start_message": "",
                "machine_id": machine_id,
                "mount_path": (mount_path or "").strip(),
                "migration_path": migration_path,
                "desktop_template_uuid": self._desktop_template_uuid or "",
                "workspace_path": f"~/.teamclaw/boxes/{bot_id}",
                "client_id": client_id,
                "callback_token": callback_token,
                "pending_since": datetime.now().isoformat(),
                "skills_layout": "pool" if initial_layout is not None else "legacy",
                "skills_layout_contract_version": (
                    initial_layout.layout_contract_version
                    if initial_layout is not None
                    else ""
                ),
            }
            avatar_url = bot.get("avatar_url")
            if avatar_url:
                initial_ext["avatar_url"] = avatar_url
            self._bot_repo.insert_with_initial_skill_layout(
                {
                    "bot_id": bot_id,
                    "bot_name": bot.get("bot_name", ""),
                    "bot_desc": bot.get("bot_desc"),
                    "entity_id": user_id,
                    "entity_type": "staff",
                    "creator_id": user_id,
                    "owner_id": user_id,
                    "owner_name": bot.get("owner_name", user_id),
                    "engine_types": list(SUPPORTED_ENGINE_TYPES),
                    "active_engine": bot.get("active_engine", DEFAULT_ENGINE_TYPE),
                    "status": "PENDING",
                    "binding_id": None,
                    "device_id": None,
                    "modifier_id": user_id,
                    "is_delete": 0,
                    "bot_type": "desktop",
                    "ext": initial_ext,
                },
                layout=initial_layout,
            )
        else:
            initial_ext = self._load_creation_ext(existing_bot)
            client_id = str(initial_ext.get("client_id") or "")
            callback_token = str(initial_ext.get("callback_token") or "")
            if not client_id or not callback_token:
                raise DesktopBotServiceError(
                    "persisted desktop creation credentials are incomplete"
                )
            self._detach_released_binding_if_present(
                existing_bot=existing_bot,
                bot_id=bot_id,
                user_id=user_id,
                env=env,
                client_id=client_id,
                callback_token=callback_token,
            )

        layout_credentials = self._hooks.layout_credentials_for_scope(
            env=env,
            entity_id=user_id,
            bot_id=bot_id,
        )
        if not self._bot_repo.claim_provisioning(
            bot_id,
            user_id,
            reclaim_after_seconds=_PROVISIONING_CLAIM_STALE_SECONDS,
        ):
            raise DesktopBotServiceError(
                f"Desktop bot creation is already in progress: bot_id={bot_id}"
            )
        try:
            installation_kwargs: dict[str, Any] = {
                "bot_id": bot_id,
                "owner_id": user_id,
            }
            if existing_bot is not None:
                installation_kwargs["bot"] = existing_bot
            self._skill_set_factory.initialize_installations(**installation_kwargs)
        except Exception:
            self._hooks.release_claim(bot_id=bot_id, owner_id=user_id)
            raise

        payload = self._hooks.build_payload(
            bot=bot,
            owner_id=user_id,
            request_id=request_id,
            migration_path=migration_path,
            mount_path=mount_path,
            machine_id=machine_id,
            agent_code=agent_code,
            client_id=client_id,
            callback_token=callback_token,
            engine_type=engine_type,
            layout_credentials=layout_credentials,
        )
        try:
            baas_result = self._baas.post_bots_api(
                path="/api/v1/bots",
                payload=payload,
                action="desktop_create_bot",
            )
        except Exception as error:
            self._hooks.release_claim(bot_id=bot_id, owner_id=user_id)
            raise DesktopBotServiceError(
                f"BaaS create_bot failed: bot_id={bot_id} error={error}"
            ) from error

        bot_uuid = baas_result.get("bot_uuid", "")
        publish_id = baas_result.get("publish_id")
        if not bot_uuid:
            self._hooks.release_claim(bot_id=bot_id, owner_id=user_id)
            raise DesktopBotServiceError("BaaS create_bot returned no bot_uuid")
        if publish_id:
            try:
                self._baas.approve_publish(
                    publish_id=publish_id,
                    operator=user_id,
                    request_id=request_id,
                    comment="自动审批",
                )
            except Exception as error:
                self._hooks.release_claim(bot_id=bot_id, owner_id=user_id)
                raise DesktopBotServiceError(
                    "Desktop bot approve publish failed: "
                    f"publish_id={publish_id}, error={error}"
                ) from error

        return self._persist_and_start_polling(
            bot=bot,
            user_id=user_id,
            bot_id=bot_id,
            bot_uuid=bot_uuid,
            publish_id=publish_id,
            agent_code=agent_code,
            engine_type=engine_type,
            env=env,
            client_id=client_id,
            callback_token=callback_token,
            initial_ext=initial_ext,
            layout_credentials=layout_credentials,
        )

    @staticmethod
    def _load_creation_ext(existing_bot: dict[str, Any]) -> dict[str, Any]:
        raw_ext = existing_bot.get("ext") or {}
        if isinstance(raw_ext, str):
            try:
                raw_ext = json.loads(raw_ext)
            except json.JSONDecodeError as error:
                raise DesktopBotServiceError(
                    "persisted desktop creation context is invalid"
                ) from error
        if not isinstance(raw_ext, dict):
            raise DesktopBotServiceError(
                "persisted desktop creation context is invalid"
            )
        return dict(raw_ext)

    def _detach_released_binding_if_present(
        self,
        *,
        existing_bot: dict[str, Any],
        bot_id: str,
        user_id: str,
        env: str,
        client_id: str,
        callback_token: str,
    ) -> None:
        binding_id = existing_bot.get("binding_id")
        device_id = existing_bot.get("device_id")
        if not binding_id or not device_id:
            return
        binding = self._binding_repo.get_by_id(int(binding_id))
        if (
            binding is None
            or str(binding.status or "").upper()
            != DeviceBindingStatus.RELEASED.value
        ):
            return
        detached = self._binding_repo.detach_released_baas_desktop_binding_if_matches(
            binding_id=int(binding_id),
            bot_id=bot_id,
            owner_id=user_id,
            device_id=str(device_id),
            entity_id=user_id,
            env=env,
            expected_client_id=client_id,
            expected_callback_token=callback_token,
        )
        if not detached:
            raise DesktopBotServiceError(
                "released Desktop binding could not be detached from the retained "
                f"Bot: bot_id={bot_id}"
            )

    def _persist_and_start_polling(
        self,
        *,
        bot: dict[str, Any],
        user_id: str,
        bot_id: str,
        bot_uuid: str,
        publish_id: Any,
        agent_code: str,
        engine_type: str | None,
        env: str,
        client_id: str,
        callback_token: str,
        initial_ext: dict[str, Any],
        layout_credentials: dict[str, str],
    ) -> dict[str, Any]:
        binding_id: int | None = None
        try:
            binding_props = {
                "bot_uuid": bot_uuid,
                "client_id": client_id,
                "callback_token": callback_token,
                "publish_id": str(publish_id) if publish_id else None,
                "envs": self._hooks.layout_env(layout_credentials),
            }
            apply_reason = f"Create desktop bot: {bot.get('bot_name', '')}"
            recoverable = self._binding_repo.get_by_device_id(bot_uuid)
            if recoverable is not None:
                recovered = (
                    self._binding_repo.recover_baas_desktop_creation_binding_if_matches(
                        binding_id=recoverable.id,
                        bot_id=bot_id,
                        owner_id=user_id,
                        device_id=bot_uuid,
                        entity_id=user_id,
                        env=env,
                        expected_client_id=client_id,
                        expected_callback_token=callback_token,
                        device_props=binding_props,
                        apply_reason=apply_reason,
                        applied_by=user_id,
                    )
                )
                if not recovered:
                    raise DesktopBotServiceError(
                        "existing Desktop binding does not match the persisted "
                        f"creation context: bot_id={bot_id}"
                    )
                binding_id = recoverable.id
            else:
                binding_id = self._binding_repo.insert_binding(
                    entity_id=user_id,
                    entity_type="staff",
                    device_id=bot_uuid,
                    device_provider="baas",
                    env=env,
                    device_props=binding_props,
                    status="PENDING",
                    apply_reason=apply_reason,
                    applied_by=user_id,
                )

            updated_bot = self._bot_repo.update_by_owner(
                bot_id=bot_id,
                owner_id=user_id,
                update_data={
                    "binding_id": binding_id,
                    "device_id": bot_uuid,
                    "ext": {
                        **initial_ext,
                        "publish_id": str(publish_id) if publish_id else "",
                    },
                    "status": "PENDING",
                },
            )
            if updated_bot is None:
                raise DesktopBotServiceError(
                    f"Desktop bot binding update did not match: bot_id={bot_id}"
                )
            if publish_id:
                self._hooks.start_publish_polling(
                    publish_id=str(publish_id),
                    binding_id=str(binding_id),
                    bot_id=bot_id,
                    owner_id=user_id,
                    device_id=bot_uuid,
                    engine_type=engine_type or DEFAULT_ENGINE_TYPE,
                )
            return {
                "bot_uuid": bot_uuid,
                "binding_id": binding_id,
                "bot_id": bot_id,
                "agent_code": agent_code,
            }
        except Exception as error:
            if binding_id is not None:
                try:
                    self._binding_repo.release_binding(
                        binding_id=binding_id,
                        release_reason="Desktop bot creation did not persist",
                        released_by=user_id,
                    )
                except Exception:
                    logger.exception(
                        "[DesktopCreationLifecycle] failed to release orphan "
                        "binding_id=%s",
                        binding_id,
                    )
                try:
                    self._bot_repo.update_by_owner(
                        bot_id=bot_id,
                        owner_id=user_id,
                        update_data={
                            "binding_id": None,
                            "device_id": None,
                            "status": "PENDING",
                        },
                    )
                except Exception:
                    logger.exception(
                        "[DesktopCreationLifecycle] failed to clear orphan binding "
                        "from bot_id=%s",
                        bot_id,
                    )
            self._hooks.release_claim(bot_id=bot_id, owner_id=user_id)
            raise DesktopBotServiceError(
                f"Desktop bot local write failed: {error}"
            ) from error
