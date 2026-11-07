"""Controlled credential boundary; NOT evidence of real Passport retirement."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from agentclaw.community.plugin_api.passport import PassportError

PREFIX = "ownersyncacceptance"


def fixture_identity(bot_id: str, user_id: str, entity_id: str) -> bool:
    return all(value.startswith(PREFIX) for value in (bot_id, user_id, entity_id))


@dataclass
class Generation:
    metadata: dict[str, Any] = field(default_factory=lambda: {"credential_id": "fixture-generation-0"})
    # Operation replay retains its original expected generation and target.
    intent: tuple[str, str, str, str] | None = None
    retired: bool = False


class FixturePassport:
    """Only the isolated namespace gets correlated, selected-generation state."""

    def __init__(self, delegate):
        self.delegate = delegate
        self._lock = threading.RLock()
        self._states: dict[tuple[str, str], Generation] = {}

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def _state(self, bot_id, entity_id):
        return self._states.setdefault((bot_id, entity_id), Generation())

    def owner_handoff_supported(self, *, bot_id, owner_workno, entity_id):
        if fixture_identity(bot_id, owner_workno, entity_id):
            return True
        return self.delegate.owner_handoff_supported(
            bot_id=bot_id, owner_workno=owner_workno, entity_id=entity_id,
        )

    def query_agent_passport(self, bot_id, owner_workno, *, entity_id=None):
        if not fixture_identity(bot_id, owner_workno, entity_id or ""):
            return self.delegate.query_agent_passport(bot_id, owner_workno, entity_id=entity_id)
        with self._lock:
            return dict(self._state(bot_id, entity_id).metadata)

    def reissue_agent_credentials(
        self, *, bot_id, owner_workno, entity_id, execution_workno,
        handoff_operation_id=None, expected_credential_id=None, **kwargs,
    ):
        if not fixture_identity(bot_id, owner_workno, entity_id):
            return self.delegate.reissue_agent_credentials(
                bot_id=bot_id, owner_workno=owner_workno, entity_id=entity_id,
                execution_workno=execution_workno, handoff_operation_id=handoff_operation_id,
                expected_credential_id=expected_credential_id, **kwargs,
            )
        if not handoff_operation_id or not expected_credential_id or not execution_workno.startswith(PREFIX):
            raise PassportError("FIXTURE_HANDOFF_INTENT_REQUIRED")
        intent = (handoff_operation_id, expected_credential_id, execution_workno, owner_workno)
        with self._lock:
            state = self._state(bot_id, entity_id)
            if state.intent is not None and state.intent[0] == handoff_operation_id:
                if state.intent != intent:
                    raise PassportError("FIXTURE_GENERATION_CONFLICT")
                return dict(state.metadata)
            if state.metadata["credential_id"] != expected_credential_id:
                raise PassportError("FIXTURE_GENERATION_CONFLICT")
            state.intent = intent
            state.retired = False
            state.metadata = {
                "credential_id": f"fixture-generation-{handoff_operation_id}",
                "handoff_operation_id": handoff_operation_id,
                "execution_workno": execution_workno,
                "authorization_id": f"fixture-authorization-{handoff_operation_id}",
                "agent_id": f"fixture-agent-{bot_id}",
            }
            return dict(state.metadata)

    def query_auth_status(self, bot_id, owner_workno, *, entity_id=None):
        if not fixture_identity(bot_id, owner_workno, entity_id or ""):
            return self.delegate.query_auth_status(bot_id, owner_workno, entity_id=entity_id)
        with self._lock:
            state = self._state(bot_id, entity_id)
            if state.intent is None:
                return {"status": "PENDING"}
            # Explicit dummy, not a real credential; never sent to a real runtime
            # or included in logs.
            return {"status": "ISSUED", "token": "test-token"}

    def retire_agent_credentials(
        self, *, bot_id, owner_workno, entity_id, keep_credential_id, handoff_operation_id,
    ):
        if not fixture_identity(bot_id, owner_workno, entity_id):
            return self.delegate.retire_agent_credentials(
                bot_id=bot_id, owner_workno=owner_workno, entity_id=entity_id,
                keep_credential_id=keep_credential_id, handoff_operation_id=handoff_operation_id,
            )
        with self._lock:
            state = self._state(bot_id, entity_id)
            if (state.intent is None or state.intent[0] != handoff_operation_id
                    or state.intent[3] != owner_workno
                    or state.metadata["credential_id"] != keep_credential_id):
                raise PassportError("FIXTURE_GENERATION_CONFLICT")
            state.retired = True
