"""Reusable Task lifecycle/action contracts; no product plans or transition tables."""

from types import MappingProxyType

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    ROLE_SELECTED_AGENT,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    ProductProfileDefinition,
    TaskAction,
    TaskLifecycleContract,
    VersionedProfileRef,
    WorkflowProfile,
)

TASK_PROGRESS_DETAIL_FIELDS = ("progress", "error_msg", "reason", "task_id")

# Never change this default to "latest". Existing omitted selections pin @1.
STANDARD_TASK_CONTRACT_REF = VersionedProfileRef("standard_task", 1)
STANDARD_TASK_CONTRACT = TaskLifecycleContract(
    ref=STANDARD_TASK_CONTRACT_REF,
    # Reuse the existing G4 machine; no parallel transition or guard table.
    state_machine_ref=VersionedProfileRef("task_commission_goal", 1),
    completion_states=("done",),
    compensation_events=("task.rejected", "task.cancelled"),
    actions=(
        TaskAction(
            "start_task",
            "task.started",
            "Task",
            ("note",),
            required_role=ROLE_SELECTED_AGENT,
        ),
        TaskAction(
            "report_progress",
            "task.progress.updated",
            "Task",
            ("note", "percent", *TASK_PROGRESS_DETAIL_FIELDS),
            required_role=ROLE_SELECTED_AGENT,
        ),
        TaskAction(
            "submit_task",
            "task.submitted",
            "Task",
            ("note", "artifacts"),
            required_role=ROLE_SELECTED_AGENT,
        ),
        TaskAction(
            "accept_task",
            "task.accepted",
            "Task",
            ("note",),
        ),
        TaskAction(
            "reject_task",
            "task.rejected",
            "Task",
            ("note",),
        ),
        TaskAction(
            "cancel_task",
            "task.cancelled",
            "Task",
            ("note",),
        ),
    ),
)

TASK_CONTRACTS = MappingProxyType({STANDARD_TASK_CONTRACT.ref: STANDARD_TASK_CONTRACT})


def resolve_event_contract(
    profile: ProductProfileDefinition,
) -> TaskLifecycleContract | WorkflowProfile:
    """One authority: legacy workflow OR exact Task contract; never merge them.

    WorkflowProfile remains supported for historical/event-stream products.
    Combining two authorities is deliberately unsupported until composition of
    their constraints is defined, rather than silently weakening either one.
    """
    if profile.workflow is not None:
        if profile.task_contract_ref is not None:
            raise ContextCollabValidationError(
                "workflow and task_contract_ref cannot both own Task semantics"
            )
        return profile.workflow
    ref = profile.task_contract_ref or STANDARD_TASK_CONTRACT_REF
    try:
        return TASK_CONTRACTS[ref]
    except KeyError:
        raise ContextCollabValidationError(
            f"required versioned Task contract is unavailable: {ref}"
        ) from None
