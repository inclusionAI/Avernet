from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from agentclaw.community.core.digital_employee.contracts import DigitalEmployeeError
from agentclaw.community.core.service_bot.services.publish_rollback_mixin import PublishRollbackMixin


@pytest.mark.asyncio
async def test_rollback_rejects_unapproved_version_before_changing_online_records():
    service = PublishRollbackMixin()
    service.can_rollback = Mock(return_value=(True, "ok"))
    service._repo = Mock()
    service._repo.get_by_id.side_effect = [SimpleNamespace(last_pub_id=41), SimpleNamespace(id=41)]
    flow = Mock()
    flow.require_employee_approval.side_effect = DigitalEmployeeError("审批未通过")
    service._publish_flow_service_provider = lambda: flow
    with pytest.raises(DigitalEmployeeError, match="审批"):
        await service.rollback_publish(42, "owner")
    service._repo.rollback_flip.assert_not_called()
    flow.execute_rollback.assert_not_called()
