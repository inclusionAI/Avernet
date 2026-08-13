from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

# --- Response Data Models ---
#
# These models are served on the public /openapi/v1/bots/logs surface as well
# as internally, so docstrings and field descriptions are caller-facing prose;
# rationale and internal component names belong in # comments.

# One trace = one recorded chat turn. Shared field texts, stated once so the
# list and detail models cannot drift apart.
_TRACE_ID_DESC = (
    "Trace id of the recorded chat turn — globally unique; pass it to the "
    "trace-detail endpoint."
)
_BIZ_SCENE_DESC = (
    "Business label supplied by the integrating system, naming the calling "
    "business domain. Null unless a label was attached to the trace or "
    "registered as a relation afterwards."
)
_BIZ_TASK_ID_DESC = (
    "The integrating system's own task id within its business scene; always "
    "paired with biz_scene. Null unless labelled."
)
_SESSION_ID_DESC = (
    "The engine's canonical session id; may equal session_key when the "
    "engine reported only one of the two."
)
_SESSION_KEY_DESC = (
    "The engine session key grouping this conversation's traces — the "
    "exact-match key the session-traces endpoint takes."
)
_BOT_NAME_DESC = (
    "The bot's current display name; null when the bot no longer resolves — "
    "fall back to bot_id."
)
_GROUP_ID_DESC = (
    "The collaboration group the session belongs to; null for non-group "
    "sessions."
)
_SESSION_KIND_DESC = (
    "Kind of group session: 'chat' — an ordinary group conversation; "
    "'service_invocation' — a request/response style invocation of the "
    "group. Null for non-group sessions."
)
# Hardcoded SUCCESS on this read path today; per-span failure is recorded but
# not surfaced, so the honest public wording is "reserved".
_STATUS_DESC = (
    "Reserved: 'SUCCESS' or 'FAILED'. Records returned today always report "
    "'SUCCESS' — do not use this field to detect a failed turn."
)
_TIMESTAMP_DESC = (
    "When the turn started (ISO 8601 UTC); empty when the source reported "
    "no time."
)
_USER_ID_DESC = "The user the turn belongs to."
_TOTAL_COST_DESC = "Model cost of the turn in USD; 0 when not reported."
_LATENCY_DESC = "Duration in milliseconds; 0 when not reported."
_TOTAL_TOKENS_DESC = "Total model tokens; 0 when not reported."
_NAME_DESC = "Trace name reported by the engine."


class SessionMetadata(BaseModel):
    """Metadata recorded with a trace."""

    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="The trace's attribute bag as recorded at ingest — e.g. "
        "identity.owner_id, identity.bot_id, gen_ai.request.model, "
        "gen_ai.usage.* token counts, and derived usage_details / "
        "cost_details breakdowns. A superset of what the emitter sent.",
    )


class ConversationSession(BaseModel):
    """One recorded chat turn (trace), as a list item.

    List items carry a preview only — read the trace detail for the full
    input, output and observation tree.
    """

    id: str = Field(..., description=_TRACE_ID_DESC)
    biz_task_id: str | None = Field(default=None, description=_BIZ_TASK_ID_DESC)
    biz_scene: str | None = Field(default=None, description=_BIZ_SCENE_DESC)
    session_id: str | None = Field(default=None, description=_SESSION_ID_DESC)
    session_key: str | None = Field(default=None, description=_SESSION_KEY_DESC)
    bot_id: str | None = Field(
        default=None, description="The bot that handled the turn."
    )
    bot_name: str | None = Field(default=None, description=_BOT_NAME_DESC)
    group_id: str | None = Field(default=None, description=_GROUP_ID_DESC)
    session_kind: str | None = Field(default=None, description=_SESSION_KIND_DESC)
    name: str = Field(default="", description=_NAME_DESC)
    input: str | None = Field(
        default=None,
        description="The last user message extracted from the turn's input.",
    )
    output_preview: str | None = Field(
        default=None,
        description="The turn's output, truncated to its first 500 characters.",
    )
    search_output: str | None = Field(
        default=None,
        exclude=True,
        repr=False,
        description="Full output retained internally for client-side filtering",
    )
    match_sources: list[str] = Field(
        default_factory=list,
        description="How a task query matched this trace: 'direct' — the "
        "business labels are on the trace itself; 'biz_ref' — matched "
        "through a registered relation. Empty outside task queries.",
    )
    status: str = Field(default="SUCCESS", description=_STATUS_DESC)
    timestamp: str = Field(..., description=_TIMESTAMP_DESC)
    user_id: str | None = Field(default=None, description=_USER_ID_DESC)
    metadata: SessionMetadata | None = Field(
        default=None, description="The trace's recorded attributes."
    )
    total_cost: float = Field(default=0.0, description=_TOTAL_COST_DESC)
    latency_ms: float = Field(default=0.0, description=_LATENCY_DESC)
    total_tokens: int = Field(default=0, description=_TOTAL_TOKENS_DESC)


