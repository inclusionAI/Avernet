"""Validate platform v1 messages before making any domain mutation."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, model_validator


ONBOARDED = "com.ant.aiworkmng.digital-employee.onboarded.v1"
APPROVAL_RESULT = "com.ant.aiworkmng.digital-employee.skill-change-approval-result.v1"


class DigitalEmployeeEvent(BaseModel):
    specversion: Literal["1.0"]
    id: UUID
    source: Literal["urn:ant:aiworkmng"]
    type: str
    subject: str
    time: datetime
    datacontenttype: Literal["application/json"]
    data: dict[str, Any]

    @model_validator(mode="after")
    def validate_identity(self) -> "DigitalEmployeeEvent":
        work_no = self.data.get("workNo")
        if not isinstance(work_no, str) or not work_no.strip() or len(work_no) > 32:
            raise ValueError("event workNo is required")
        if self.subject != f"digital-employee/{work_no}":
            raise ValueError("event subject and workNo mismatch")
        if self.time.tzinfo is None:
            raise ValueError("event time must include timezone")
        if self.type == APPROVAL_RESULT:
            for field in ("taskId", "requestId", "platformCode", "agentId", "agentCode"):
                if not isinstance(self.data.get(field), str) or not self.data[field].strip():
                    raise ValueError(f"approval event requires {field}")
            statuses = {"agree": "APPROVED", "disagree": "REJECTED",
                        "reject": "REJECTED", "cancel": "CANCELED"}
            if self.data.get("operate") not in statuses or statuses[self.data["operate"]] != self.data.get("approvalStatus"):
                raise ValueError("approval operation and status mismatch")
        return self
