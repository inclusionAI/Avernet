"""ManualConsentPassportPlugin — AgentPass stand-in with a human in the loop.

openspec ``add-bot-create-human-confirm`` D8: the community composition built
for the simulator stack can mirror what a deployment actually experiences in
prod — ``apply_agent_passport`` creates a PENDING application and returns the
consent URLs once, and no headless caller anywhere gets to flip it. Only a
human clicking approve/reject on the consent page does. A pending application
that nobody attends dies by the durable creation job's authorization window,
never by this plugin: tcauthmng parity means the plugin invents no expiry of
its own.

The registry is in-process only (a lock-guarded pair of dicts), which is the
simulator's own durability statement: a backend restart mid-park degrades
exactly like prod's never-approved window — the job's expiry is the backstop.

Registry entries key on ``(tenant, bot_id, owner_workno)`` — the ambient
``avernet_tenant`` at apply time joins the key because a ``bot_id`` is unique
only within a tenant (the same residual-collision rule that keys the manifest
tables), and the creation job re-establishes that tenant from its payload
before every poll, so the key resolution matches on both sides of the fence.

``apply_first_agent_passport`` preserves the trusted-bootstrap parity: the
owner's first passport issues inline even in manual mode, the way tcauthmng
does, so legacy-surface first bots and the auto-issue fast path stay gate-free
side by side with interactive ones.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.passport import (
    CliItem,
    PassportError,
    PassportManualConsentControl,
    validate_passport_target_env,
)
from agentclaw.community.plugins.community.passport import (
    SelfIssuedPassportPlugin,
    _token_for,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


logger = get_logger()

APPLICATION_STATUS_PENDING = "PENDING"
APPLICATION_STATUS_ISSUED = "ISSUED"
APPLICATION_STATUS_REJECTED = "REJECTED"


class PassportConsentAlreadyDecidedError(PassportError):
    """A decided application cannot be decided again — the verdict is a transition."""

    def __init__(self, application_id: str, status: str):
        super().__init__(
            f"passport application {application_id} was already decided as {status}"
        )
        self.application_id = application_id
        self.status = status


@dataclass
class _Application:
    """One in-process passport application; guarded by the plugin's lock."""

    application_id: str
    tenant: str
    bot_id: str
    owner_workno: str
    status: str
    created_at: str
    bot_name: str | None = None
    bot_desc: str | None = None
    engine_type: str | None = None
    decided_at: str | None = None
    reason: str | None = None

    def facts(self, *, page_facts: bool = False) -> dict[str, Any]:
        facts: dict[str, Any] = {
            "application_id": self.application_id,
            "bot_id": self.bot_id,
            "owner_workno": self.owner_workno,
            "status": self.status,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
        }
        if page_facts:
            facts.update(
                {
                    "tenant": self.tenant,
                    "bot_name": self.bot_name,
                    "bot_desc": self.bot_desc,
                    "engine_type": self.engine_type,
                    "reason": self.reason,
                }
            )
        return facts