class ConversationObservation(BaseModel):
    """One span of a trace — the turn itself, a model generation, or a tool
    call — with its child spans nested."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "a3f81c2b90d4e5f6",
                "biz_task_id": None,
                "biz_scene": None,
                "name": "write",
                "type": "TOOL",
                "latency_ms": 240.0,
                "total_cost": 0.0,
                "total_tokens": 0,
                "input": {"path": "reports/q3.md"},
                "output": {"ok": True},
                "metadata": {"attributes": {"gen_ai.span.kind": "TOOL"}},
                "model_name": None,
                "parent_observation_id": "9b7c1d2e3f405162",
                "children": [],
            }
        }
    )

    id: str = Field(
        description="Span id — unique per observation. Distinct from the "
        "trace id, even for the turn's root span."
    )
    biz_task_id: str | None = Field(default=None, description=_BIZ_TASK_ID_DESC)
    biz_scene: str | None = Field(default=None, description=_BIZ_SCENE_DESC)
    name: str = Field(
        default="",
        description="The span's name — for a tool span, the tool's name.",
    )
    type: str = Field(
        ...,
        description="Span kind: 'CHAT' — the chat-turn root; 'LLM' — a model "
        "generation; 'TOOL' — a tool call; 'SPAN' — anything else. Migrated "
        "records may carry other labels (e.g. 'GENERATION'); treat unknown "
        "values as 'SPAN'.",
    )
    latency_ms: float = Field(default=0.0, description=_LATENCY_DESC)
    total_cost: float = Field(default=0.0, description=_TOTAL_COST_DESC)
    total_tokens: int = Field(default=0, description=_TOTAL_TOKENS_DESC)
    input: Any = Field(
        default=None,
        description="What went into the span — for a model span the "
        "messages, for a tool span the call arguments. JSON when the "
        "recorded value parses as JSON, otherwise the raw string.",
    )
    output: Any = Field(
        default=None,
        description="What the span produced — a model span's reply, a tool "
        "span's result. JSON when it parses, otherwise the raw string.",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="The span's raw recorded metadata — its attribute bag "
        "nested under an 'attributes' key.",
    )
    model_name: str | None = Field(
        default=None,
        description="Model that served a generation span; null for "
        "non-model spans.",
    )
    parent_observation_id: str | None = Field(
        default=None,
        description="Span id of the parent observation; null for a root.",
    )
    children: list["ConversationObservation"] = Field(
        default_factory=list,
        description="Child observations, in start order.",
    )


class ConversationDetail(BaseModel):
    """One recorded chat turn (trace) in full, including its observation tree."""

    id: str = Field(..., description=_TRACE_ID_DESC)
    biz_task_id: str | None = Field(default=None, description=_BIZ_TASK_ID_DESC)
    biz_scene: str | None = Field(default=None, description=_BIZ_SCENE_DESC)
    session_id: str | None = Field(default=None, description=_SESSION_ID_DESC)
    session_key: str | None = Field(default=None, description=_SESSION_KEY_DESC)
    bot_id: str | None = Field(
        default=None, description="The bot that handled the turn."
    )
    bot_name: str | None = Field(default=None, description=_BOT_NAME_DESC)
    group_id: str | None = Field(default=None, description=_GROUP_ID_DESC)
    session_kind: str | None = Field(default=None, description=_SESSION_KIND_DESC)
    name: str = Field(default="", description=_NAME_DESC)
    input: Any = Field(
        default=None,
        description="The turn's full input — typically the chat messages "
        "sent to the bot, e.g. a list of role/content objects. JSON when "
        "the recorded value parses as JSON, otherwise the raw string.",
    )
    output: Any = Field(
        default=None,
        description="The turn's full output — typically the assistant's "
        "reply message. JSON when it parses, otherwise the raw string.",
    )
    status: str = Field(default="SUCCESS", description=_STATUS_DESC)
    timestamp: str = Field(..., description=_TIMESTAMP_DESC)
    user_id: str | None = Field(default=None, description=_USER_ID_DESC)
    metadata: SessionMetadata | None = Field(
        default=None, description="The trace's recorded attributes."
    )
    total_cost: float = Field(default=0.0, description=_TOTAL_COST_DESC)
    latency_ms: float = Field(default=0.0, description=_LATENCY_DESC)
    total_tokens: int = Field(default=0, description=_TOTAL_TOKENS_DESC)
    observations: list[ConversationObservation] = Field(
        default_factory=list,
        description="Root observations with children nested — typically one "
        "root (the chat turn) whose children are the model and tool spans.",
    )


class SessionListResponse(BaseModel):
    """Paginated list of recorded chat turns (traces), newest first."""

    sessions: list[ConversationSession] = Field(
        default_factory=list,
        description="The current page of traces. Items carry previews only — "
        "read the trace detail for full input/output and observations.",
    )
    total: int = Field(default=0, description="Total number of matching sessions")
    page: int = Field(
        default=1, description="The effective 1-based page number answered."
    )
    limit: int = Field(default=20, description="The effective page size answered.")
    has_more: bool = Field(
        default=False, description="Whether more pages exist beyond this one."
    )


class HealthCheckData(BaseModel):
    """Health check response data."""

    status: str = Field(..., description="healthy or unhealthy")
    langfuse_url: str | None = Field(default=None)
    error: str | None = Field(default=None)


# --- Generic API Response ---


T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """Unified API response envelope."""

    success: bool
    message: str
    error_code: int = 200
    data: T | None = None
