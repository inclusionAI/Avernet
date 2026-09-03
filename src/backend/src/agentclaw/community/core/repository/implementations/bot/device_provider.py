"""Which device provider a bot runs on — the ``ac_entity_device_binding`` join.

Split out of ``bot.py`` as one concern, the way ``reachability.py`` is. Both
queries answer the same question — *what provisioned this bot* — by joining
``ac_bots`` to ``ac_entity_device_binding`` on ``device_id``, the prod join
the old SQLite twin stubbed as ``None``. The answer is the provider name plus,
for ARCA, the sandbox id from the binding's ``device_props``; ``bot_type``
rides along because the callers that ask about the provider ask about the
kind of bot in the same breath.

A mixin rather than a module of functions because both queries need the
repository's session, model and env scoping. The binding model is imported
inside the methods, as before, to keep the devices package off this module's
import path.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional


class BotDeviceProviderQueries:
    """Device-provider join queries for :class:`BotRepository`."""

    def _device_provider_result(
        self, device_provider, device_props_json, bot_type=None
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "device_provider": device_provider,
            "bot_type": bot_type or "",
        }
        if device_provider == "arca":
            try:
                props = (
                    json.loads(device_props_json)
                    if isinstance(device_props_json, str)
                    else device_props_json
                )
                sandbox_id = (props or {}).get("sandbox_id")
                if sandbox_id:
                    result["sandbox_id"] = sandbox_id
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass
        return result

    def get_device_provider_by_bot_id_and_owner(
        self, bot_id: str, owner_id: str
    ) -> Optional[Dict[str, Any]]:
        from agentclaw.community.core.devices.repository.models import (
            EntityDeviceBinding,
        )

        with self._db.orm_session() as db:
            row = (
                db.query(
                    EntityDeviceBinding.device_provider,
                    EntityDeviceBinding.device_props,
                    self.Model.bot_type,
                )
                .select_from(self.Model)
                .outerjoin(
                    EntityDeviceBinding,
                    self.Model.device_id == EntityDeviceBinding.device_id,
                )
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.owner_id == owner_id,
                    self.Model.is_delete == 0,
                    self._env(),
                )
                .first()
            )
            if row is None:
                return None
            return self._device_provider_result(row[0], row[1], row[2])

    def get_device_provider_by_bot_id(self, bot_id: str) -> Optional[Dict[str, Any]]:
        from agentclaw.community.core.devices.repository.models import (
            EntityDeviceBinding,
        )

        with self._db.orm_session() as db:
            row = (
                db.query(
                    EntityDeviceBinding.device_provider,
                    EntityDeviceBinding.device_props,
                    self.Model.bot_type,
                )
                .select_from(self.Model)
                .outerjoin(
                    EntityDeviceBinding,
                    self.Model.device_id == EntityDeviceBinding.device_id,
                )
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.is_delete == 0,
                    self._env(),
                )
                .first()
            )
            if row is None:
                return None
            return self._device_provider_result(row[0], row[1], row[2])
