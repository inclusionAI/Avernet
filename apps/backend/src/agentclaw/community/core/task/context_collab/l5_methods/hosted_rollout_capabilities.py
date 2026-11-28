"""Hosted product runtime capabilities; public defaults remain fail-closed.

These adapters are composition-root seams over installed infrastructure:

* Bot identity and authority come from the scoped Bot repository.
* Progress verification only accepts the existing signed Bot ingress identity.
* The stage-improvement decision is an actual Bot call that must return a typed
  JSON proposal; no worker prose is silently promoted into a privileged action.
* Notifications use the deployment's configured notification sender.

The adapters do not fabricate runtime facts when the required ports are absent.
"""

from __future__ import annotations

import json
from hashlib import sha256

from agentclaw.community.core.errors import Forbidden
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.product_runtime import (
    OwnerProductRuntimeAuthorization,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    AdjustTypeEnum as A,
    StatusEnum as S,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_contracts import (
    AdjustmentProposal,
    StagePatch,
    StrategyDescriptor,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_policy import (
    AdaptiveUntilAccepted,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    ExecutionIdentity,
    RolloutIdentityPort,
    SessionRoute,
)
from agentclaw.community.core.task.domain.json_extract import extract_json
from agentclaw.community.core.task.task_runner.client.ports import OpenApiBotPort

_FAILURE_STATUSES = {S.RUN_FAILED, S.ACCEPTANCE_FAILED}


class BotRepositoryRolloutIdentity(RolloutIdentityPort):
    """Resolve a unique live Bot and its current owner in the current scope."""

    def __init__(self, bots: BotRepository) -> None:
        self._bots = bots

    def resolve(self, candidate: AgentRef, context: ActorContext) -> ExecutionIdentity:
        del context
        if candidate.agent_type != "bot" or not candidate.agent_id.strip():
            raise Invalid("OpenAPI execution requires a configured Bot candidate")
        raw_bot_id, _, explicit_owner = candidate.agent_id.partition(":")
        if not raw_bot_id.strip():
            raise Invalid("configured Bot candidate has no Bot identity")
        owner = explicit_owner if explicit_owner.strip() else ""
        if not owner:
            state, repository_owner = self._bots.classify_forum_recipient(raw_bot_id)
            if state != "live":
                raise Invalid(f"configured Bot candidate is {state}")
            owner = repository_owner or ""
        if not owner.strip():
            raise Invalid("configured Bot candidate has no current owner")
        return ExecutionIdentity(raw_bot_id, owner, "online")


class OwnedBotProductRuntimeAuthorization(OwnerProductRuntimeAuthorization):
    """Owner authority plus a persisted, owner-scoped candidate participation grant."""

    def __init__(self, identity: BotRepositoryRolloutIdentity) -> None:
        self._identity = identity

    def check_candidate(self, candidate, context, snapshot) -> None:
        identity = self._identity.resolve(candidate, context)
        if identity.owner_id != snapshot.owner.principal_id:
            raise Forbidden("Bot candidate is not owned by the recorded product owner")


class SignedOpenApiEvidence:
    """Verify only progress from the existing signed Bot ingress identity."""

    def require(self, acceptance_ref: str) -> None:
        if acceptance_ref != "trusted-evidence/v1":
            raise Invalid("unsupported trusted evidence capability")

    async def verify(
        self,
        acceptance_ref,
        route: SessionRoute,
        conditions,
        artifacts,
        acceptances,
        context: ActorContext,
    ) -> None:
        self.require(acceptance_ref)
        if route.provider != "openapi_bot":
            raise Invalid("unsupported evidence route provider")
        if (
            context.source != "product_progress"
            or context.actor.principal_type != "bot"
            or context.actor.principal_id != route.bot_id
            or context.accountable_principal_ref is None
            or context.accountable_principal_ref.principal_id != route.owner_id
        ):
            raise Forbidden("acceptance evidence does not come from the allocated Bot")
        if not acceptances:
            raise Invalid("Bot progress requires acceptance results")
        actual: dict[str, bool] = {}
        for acceptance in acceptances:
            if acceptance.condition in actual:
                raise Invalid("duplicate acceptance result")
            actual[acceptance.condition] = acceptance.passed
        if set(actual) != set(conditions):
            raise Invalid("acceptance results do not exactly cover frozen conditions")
        if all(actual[name] for name in conditions) and not artifacts:
            raise Invalid("successful acceptance requires real artifact evidence")


class AdaptiveStageImprovementStrategy:
    """Call a failed stage Bot as the human-gated stage improvement agent."""

    _REFERENCE = "stage-improvement-agent@1"
    _RUBRIC = "human-feedback-stage-improvement@1"

    def __init__(
        self, bot: OpenApiBotPort, identities: BotRepositoryRolloutIdentity
    ) -> None:
        self._bot = bot
        self._identities = identities

    def require(self, policy, stages, context) -> StrategyDescriptor:
        del context
        if (
            type(policy.strategy) is not AdaptiveUntilAccepted
            or policy.strategy.adaptation_ref != self._REFERENCE
            or policy.strategy.feedback_mode != "interactive"
        ):
            raise Invalid("interactive stage improvement is not installed")
        if any(
            candidate.agent_type != "bot"
            for stage in stages
            for candidate in stage.candidates
        ):
            raise Invalid("stage improvement only installs Bot execution candidates")
        return StrategyDescriptor(
            reference=self._REFERENCE,
            rubric=self._RUBRIC,
            evaluator_ref="trusted-evidence/v1",
            minimum=0,
            maximum=1,
        )

    async def evaluate(self, *args):
        del args
        raise Invalid("adaptive strategy does not select parallel candidates")

    async def propose_adjustment(
        self, reference, snapshot, feedback, context
    ) -> AdjustmentProposal:
        if reference != self._REFERENCE:
            raise Invalid("stage improvement capability reference changed")
        failed = next(
            (
                (stage, next(s for s in snapshot.specs if s.name == stage.name))
                for stage in snapshot.current().stages
                if stage.status in _FAILURE_STATUSES
            ),
            None,
        )
        if failed is None:
            raise Invalid("stage improvement requires a failed stage")
        stage, spec = failed
        candidate = next(
            (
                candidate
                for candidate in spec.candidates
                if candidate.agent_type == "bot"
            ),
            None,
        )
        if candidate is None:
            raise Invalid("failed stage has no Bot improvement executor")
        identity = self._identities.resolve(candidate, context)
        prompt = {
            "schema": "product-stage-improvement@1",
            "authorized_by": "human_feedback",
            "allowed_actions": [
                "DO_NOTHING",
                "OPTIMIZE",
                "RE_DO",
                "SHUTDOWN",
            ],
            "response_contract": {
                "action": "RE_DO|OPTIMIZE|SHUTDOWN|DO_NOTHING",
                "message": "bounded explanation",
                "rerun": ["only for RE_DO; existing stage names"],
                "patches": [],
                "request_feedback": False,
            },
            "feedback": feedback,
            "failed_stage": {
                "name": stage.name,
                "status": stage.status.value,
                "error": stage.error_msg,
                "acceptances": [
                    {
                        "condition": result.condition,
                        "passed": result.passed,
                        "reason": result.reason,
                    }
                    for result in stage.acceptances
                ],
                "artifact_names": [artifact.name for artifact in stage.artifacts],
            },
            "all_stage_names": [stage.name for stage in snapshot.specs],
        }
        run = await self._bot.send_and_wait_async(
            bot_id=f"{identity.bot_id}:{identity.owner_id}",
            message=json.dumps(prompt, ensure_ascii=False, sort_keys=True),
            metadata={
                "product_collet_id": snapshot.collet.id,
                "product_rollout_id": snapshot.current_rollout_id,
                "product_stage_rollout_id": stage.stage_rollout_id,
                "product_capability": self._REFERENCE,
                "lifecycle_stage": identity.lifecycle_stage,
            },
            timeout=180,
        )
        return self._parse(run)

    @classmethod
    def _parse(cls, run: dict) -> AdjustmentProposal:
        result = run.get("result")
        content = result.get("content") if isinstance(result, dict) else result
        try:
            parsed = extract_json(content)
        except (TypeError, ValueError) as exc:
            raise Invalid("stage improvement Bot did not return JSON") from exc
        if type(parsed) is not dict or type(parsed.get("action")) is not str:
            raise Invalid("stage improvement Bot returned an invalid proposal")
        action = parsed["action"].strip().upper()
        message = parsed.get("message")
        if type(message) is not str or not message.strip():
            raise Invalid("stage improvement proposal requires a message")
        request_feedback = parsed.get("request_feedback") is True
        patches = cls._patches(parsed.get("patches"))
        rerun = cls._rerun(parsed.get("rerun"))
        mapping = {
            "DO_NOTHING": A.DO_NOTHING,
            "OPTIMIZE": A.OPTIMIZE,
            "RE_DO": A.RE_DO,
            "SHUTDOWN": A.SHUTDOWN,
        }
        if action not in mapping:
            raise Invalid("stage improvement action is outside the allowed vocabulary")
        return AdjustmentProposal(
            mapping[action],
            message,
            patches,
            rerun,
            request_feedback,
        )

    @staticmethod
    def _patches(value) -> tuple[StagePatch, ...]:
        if value in (None, []):
            return ()
        if type(value) is not list:
            raise Invalid("stage improvement patches must be an array")
        patches = []
        for item in value:
            if type(item) is not dict:
                raise Invalid("stage improvement patch must be an object")
            candidates = item.get("candidates", [])
            if type(candidates) is not list:
                raise Invalid("stage improvement candidates must be an array")
            parsed_candidates = []
            for candidate in candidates:
                if type(candidate) is not dict:
                    raise Invalid("stage improvement candidate must be an object")
                parsed_candidates.append(
                    AgentRef(
                        str(candidate.get("type", "")), str(candidate.get("uuid", ""))
                    )
                )
            patches.append(
                StagePatch(
                    stage=item.get("stage", ""),
                    instruction=item.get("instruction", ""),
                    candidates=tuple(parsed_candidates),
                )
            )
        return tuple(patches)

    @staticmethod
    def _rerun(value) -> tuple[str, ...]:
        if value in (None, []):
            return ()
        if type(value) is not list or any(type(name) is not str for name in value):
            raise Invalid("stage improvement rerun must contain stage names")
        return tuple(value)


class OpenApiCandidateIsolation:
    """Provide a one-candidate OpenAPI session namespace for the frozen graph."""

    def __init__(self, identities: BotRepositoryRolloutIdentity) -> None:
        self._identities = identities

    def require(self, reference, execution_ref, stages, context) -> int:
        del context
        if reference != "per-candidate-workspace@1":
            raise Invalid("unsupported candidate isolation capability")
        if execution_ref != "openapi-bot/v1":
            raise Invalid("candidate isolation does not support this executor")
        if any(
            candidate.agent_type != "bot"
            for stage in stages
            for candidate in stage.candidates
        ):
            raise Invalid("OpenAPI isolation requires Bot candidates")
        return 1

    async def prepare(self, reference, namespace, candidate, context) -> dict[str, str]:
        self.require(reference, "openapi-bot/v1", (), context)
        identity = self._identities.resolve(candidate, context)
        material = (
            f"{namespace}:{identity.bot_id}:{identity.owner_id}:{identity.lifecycle_stage}"
        ).encode()
        return {
            "workspace": f"openapi-run:{namespace}",
            "artifact_namespace": f"artifacts:{sha256(material).hexdigest()}",
            "execution_key": f"execution:{sha256(material).hexdigest()}",
        }

    async def verify_route(
        self, reference, namespace, route: SessionRoute, context
    ) -> None:
        del namespace, context
        if reference != "per-candidate-workspace@1":
            raise Invalid("unsupported candidate isolation capability")
        if (
            route.provider != "openapi_bot"
            or not route.session_id.strip()
            or not route.run_id.strip()
            or not route.bot_id.strip()
            or not route.owner_id.strip()
        ):
            raise Invalid("OpenAPI route does not carry an isolated real session")

    async def verify_artifacts(self, reference, namespace, artifacts, context) -> None:
        self.require(reference, "openapi-bot/v1", (), context)
        del namespace
        if not artifacts:
            raise Invalid("isolated acceptance requires artifact evidence")


class ChannelFeedbackNotifications:
    """Delivery through the configured notification channel; no local no-op."""

    def __init__(self, sender) -> None:
        self._sender = sender

    def require(self) -> None:
        if not getattr(self._sender, "channels", frozenset()):
            raise Invalid("feedback notification sender is not installed")

    async def notify(self, key, collet_id, rollout_id, deadline, context) -> None:
        del key
        from agentclaw.community.plugin_api.notify_sender import NotifyMessage

        message_id = self._sender.send(
            NotifyMessage(
                title="Product rollout等待人工反馈",
                body=(
                    f"Collet {collet_id} rollout {rollout_id} 已失败。"
                    f"请在 {deadline} 前提交处理反馈。"
                ),
                recipient=context.actor.principal_id,
                extra={
                    "product_collet_id": collet_id,
                    "product_rollout_id": rollout_id,
                    "feedback_deadline": deadline,
                },
            ),
            channel=next(iter(self._sender.channels)),
        )
        if not message_id:
            raise Invalid("feedback notification delivery failed")


class FeedbackShutdownInteraction:
    """Recognize only an explicit authenticated shutdown request outside waiting."""

    _SHUTDOWN_WORDS = {"SHUTDOWN", "停止", "关闭", "结束", "停止执行"}

    async def propose(self, snapshot, content, context):
        from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
            Adjustment,
        )

        del snapshot, context
        normalized = content.strip().strip("。.，,")
        if normalized.upper() in self._SHUTDOWN_WORDS or any(
            normalized == word for word in self._SHUTDOWN_WORDS
        ):
            return Adjustment(
                A.SHUTDOWN, "Authenticated human explicitly requested shutdown."
            )
        return Adjustment(
            A.DO_NOTHING,
            "Only an explicit shutdown request is accepted outside feedback waiting.",
        )
