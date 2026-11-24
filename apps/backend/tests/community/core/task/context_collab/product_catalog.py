"""Test-owned catalog assembled from the same explicit product installation as DI."""

from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    WorkflowProfile,
)
from agentclaw.community.core.task.context_collab.l6_products.task_contracts import (
    STANDARD_TASK_CONTRACT,
)

from agentclaw.community.di.modules.task.context_collab_products import (
    INSTALLED_PRODUCT_PROFILES,
    build_product_catalog,
)

PRODUCT_CATALOG = build_product_catalog()
PRODUCT_PROFILES = INSTALLED_PRODUCT_PROFILES


def standard_task_workflow() -> WorkflowProfile:
    """Author an explicit workflow variant instead of relying on an omitted one."""
    contract = STANDARD_TASK_CONTRACT
    return WorkflowProfile(
        ref=contract.state_machine_ref,
        state_machine_ref=contract.state_machine_ref,
        completion_states=contract.completion_states,
        compensation_events=contract.compensation_events,
        strict_control_events=contract.strict_control_events,
        executor_submission_only=contract.executor_submission_only,
        public_events_only=contract.public_events_only,
        actions=contract.actions,
    )
