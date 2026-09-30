import pytest
from pydantic import ValidationError

from agentclaw.community.core.digital_employee.events import (
    APPROVAL_RESULT, ONBOARDED, DigitalEmployeeEvent,
)


def event(event_type=ONBOARDED):
    return {
        "specversion": "1.0", "id": "3e5c47aa-ecc8-4972-8123-f70a9a11ef57",
        "source": "urn:ant:aiworkmng", "type": event_type,
        "subject": "digital-employee/AI00000124", "time": "2026-08-25T02:30:45.123Z",
        "datacontenttype": "application/json", "data": {"workNo": "AI00000124"},
    }


def test_onboarded_accepts_missing_optional_details_for_later_lookup():
    parsed = DigitalEmployeeEvent.model_validate(event())
    assert parsed.data["workNo"] == "AI00000124"


@pytest.mark.parametrize("field,value", [
    ("source", "untrusted"), ("specversion", "2.0"),
    ("subject", "digital-employee/another"), ("id", "not-an-event-id"),
    ("time", "2026-08-25T02:30:45"),
])
def test_rejects_invalid_envelope(field, value):
    payload = event()
    payload[field] = value
    with pytest.raises(ValidationError):
        DigitalEmployeeEvent.model_validate(payload)


@pytest.mark.parametrize("operate,status", [
    ("agree", "APPROVED"), ("disagree", "REJECTED"),
    ("reject", "REJECTED"), ("cancel", "CANCELED"),
])
def test_approval_requires_exact_documented_terminal_mapping(operate, status):
    payload = event(APPROVAL_RESULT)
    payload["data"].update(taskId="task-1", requestId="request-1", platformCode="platform",
                           agentId="agent-1", agentCode="code-1", operate=operate,
                           approvalStatus=status)
    assert DigitalEmployeeEvent.model_validate(payload).data["approvalStatus"] == status
    payload["data"]["approvalStatus"] = "UNKNOWN"
    with pytest.raises(ValidationError):
        DigitalEmployeeEvent.model_validate(payload)


def test_approval_without_task_correlation_is_rejected():
    with pytest.raises(ValidationError):
        DigitalEmployeeEvent.model_validate(event(APPROVAL_RESULT))
