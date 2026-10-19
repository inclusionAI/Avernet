"""MessageRepository — ac_agent_swarm_message persistence (D19 no-await-in-session)."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository.message import model as om
from agentclaw.community.core.agentswarm.repository.message.protocol import (
    MessageRepositoryProtocol,
)
from agentclaw.community.core.agentswarm.repository.types import _dumps, _loads
from agentclaw.community.plugin_api.database import DatabasePlugin


def _decode_message_role(val: str) -> dm.MessageRole:
    try:
        return dm.MessageRole(val)
    except ValueError:
        return dm.MessageRole.SYSTEM


def _encode_decision(decision: dm.Decision) -> dict[str, Any]:
    """JSON-safe dict for one orchestrator decision, keyed on kind.

    Lossless inverse of `_decode_decision`. Raw `decision.__dict__`
    through `_dumps(default=str)` serialized every TaskEnvelope in a
    SpawnDecision to its repr string — unreadable on the way back out —
    so spawn entries are expanded to plain dicts via `asdict` here and
    rebuilt as TaskEnvelopes on decode. Wait/handoff/finish key/value
    shapes are unchanged: StrEnum members already json-encode as their
    values, and tuples as lists.
    """
    if isinstance(decision, dm.SpawnDecision):
        return {
            "kind": dm.DecisionKind.SPAWN_AGENT.value,
            "agents": [asdict(env) for env in decision.agents],
        }
    if isinstance(decision, dm.WaitDecision):
        return {
            "kind": dm.DecisionKind.WAIT.value,
            "refs": list(decision.refs),
            "mode": decision.mode.value,
        }
    if isinstance(decision, dm.HandoffDecision):
        return {
            "kind": dm.DecisionKind.HANDOFF.value,
            "target": decision.target,
            "reason": decision.reason,
        }
    if isinstance(decision, dm.FinishDecision):
        return {
            "kind": dm.DecisionKind.FINISH.value,
            "answer": decision.answer,
        }
    return dict(decision.__dict__)


def _decode_decision(raw: dict | None) -> dm.Decision | None:
    """Inverse of `_encode_decision`, keyed on kind.

    A spawn row whose agents entries are not dicts (legacy rows written
    by the repr-string encoding) cannot be restored to TaskEnvelopes;
    that is corrupt data, so it fails loud here instead of building a
    SpawnDecision whose typed `tuple[TaskEnvelope, ...]` silently holds
    strings. Unknown kinds decode to None.
    """
    if not raw:
        return None
    kind = raw.get("kind")
    if kind == dm.DecisionKind.WAIT.value:
        return dm.WaitDecision(
            refs=tuple(str(ref) for ref in raw.get("refs", ())),
            mode=dm.WaitMode(raw.get("mode", dm.WaitMode.ALL.value)),
        )
    if kind == dm.DecisionKind.HANDOFF.value:
        return dm.HandoffDecision(
            target=raw.get("target", ""), reason=raw.get("reason", ""),
        )
    if kind == dm.DecisionKind.FINISH.value:
        return dm.FinishDecision(answer=raw.get("answer", ""))
    if kind == dm.DecisionKind.SPAWN_AGENT.value:
        agents = raw.get("agents", ())
        if any(not isinstance(agent, dict) for agent in agents):
            raise ValueError(
                "spawn decision carries non-dict agent entries; legacy "
                "repr-encoded rows cannot be restored to TaskEnvelopes"
            )
        return dm.SpawnDecision(
            agents=tuple(dm.TaskEnvelope(**agent) for agent in agents)
        )
    return None


def _decode_token_usage(raw: dict | None) -> dm.TokenUsage | None:
    if not raw:
        return None
    return dm.TokenUsage(
        prompt_tokens=int(raw.get("prompt_tokens", 0)),
        completion_tokens=int(raw.get("completion_tokens", 0)),
        total_tokens=int(raw.get("total_tokens", 0)),
    )


class MessageRepository(MessageRepositoryProtocol):

    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def create(self, message: dm.Message) -> dm.Message:
        try:
            with self._db.orm_session() as session:
                row = om.AcAgentSwarmMessageModel(
                    message_id=message.id,
                    run_id=message.run_id,
                    tenant=message.tenant,
                    agent_id=message.agent_id,
                    seq=message.seq,
                    role=message.role.value,
                    content=_dumps(message.content),
                    decision=_dumps(_encode_decision(message.decision)) if message.decision else None,
                    token_usage=_dumps(message.token_usage.__dict__) if message.token_usage else None,
                    artifact_key=message.artifact_key,
                )
                session.add(row)
        except IntegrityError:
            # uk_message: the (env, tenant, agent_id, seq) tuple already
            # landed — a stale worker replaying the seq lost the reclaim
            # race. Exactly-once (D24) means converge to the surviving row,
            # not surface the collision; a non-tuple IntegrityError re-raises.
            existing = self._existing_by_agent_seq(message)
            if existing is not None:
                return existing
            raise
        return message

    def _existing_by_agent_seq(self, message: dm.Message) -> dm.Message | None:
        with self._db.orm_session() as session:
            row = session.execute(
                select(om.AcAgentSwarmMessageModel).where(
                    om.AcAgentSwarmMessageModel.agent_id == message.agent_id,
                    om.AcAgentSwarmMessageModel.seq == message.seq,
                    om.AcAgentSwarmMessageModel.tenant == message.tenant,
                    om.AcAgentSwarmMessageModel.is_deleted.is_(False),
                )
            ).scalar_one_or_none()
            return self._to_domain(row) if row else None

    def list_by_agent(self, run_id: dm.RunId, agent_id: dm.AgentId, tenant: dm.TenantId) -> list[dm.Message]:
        with self._db.orm_session() as session:
            rows = session.execute(
                select(om.AcAgentSwarmMessageModel).where(
                    om.AcAgentSwarmMessageModel.run_id == run_id,
                    om.AcAgentSwarmMessageModel.agent_id == agent_id,
                    om.AcAgentSwarmMessageModel.tenant == tenant,
                    om.AcAgentSwarmMessageModel.is_deleted.is_(False),
                ).order_by(om.AcAgentSwarmMessageModel.seq)
            ).scalars().all()
            return [self._to_domain(r) for r in rows]

    def apply_retention(self, run_id: dm.RunId, agent_id: dm.AgentId,
                        tenant: dm.TenantId, tail: int) -> int:
        with self._db.orm_session() as session:
            total = session.execute(
                select(om.AcAgentSwarmMessageModel.seq).where(
                    om.AcAgentSwarmMessageModel.run_id == run_id,
                    om.AcAgentSwarmMessageModel.agent_id == agent_id,
                    om.AcAgentSwarmMessageModel.tenant == tenant,
                    om.AcAgentSwarmMessageModel.is_deleted.is_(False),
                ).order_by(om.AcAgentSwarmMessageModel.seq.desc())
            ).scalars().all()
            if len(total) <= tail:
                return 0
            cutoff = total[tail]
            result = session.execute(
                update(om.AcAgentSwarmMessageModel).where(
                    om.AcAgentSwarmMessageModel.run_id == run_id,
                    om.AcAgentSwarmMessageModel.agent_id == agent_id,
                    om.AcAgentSwarmMessageModel.tenant == tenant,
                    om.AcAgentSwarmMessageModel.seq <= cutoff,
                    om.AcAgentSwarmMessageModel.is_deleted.is_(False),
                ).values(is_deleted=True)
            )
        return result.rowcount

    def purge_run(self, run_id: dm.RunId, tenant: dm.TenantId) -> int:
        with self._db.orm_session() as session:
            result = session.execute(
                delete(om.AcAgentSwarmMessageModel).where(
                    om.AcAgentSwarmMessageModel.run_id == run_id,
                    om.AcAgentSwarmMessageModel.tenant == tenant,
                )
            )
        return result.rowcount

    @staticmethod
    def _to_domain(row: om.AcAgentSwarmMessageModel) -> dm.Message:
        # gmt_create is a DB-clock naive TIMESTAMP: converting it to epoch
        # millis is timezone-ambiguous, so created_at keeps its EpochMillis(0)
        # default rather than guessing a clock the writer never wrote.
        return dm.Message(
            id=dm.MessageId(row.message_id),
            run_id=dm.RunId(row.run_id),
            tenant=row.tenant,
            agent_id=dm.AgentId(row.agent_id),
            seq=row.seq,
            role=_decode_message_role(row.role),
            content=_loads(row.content),
            decision=_decode_decision(_loads(row.decision)),
            token_usage=_decode_token_usage(_loads(row.token_usage)),
            artifact_key=dm.ArtifactKey(row.artifact_key) if row.artifact_key else None,
            created_at=dm.EpochMillis(0),
        )
