"""Authorized five-method product orchestration. All blocking SQL is offloaded."""

import asyncio
from dataclasses import asdict
from copy import deepcopy
from uuid import uuid4
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
    ContextCollabVersionConflict as Conflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    current_actor_context,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.profile_publication import (
    ProductProfilePublicationAuthorizer,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_values import (
    positive_id,
    text,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    AdjustTypeEnum as A,
    StatusEnum,
)
from agentclaw.community.core.task.context_collab.l5_methods.collet_rollout import (
    ColletRollout,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    ProductRuntimeAuthorization,
    RolloutExecutionPort,
    RolloutInteractionPort,
    StageSessionMessagesPort,
    Adjustment,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_lifecycle import (
    affected_stages,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile import (
    ProductProfile,
    YAML,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    MethodCompositionTemplate,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.contracts import (
    ProductProfileServiceProtocol,
    InteractiveResult,
    StageRolloutDetails,
    RolloutList,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    validate_configuration,
    multi_policy,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.ports import (
    ProductProfileRepository,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.snapshot_codec import (
    canonical,
)


from ...l5_methods.multi_rollout_projection import control_metadata
from ...l5_methods.multi_rollout_controller import MultiRolloutController


class ProductProfileService(ProductProfileServiceProtocol):
    def __init__(
        self,
        repository: ProductProfileRepository,
        publisher: ProductProfilePublicationAuthorizer,
        authorization: ProductRuntimeAuthorization,
        execution: RolloutExecutionPort,
        interaction: RolloutInteractionPort,
        messages: StageSessionMessagesPort,
        multi_rollout: MultiRolloutController | None = None,
    ) -> None:
        self._repository = repository
        self._publisher = publisher
        self._authorization = authorization
        self._execution = execution
        self._interaction = interaction
        self._messages = messages
        self._multi_rollout = multi_rollout

    async def create_product_profile(
        self,
        name: str,
        composition: MethodCompositionTemplate,
        stages: YAML,
    ) -> ProductProfile:
        context = current_actor_context()
        text(name, maximum=256)
        # Keep the existing independently configured management authority.
        self._publisher.check_publish(name, 1, context)
        specs, execution_policy = await asyncio.to_thread(
            validate_configuration, composition, stages, self._execution
        )
        await self._multi_setup(composition, specs, execution_policy, context)
        return await asyncio.to_thread(
            self._repository.create_profile,
            name,
            composition,
            stages,
            context,
            uuid4().hex,
        )

    async def create_product_collet(self, profile_id: int, content: str) -> Collet:
        context = current_actor_context()
        positive_id(profile_id)
        text(content)
        profile, owner = await asyncio.to_thread(
            self._repository.get_profile, profile_id
        )
        self._authorization.check("create", context, owner)
        specs, execution_policy = await asyncio.to_thread(
            validate_configuration, profile.composition, profile.stages, self._execution
        )
        setup = await self._multi_setup(
            profile.composition, specs, execution_policy, context
        )
        kwargs = {"multi_setup": setup} if setup else {}
        snapshot = await asyncio.to_thread(
            self._repository.create_collet,
            profile,
            content,
            context,
            uuid4().hex,
            **kwargs
        )
        return snapshot.collet

    async def _multi_setup(self, composition, specs, execution_policy, context):
        policy = multi_policy(composition)
        if policy is None:
            return None
        if self._multi_rollout is None:
            raise Invalid("MultiRollout host capabilities are not configured")
        return await asyncio.to_thread(
            self._multi_rollout.configure,
            policy,
            specs,
            execution_policy.execution_ref,
            context,
        )

    async def query_collet_rollout(
        self, collet_id: int, all_rollout: bool = True
    ) -> list[ColletRollout]:
        context = current_actor_context()
        positive_id(collet_id)
        if type(all_rollout) is not bool:
            raise Invalid("all_rollout must be a boolean")
        snapshot = await asyncio.to_thread(self._repository.get_collet, collet_id)
        self._authorization.check("read", context, snapshot.owner)
        values = (
            snapshot.rollouts
            if all_rollout or not snapshot.rollouts
            else [snapshot.current()]
        )
        return RolloutList(
            values,
            snapshot.revision,
            snapshot.current_rollout_id,
            snapshot.stopped,
            multi_rollout_control=control_metadata(snapshot),
        )

    async def interactive(
        self,
        collet_rollout_id: int,
        interactive_uuid: str,
        interactive_context: str,
    ) -> InteractiveResult:
        context = current_actor_context()
        positive_id(collet_rollout_id)
        text(interactive_uuid, maximum=128)
        text(interactive_context)
        snapshot = await asyncio.to_thread(
            self._repository.locate, collet_rollout_id, "rollout"
        )
        self._authorization.check("interactive", context, snapshot.owner)
        payload = {
            "rollout_id": collet_rollout_id,
            "content": interactive_context,
        }
        replay = await asyncio.to_thread(
            self._repository.replay, interactive_uuid, "interactive", payload, context
        )
        if replay is not None:
            return InteractiveResult(A(replay["type"]), replay["message"])
        if snapshot.current_rollout_id != collet_rollout_id:
            raise Conflict("stale rollout")
        if snapshot.multi:
            if self._multi_rollout is None:
                raise Invalid("MultiRollout controller is not installed")
            try:
                result = await self._multi_rollout.interactive(
                    snapshot, interactive_uuid, interactive_context, context
                )
            except Conflict:
                result = await asyncio.to_thread(
                    self._repository.replay,
                    interactive_uuid,
                    "interactive",
                    payload,
                    context,
                )
                if result is None:
                    raise
            return InteractiveResult(A(result["type"]), result["message"])
        revision = snapshot.revision
        adjustment = await self._interaction.propose(
            deepcopy(snapshot), interactive_context, context
        )
        if (
            type(adjustment) is not Adjustment
            or type(adjustment.type) is not A
            or type(adjustment.stages) is not tuple
        ):
            raise Invalid("invalid interaction proposal")
        text(adjustment.message)
        text(adjustment.instruction, empty=True)
        if any(type(name) is not str for name in adjustment.stages):
            raise Invalid("adjusted stages must be names")
        intents, successor = [], None
        if adjustment.type in {A.OPTIMIZE, A.RE_DO}:
            affected = affected_stages(snapshot, adjustment.stages)
            # Preserve complete iteration context; the instruction cannot edit
            # the frozen candidate/DAG/acceptance configuration.
            content = canonical(
                {
                    "previous_context": snapshot.current().content,
                    "feedback": interactive_context,
                    "instruction": adjustment.instruction,
                }
            )
            text(content)
            successor = (content, affected)
        elif adjustment.type is A.SHUTDOWN:
            if adjustment.stages or adjustment.instruction:
                raise Invalid("shutdown cannot edit stages")
            snapshot.stopped = True
        elif adjustment.stages or adjustment.instruction:
            raise Invalid("no-op cannot edit stages")
        if adjustment.type is not A.DO_NOTHING:
            live_stage_ids = {
                s.stage_rollout_id
                for s in snapshot.current().stages
                if s.status
                not in {
                    StatusEnum.RUN_FAILED,
                    StatusEnum.ACCEPTANCE_FAILED,
                    StatusEnum.ACCEPTANCE_SUCCEED,
                }
            }
            for record in snapshot.records:
                if (
                    record.stage_id in live_stage_ids
                    and record.attempt
                    and record.dispatch_state
                    not in {
                        "revoked",
                        "reused",
                    }
                ):
                    record.dispatch_state = "revoked"
                    record.lease_until = 0
                    if record.route:
                        intents.append(
                            {
                                "kind": "cancel",
                                "stage_id": record.stage_id,
                                "route": asdict(record.route),
                            }
                        )
        result = {"type": adjustment.type.value, "message": adjustment.message}
        if successor or (
            adjustment.type is A.DO_NOTHING
            and not snapshot.stopped
            and snapshot.current().status
            not in {
                StatusEnum.RUN_FAILED,
                StatusEnum.ACCEPTANCE_FAILED,
                StatusEnum.ACCEPTANCE_SUCCEED,
            }
        ):
            intents.append({"kind": "advance"})
        try:
            committed = await asyncio.to_thread(
                self._repository.save,
                snapshot,
                revision,
                receipt=(interactive_uuid, "interactive", payload, context, result),
                intents=tuple(intents),
                successor=successor,
            )
        except Conflict:
            # A duplicate may have committed after save's receipt read but before
            # its CAS. Re-read in a new transaction; never retry a stale proposal.
            committed = await asyncio.to_thread(
                self._repository.replay,
                interactive_uuid,
                "interactive",
                payload,
                context,
            )
            if committed is None:
                raise
        return InteractiveResult(A(committed["type"]), committed["message"])

    async def query_stage_rollout_details(
        self, stage_rollout_id: int, *, limit: int = 50
    ) -> StageRolloutDetails:
        context = current_actor_context()
        positive_id(stage_rollout_id)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise Invalid("message limit must be an integer in 1..200")
        snapshot = await asyncio.to_thread(
            self._repository.locate, stage_rollout_id, "stage"
        )
        self._authorization.check("read_session", context, snapshot.owner)
        stage = next(
            s
            for r in snapshot.rollouts
            for s in r.stages
            if s.stage_rollout_id == stage_rollout_id
        )
        record = next(r for r in snapshot.records if r.stage_id == stage_rollout_id)
        if not stage.session_id:
            return StageRolloutDetails(stage_rollout_id, "", [], False, limit)
        if record.route is None or record.route.session_id != stage.session_id:
            raise Invalid("stage has no trustworthy persisted session route")
        messages = await self._messages.read(record.route, limit)
        if type(messages) is not list or len(messages) > limit:
            raise Invalid("message provider violated the bounded list contract")
        return StageRolloutDetails(
            stage_rollout_id, stage.session_id, messages, True, limit
        )