class ManualConsentPassportPlugin(SelfIssuedPassportPlugin, PassportManualConsentControl):
    """Community passport that requires a human decision for every apply.

    Only ``CommunityIdentityModule`` names this class, and only when
    ``user_config.community_identity.passport_mode`` is ``manual_consent``;
    every other composition holds :class:`NullPassportManualConsentControl`.
    """

    available = True

    def __init__(self, consent_base_url: str):
        self._consent_base_url = consent_base_url
        self._lock = threading.Lock()
        self._applications: dict[str, _Application] = {}
        self._application_ids_by_key: dict[tuple[str, str, str], str] = {}

    # ── PassportPlugin: the apply side ───────────────────────────────────────

    def apply_agent_passport(
        self,
        bot_id: str,
        owner_workno: str,
        mcp_codes: list[str],
        bot_name: str | None = None,
        bot_desc: str | None = None,
        bot_llm: str | None = None,
        engine_type: str | None = None,
        access_mode: str | None = None,
        device_token: str | None = None,
        workspace_path: str | None = None,
        admins: list[str] | None = None,
        cli_items: list[CliItem] | None = None,
    ) -> dict[str, Any] | None:
        entry = self._register(
            bot_id=bot_id,
            owner_workno=owner_workno,
            bot_name=bot_name,
            bot_desc=bot_desc,
            engine_type=engine_type,
        )
        if entry.status == APPLICATION_STATUS_PENDING:
            consent_url = (
                f"{self._consent_base_url}/simulator/agentpass/consent"
                f"?app={entry.application_id}"
            )
            redirect_url = (
                f"{self._consent_base_url}/simulator/agentpass/applications"
                f"/{entry.application_id}"
            )
            return {
                "token": None,
                "agent_code": None,
                "iframe_url": consent_url,
                "redirect_url": redirect_url,
            }
        if entry.status == APPLICATION_STATUS_ISSUED:
            # Read parity: exactly the answer SelfIssuedPassportPlugin would
            # have given for this bot, so a completed authorization and an
            # always-issued composition are indistinguishable downstream.
            return {
                "token": _token_for(bot_id),
                "agent_code": bot_id,
                "iframe_url": None,
                "redirect_url": None,
            }
        # REJECTED stays sticky per key: a caller re-applying for the same bot
        # gets neither a token nor a fresh application to authorize.
        return {
            "token": None,
            "agent_code": None,
            "iframe_url": None,
            "redirect_url": None,
        }

    def apply_first_agent_passport(
        self,
        bot_id: str,
        owner_workno: str,
        mcp_codes: list[str],
        bot_name: str | None = None,
        bot_desc: str | None = None,
        bot_llm: str | None = None,
        engine_type: str | None = None,
        access_mode: str | None = None,
        device_token: str | None = None,
        workspace_path: str | None = None,
        admins: list[str] | None = None,
        cli_items: list[CliItem] | None = None,
        *,
        target_env: str | None = None,
    ) -> dict[str, Any] | None:
        validate_passport_target_env(target_env)
        self._record_first_bootstrap(bot_id=bot_id, owner_workno=owner_workno)
        return super().apply_first_agent_passport(
            bot_id=bot_id,
            owner_workno=owner_workno,
            mcp_codes=mcp_codes,
            bot_name=bot_name,
            bot_desc=bot_desc,
            bot_llm=bot_llm,
            engine_type=engine_type,
            access_mode=access_mode,
            device_token=device_token,
            workspace_path=workspace_path,
            admins=admins,
            cli_items=cli_items,
            target_env=target_env,
        )

    # ── PassportPlugin: the read side ────────────────────────────────────────

    def query_auth_status(
        self,
        bot_id: str,
        owner_workno: str,
        *,
        target_env: str | None = None,
        entity_id: str | None = None,
    ) -> dict[str, Any] | None:
        validate_passport_target_env(target_env)
        entry = self._find(bot_id=bot_id, owner_workno=owner_workno)
        if entry is None:
            return None
        if entry.status == APPLICATION_STATUS_ISSUED:
            return {"status": APPLICATION_STATUS_ISSUED, "token": _token_for(bot_id)}
        return {"status": entry.status}

    def query_token(
        self,
        bot_id: str,
        owner_workno: str,
        *,
        target_env: str | None = None,
        entity_id: str | None = None,
    ) -> str | None:
        validate_passport_target_env(target_env)
        entry = self._find(bot_id=bot_id, owner_workno=owner_workno)
        if entry is not None and entry.status == APPLICATION_STATUS_ISSUED:
            return _token_for(bot_id)
        return None

    def query_agent_passport(
        self,
        bot_id: str,
        owner_workno: str,
        *,
        target_env: str | None = None,
        entity_id: str | None = None,
    ) -> dict[str, Any] | None:
        validate_passport_target_env(target_env)
        entry = self._find(bot_id=bot_id, owner_workno=owner_workno)
        if entry is None or entry.status != APPLICATION_STATUS_ISSUED:
            return None
        return {
            "agent_id": bot_id,
            "agent_code": bot_id,
            "status": APPLICATION_STATUS_ISSUED,
            "access_mode": None,
            "mcps": [],
            "clis": [],
        }

    # ── PassportManualConsentControl: the human's side ──────────────────────

    def decide(
        self,
        application_id: str,
        *,
        approve: bool,
        reason: str | None = None,
    ) -> str | None:
        with self._lock:
            entry = self._applications.get(application_id)
            if entry is None:
                return None
            if entry.status != APPLICATION_STATUS_PENDING:
                raise PassportConsentAlreadyDecidedError(
                    application_id, entry.status
                )
            entry.status = (
                APPLICATION_STATUS_ISSUED if approve else APPLICATION_STATUS_REJECTED
            )
            entry.decided_at = datetime.now().isoformat()
            entry.reason = reason
            logger.info(
                "[ManualConsent] decision application=%s bot_id=%s → %s",
                application_id,
                entry.bot_id,
                entry.status,
            )
            return entry.status

    def get_application(self, application_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._applications.get(application_id)
            if entry is None:
                return None
            return entry.facts(page_facts=True)

    def list_applications(self, bot_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            entries = list(self._applications.values())
        if bot_id is not None:
            entries = [entry for entry in entries if entry.bot_id == bot_id]
        entries.sort(key=lambda entry: entry.created_at, reverse=True)
        return [entry.facts() for entry in entries]

    # ── internal ─────────────────────────────────────────────────────────────

    def _register(
        self,
        *,
        bot_id: str,
        owner_workno: str,
        bot_name: str | None,
        bot_desc: str | None,
        engine_type: str | None,
    ) -> _Application:
        """Idempotently register the key's application and return its entry.

        Idempotency is per ``(tenant, bot_id, owner_workno)``: a live (PENDING)
        application is re-served rather than joined by a second one, so a
        retried submission cannot mint a replacement identity nor reset the
        human's window (the same rule that keeps swarm attempts from minting a
        new bot per retry).
        """
        tenant = get_current_avernet_tenant()
        key = (tenant, bot_id, owner_workno)
        with self._lock:
            existing_id = self._application_ids_by_key.get(key)
            if existing_id is not None:
                return self._applications[existing_id]
            application_id = f"sim-passport-{uuid4().hex[:16]}"
            entry = _Application(
                application_id=application_id,
                tenant=tenant,
                bot_id=bot_id,
                owner_workno=owner_workno,
                status=APPLICATION_STATUS_PENDING,
                created_at=datetime.now().isoformat(),
                bot_name=bot_name,
                bot_desc=bot_desc,
                engine_type=engine_type,
            )
            self._applications[application_id] = entry
            self._application_ids_by_key[key] = application_id
            logger.info(
                "[ManualConsent] application=%s PENDING for bot_id=%s owner=%s",
                application_id,
                bot_id,
                owner_workno,
            )
            return entry

    def _record_first_bootstrap(self, *, bot_id: str, owner_workno: str) -> None:
        """Mark the key ISSUED so the read side agrees with the inline issue.

        Without this, the auto-issue fast path (an inline-issued creation on the
        manifest surface) would leave the application registry silent for the
        key, and the creation job's own status poll would wait on an answer
        that never comes. The owner's authenticated bootstrap call is the
        decision, the way tcauthmng's ``applyFirstAgentPassport`` is.
        """
        tenant = get_current_avernet_tenant()
        key = (tenant, bot_id, owner_workno)
        with self._lock:
            existing_id = self._application_ids_by_key.get(key)
            if existing_id is not None:
                entry = self._applications[existing_id]
                if entry.status == APPLICATION_STATUS_ISSUED:
                    return
                entry.status = APPLICATION_STATUS_ISSUED
                entry.decided_at = datetime.now().isoformat()
                return
            application_id = f"sim-passport-{uuid4().hex[:16]}"
            self._applications[application_id] = _Application(
                application_id=application_id,
                tenant=tenant,
                bot_id=bot_id,
                owner_workno=owner_workno,
                status=APPLICATION_STATUS_ISSUED,
                created_at=datetime.now().isoformat(),
                decided_at=datetime.now().isoformat(),
            )
            self._application_ids_by_key[key] = application_id

    def _find(self, *, bot_id: str, owner_workno: str) -> _Application | None:
        key = (get_current_avernet_tenant(), bot_id, owner_workno)
        with self._lock:
            application_id = self._application_ids_by_key.get(key)
            if application_id is None:
                return None
            return self._applications[application_id]


class NullPassportManualConsentControl(PassportManualConsentControl):
    """The mode-off control bound wherever manual consent is off (D8).

    The community identity module binds this in ``self_issued`` compositions
    (and the test identity module in the local columns) so the simulator
    consent surfaces exist but answer 404 — "the app does not branch on mode;
    DI decides". Every method answers the unknown/empty answer, which the
    HTTP adapters cannot rely on because ``available`` is what they check.
    """

    available = False

    def decide(
        self,
        application_id: str,
        *,
        approve: bool,
        reason: str | None = None,
    ) -> str | None:
        return None

    def get_application(self, application_id: str) -> dict[str, Any] | None:
        return None

    def list_applications(self, bot_id: str | None = None) -> list[dict[str, Any]]:
        return []


__all__ = [
    "APPLICATION_STATUS_ISSUED",
    "APPLICATION_STATUS_PENDING",
    "APPLICATION_STATUS_REJECTED",
    "ManualConsentPassportPlugin",
    "NullPassportManualConsentControl",
    "PassportConsentAlreadyDecidedError",
]