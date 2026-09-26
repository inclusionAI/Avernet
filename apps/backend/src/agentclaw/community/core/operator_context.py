"""Operator context for service layer."""
from pydantic import BaseModel


class OperatorContext(BaseModel):
    """Context information about the current operator."""
    staff_id: str
    staff: str
    nick_name: str | None = None
    operator_name: str | None = None
    tenant_id: str | None = None
