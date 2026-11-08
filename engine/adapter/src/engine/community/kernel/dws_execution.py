"""Durable hosting execution values. No engine wire types or delivery targets."""

from typing import Annotated, Literal
import unicodedata

from pydantic import Field, model_validator

from engine.community.kernel.dws_hosting import Identifier, Value
from engine.community.kernel.dws_context import HostingContext
from engine.community.kernel.dws_dialogue import DialogueSnapshot


class ExecutionError(RuntimeError):
    """A stable safe code, never an engine diagnostic or private message body."""


class GatewayWitness(Value):
    host_boot_id: Identifier
    started_lower_ms: Annotated[int, Field(ge=0)]
    started_upper_ms: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def ordered(self):
        if self.started_upper_ms < self.started_lower_ms:
            raise ValueError("invalid gateway start interval")
        return self


class HostingScope(Value):
    decision: Literal["allow", "ignore", "uncertain"]
    reason_code: Literal[
        "inside_rule", "outside_rule", "insufficient_facts", "unavailable"
    ]

    @model_validator(mode="after")
    def consistent(self):
        valid = {
            "allow": {"inside_rule"},
            "ignore": {"outside_rule"},
            "uncertain": {"insufficient_facts", "unavailable"},
        }
        if self.reason_code not in valid[self.decision]:
            raise ValueError("inconsistent scope result")
        return self


class AgentInvocation(Value):
    rpc_id: Identifier
    run_id: Identifier
    agent_id: Identifier
    session_key: Identifier
    message: Annotated[str, Field(min_length=1, max_length=200_000)]
    instructions: Annotated[str, Field(min_length=1, max_length=40_000)]
    hosting_context: HostingContext | None = None
    dialogue_snapshot: DialogueSnapshot | None = None
    dialogue_sync_keys: tuple[str, ...] = ()
    gateway_witness: GatewayWitness | None = None
    scope: HostingScope | None = None


class EngineUpdate(Value):
    run_id: Identifier
    status: Literal["prepared", "accepted", "completed", "failed", "unknown"]
    result_json: Annotated[str, Field(max_length=262_144)] = "{}"
    reason: str = ""
    gateway_witness: GatewayWitness | None = None


class RunObservation(Value):
    run_id: Identifier
    ended: bool


class HostingDecision(Value):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    rule_version: Annotated[int, Field(gt=0)]
    action: Literal["reply", "ignore", "escalate"]
    text: Annotated[str, Field(max_length=8000)] = ""
    summary: Annotated[str, Field(max_length=2000)] = ""

    @model_validator(mode="after")
    def valid_action(self):
        allowed = {"schema_version", "rule_version", "action"}
        if self.action == "reply":
            allowed.add("text")
            if not self.text.strip():
                raise ValueError("reply text required")
        if self.action == "escalate":
            allowed.add("summary")
            if not self.summary.strip():
                raise ValueError("escalation summary required")
        if self.model_fields_set - allowed:
            raise ValueError("unexpected action fields")
        return self


class HostingReview(Value):
    """Bounded internal diagnostics, never a message or delivery authorization."""

    verdict: Literal["approve", "skip_owner_replied", "reject"]
    reason_code: Literal[
        "approved",
        "outside_rule",
        "owner_replied",
        "identity_uncertain",
        "context_insufficient",
        "unsupported_fact",
        "off_topic",
        "other",
        "unspecified",
    ]
    reason: Annotated[str, Field(min_length=1, max_length=240, repr=False)]

    @model_validator(mode="after")
    def valid_review(self):
        allowed = {
            "approve": {"approved", "unspecified"},
            "skip_owner_replied": {"owner_replied", "unspecified"},
            "reject": {
                "outside_rule",
                "identity_uncertain",
                "context_insufficient",
                "unsupported_fact",
                "off_topic",
                "other",
                "unspecified",
            },
        }
        if self.reason_code not in allowed[self.verdict]:
            raise ValueError("inconsistent review verdict and reason code")
        if not self.reason.strip() or any(
            unicodedata.category(c).startswith("C")
            or unicodedata.category(c) in {"Zl", "Zp"}
            for c in self.reason
        ):
            raise ValueError("review reason must be a short single-line explanation")
        return self


class ExecutionRecord(Value):
    turn_id: Identifier
    invocation: AgentInvocation
    state: Literal[
        "dispatching",
        "accepted",
        "received",
        "unknown",
        "resolved",
        "manual",
        "cancelled",
    ]
    result: EngineUpdate | None = None
    decision: HostingDecision | None = None
    reason: str = ""
    review: HostingReview | None = Field(default=None, exclude=True, repr=False)
