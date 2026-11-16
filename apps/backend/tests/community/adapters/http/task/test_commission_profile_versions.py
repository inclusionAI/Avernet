"""Work accepts exact product versions, not a hardcoded v1 or malformed selectors."""

import pytest

from agentclaw.community.adapters.http.task.commission_a2a_schemas import report_command
from agentclaw.community.core.gateway_principal import GatewayBot
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.envelope import (
    AccsOperationEnvelope,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    TaskCommissionCreatedView,
)
from tests.community.factories.task_commission import (
    BOT_ID,
    OWNER,
    commission_report_request,
)


@pytest.fixture
def report():
    created = TaskCommissionCreatedView(
        "collet_test", "work", "event_test", "created", 2
    )
    request = commission_report_request(created)
    operation = request["params"]["message"]["parts"][0]["data"]
    bot = GatewayBot(
        bot_uuid=BOT_ID, owner_id=OWNER, app_id=1, agent_code="writer", tenant="test"
    )
    return operation, bot


@pytest.mark.parametrize("version", ["1", "2", "42"])
def test_work_translation_preserves_the_exact_version(report, version):
    operation, bot = report
    operation["context"]["product_profile_ref"]["version"] = version
    command = report_command(AccsOperationEnvelope.from_dict(operation), bot)
    # Translation is not installation or authorization. The service must compare
    # this exact selector with the persisted ProductInstance before any write.
    assert command.product_profile_ref == VersionedProfileRef(
        "task_commission", int(version)
    )


@pytest.mark.parametrize(
    "version",
    [None, "0", "01", "-1", "+1", "1.0", "１", "١", "1" * 5000],
    ids=[
        "absent",
        "zero",
        "leading_zero",
        "negative",
        "signed",
        "decimal",
        "wide",
        "arabic",
        "oversized",
    ],
)
def test_malformed_versions_raise_validation_not_internal_errors(report, version):
    operation, bot = report
    operation["context"]["product_profile_ref"]["version"] = version
    with pytest.raises(ContextCollabValidationError):
        report_command(AccsOperationEnvelope.from_dict(operation), bot)
