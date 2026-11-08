"""Engine-neutral, durable delivery values; targets never come from model output."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from engine.community.kernel.dws_hosting import Binding, Identifier, Value

AI_LABEL = "【AI 助理代回复】"


class PreflightError(RuntimeError):
    """Safe code for a failure before any message write was attempted."""

    def __init__(self, code: str, *, retryable: bool = False):
        super().__init__(code)
        self.retryable = retryable


class DeliveryPolicy(Value):
    retry_delays_ms: tuple[int, ...] = (2000, 5000, 15000)
    lease_ms: Annotated[int, Field(ge=1000)] = 60000
    poll_interval_ms: Annotated[int, Field(ge=1000)] = 5000
    max_polls: Annotated[int, Field(ge=1, le=100)] = 12

    @model_validator(mode="after")
    def bounds(self):
        if len(self.retry_delays_ms) > 10 or any(x < 1 for x in self.retry_delays_ms):
            raise ValueError("bounded positive retry delays required")
        return self


class Delivery(Value):
    delivery_id: Identifier
    turn_id: Identifier
    binding: Binding
    conversation_id: Identifier
    purpose: Literal["reply", "owner_notice"]
    text: Annotated[str, Field(min_length=1, max_length=10000)]

class DeliveryReceipt(Value):
    status: Literal["sent", "accepted", "unknown", "rejected"]
    task_id: str = ""
    message_id: str = ""
    conversation_id: str = ""
    queryable: bool = True

    @model_validator(mode="after")
    def reference(self):
        for identifier in (self.task_id, self.message_id, self.conversation_id):
            if len(identifier) > 512 or any(ord(c) < 32 for c in identifier):
                raise ValueError("invalid receipt identity")
        if self.status == "sent" and not (self.message_id and self.conversation_id):
            raise ValueError("confirmed message reference required")
        if self.status == "accepted" and not self.task_id:
            raise ValueError("accepted task reference required")
        if not self.queryable and self.status != "accepted":
            raise ValueError("only accepted receipts may declare no query support")
        return self


class DeliveryRecord(Value):
    delivery: Delivery
    state: Literal[
        "queued",
        "claimed",
        "sending",
        "awaiting",
        "checking",
        "unknown",
        "sent",
        "accepted",
        "manual",
        "cancelled",
    ]
    claim_id: str
    attempts: int
    polls: int
    due_ms: int
    lease_until_ms: int
    task_id: str
    message_id: str
    remote_conversation_id: str
    reason: str
