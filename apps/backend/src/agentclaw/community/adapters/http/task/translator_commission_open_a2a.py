"""Wire translations for bounded L4 Query discovery and L5 Open application.

First response part remains AccsOperationResult; a successful query/apply adds
one data part with the minimal product projection/admission receipt.
"""

from dataclasses import asdict

from agentclaw.community.adapters.http.task.commission_a2a_schemas import (
    ISSUER,
    authenticated_operation_deadline,
)
from agentclaw.community.api.task.task_commission_service import (
    TaskCommissionServiceProtocol,
)
from agentclaw.community.core.gateway_principal import GatewayBot
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.common import (
    ObjectRef,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.envelope import (
    AccsOperationEnvelope,
    AccsOperationResult,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    ApplyForTaskCommand,
)


def execute_open_operation(
    operation: AccsOperationEnvelope,
    bot: GatewayBot,
    service: TaskCommissionServiceProtocol,
) -> tuple[AccsOperationResult, dict]:
    authenticated_operation_deadline(operation, bot)
    ctx = operation.context
    profile = ctx.product_profile_ref
    if (
        profile != ObjectRef("ProductProfile", "task_commission", "1", ISSUER)
        or operation.payload != {}
        or operation.expected_revision is not None
    ):
        raise ContextCollabValidationError("unsupported opportunity operation context")
    ref = VersionedProfileRef(profile.object_id, int(profile.version))
    actor = ActorContext(PrincipalRef("bot", bot.bot_uuid), source="a2a")
    if (
        operation.layer,
        operation.operation_family,
        operation.action,
        operation.mode,
    ) == ("L4", "query", "opportunities", "QUERY"):
        if (
            operation.target_ref != profile
            or ctx.collet_ref is not None
            or ctx.method_binding_ref is not None
        ):
            raise ContextCollabValidationError(
                "discovery targets a product, not an admitted context"
            )
        opportunities = service.query_opportunities(ref, actor)
        refs = tuple(
            ObjectRef("OpenOpportunity", item.opportunity_id, None, ISSUER)
            for item in opportunities
        )
        data = {"opportunities": [asdict(item) for item in opportunities]}
    elif (
        operation.layer,
        operation.operation_family,
        operation.action,
        operation.mode,
    ) == ("L5", "open", "apply", "COMMAND"):
        target, binding, collet = (
            operation.target_ref,
            ctx.method_binding_ref,
            ctx.collet_ref,
        )
        if (
            target is None
            or target.object_type != "OpenOpportunity"
            or target.issuer != ISSUER
            or target.version is not None
            or binding is None
            or binding.object_type != "MethodBinding"
            or binding.issuer != ISSUER
            or binding.version is not None
            or collet is None
            or collet.context_type != "COLLET"
        ):
            raise ContextCollabValidationError(
                "application requires opportunity, Method and Collet context"
            )
        receipt = service.apply_for_task(
            ApplyForTaskCommand(
                collet.context_id,
                target.object_id,
                binding.object_id,
                ref,
                bot.owner_id,
            ),
            actor,
        )
        refs = (ObjectRef("AdmissionDecision", receipt.decision_id, None, ISSUER),)
        data = {"admission": asdict(receipt)}
    else:
        raise ContextCollabValidationError("unsupported Query/Open operation")
    return AccsOperationResult(
        ObjectRef("Operation", operation.operation_id, None, ISSUER),
        "SUCCEEDED",
        refs,
        (),
        None,
    ), data
