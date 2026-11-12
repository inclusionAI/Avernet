"""Read-only independent Caller observation."""

from pydantic import BaseModel, Field


class CallerInstanceStatus(BaseModel):
    exists: bool = Field(description="A Caller UUID is registered, including after release.")
    alive: bool = Field(description="The registered Caller currently has an ACTIVE healthy device.")
