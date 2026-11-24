"""The configured admission Agent explicitly owns Open's review operations."""

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ContextServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    OpenAdmitCommand,
    OpenMethodOpsServiceProtocol,
    OpenScreenCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from tests.community.core.task.context_collab.products.test_task_commission_end_to_end import (
    ADMISSION_JUDGE,
    _create_commission_collet,
    _create_product_profile,
    _discover_opportunity,
    _instantiate_product_instance,
    _publish_opportunity,
)
from tests.community.core.task.context_collab.products.test_task_commission_end_to_end import (
    commission_deployment as _commission_deployment_fixture,
)
from tests.community.core.task.context_collab.products.test_task_commission_end_to_end import (
    deploy_commission as _deploy_commission_fixture,
)

commission_deployment = _commission_deployment_fixture
deploy_commission = _deploy_commission_fixture


@pytest.fixture
def configured_admission_runtime(deploy_commission):
    profile = _create_product_profile()
    _, working_bot, injector = deploy_commission(profile)
    open_ops = injector.get(OpenMethodOpsServiceProtocol)
    collet = _create_commission_collet(injector.get(ContextServiceProtocol))
    _instantiate_product_instance(injector, collet, profile)
    _publish_opportunity(open_ops, collet.collet_id)
    store, axes = injector.get(MethodStoreProtocol), current_axes()
    opportunity, submitted = _discover_opportunity(store, axes, collet, working_bot)
    return open_ops, collet, opportunity, submitted, working_bot


def test_only_the_configured_agent_can_screen_and_admit(configured_admission_runtime):
    open_ops, collet, opportunity, submitted, working_bot = configured_admission_runtime
    context = working_bot.actor_context

    with pytest.raises(
        ContextCollabValidationError, match="configured admission decision Agent"
    ):
        open_ops.screen(
            OpenScreenCommand(
                application_ref=submitted.object_id,
                context=context,
                collect_id=collet.collet_id,
                reason="wrong executor cannot self-review",
            ),
        )

    screened = open_ops.screen(
        OpenScreenCommand(
            application_ref=submitted.object_id,
            context=ADMISSION_JUDGE,
            collect_id=collet.collet_id,
            reason=opportunity.admission_policy_refs[0],
        ),
    )
    assert screened.accepted and screened.state == "SCREENING"

    with pytest.raises(
        ContextCollabValidationError, match="configured admission decision Agent"
    ):
        open_ops.admit(
            OpenAdmitCommand(
                application_ref=submitted.object_id,
                context=context,
                collect_id=collet.collet_id,
                reason="wrong executor cannot admit itself",
            ),
        )

    admitted = open_ops.admit(
        OpenAdmitCommand(
            application_ref=submitted.object_id,
            context=ADMISSION_JUDGE,
            collect_id=collet.collet_id,
            reason=opportunity.admission_policy_refs[0],
        ),
    )
    assert admitted.accepted and admitted.state == "ADMITTED"
