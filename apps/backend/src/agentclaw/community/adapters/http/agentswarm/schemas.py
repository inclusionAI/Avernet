from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentclaw.community.core.agentswarm.domain.models import (
    WIRE_BOT_CHAT_TIMEOUT_CAP, WIRE_MAX_TOTAL_AGENTS_CAP,
    WIRE_MAX_TURNS_CAP, AgentKind, ProviderKind,
)


class AgentSpecDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=128)
    role: str = Field(max_length=20000)
    instructions: str = Field(default="", max_length=20000)
    # Required, not defaulted — same reason as CreateDefinitionDTO.kind: the
    # kind decides whether the agent answers in-process or provisions a
    # persona. The interface creates bot agents only (``llm`` is test-only and
    # has no entry point), so a silent default was the wrong one to fall back
    # to: an omitted kind produced an llm agent that looked like a bot request.
    kind: AgentKind
    model: str | None = None
    engine: str | None = None
    bot_type: str | None = None
    identity_file: str | None = None
    metadata: dict[
        Annotated[str, Field(max_length=200)],
        Annotated[str, Field(max_length=2000)],
    ] = Field(default_factory=dict, max_length=32)
    config_manifest: str = Field(
        default="", max_length=200_000,
        description=(
            "Full five-category bot_config_manifest YAML document for "
            "this agent (mcp/resources/skills/cli_tools/identity + "
            "sources + script), exactly as POST /openapi/v1/bots/"
            "with-manifest accepts it. When present, the swarm "
            "provisions the agent's bot from this document verbatim; "
            "the platform's preflight validates its grammar."
        ),
    )


class RunOptionsDTO(BaseModel):
    """Per-request operational overrides — values absent from the request
    fall back to the deployment's `agentswarm.orchestration.*` config.

    Keys mirror the yaml config keys so operators think in one
    vocabulary. Honored at runtime, clamped to deployment authority:
    model_name (provider default when unset), max_width and
    max_attempts (tighten-only), per_attempt_timeout_seconds and
    slice_max_seconds (tighten-only), and setup_timeout_seconds —
    which may raise up to the deployment's slice room
    (slice_max_seconds minus per_attempt_timeout), so slow-env
    provisioning can be re-budgeted from the request alone.
    bot_chat_timeout_seconds and the bot_chat/per-attempt pairing
    follow the same slice math: the request may re-budget one engine
    chat round trip up to 3600s, with the per-attempt wait_for as the
    runtime cap.
    max_turns and max_total_agents are the run's own budgets: a
    request value replaces the deployment default, bounded by the
    wire caps below (a run may widen its turn budget for slow
    human-in-the-loop authorizations, or tighten its spawn ceiling)."""

    model_config = ConfigDict(extra="forbid")

    model_name: str | None = Field(
        default=None, max_length=128,
        description="Model for agents that don't specify their own.")
    slice_max_seconds: float | None = Field(
        default=None, gt=0, le=3600,
        description="Orchestrator deadline per slice (seconds).")
    per_attempt_timeout_seconds: float | None = Field(
        default=None, gt=0, le=1800,
        description="Upper bound for one attempt task (seconds).")
    bot_chat_timeout_seconds: float | None = Field(
        default=None, gt=0, le=WIRE_BOT_CHAT_TIMEOUT_CAP,
        description="Upper bound for one engine chat round trip (seconds). "
        "Replaces the deployment's executor.bot_chat_timeout_seconds "
        "(default 1800) within this cap, so long-running turns can be "
        "given more room without a redeploy; the per-attempt timeout "
        "still bounds the whole leg.")
    setup_timeout_seconds: float | None = Field(
        default=None, gt=0, le=1800,
        description="Upper bound for one agent's provisioning phase "
        "(spawn + authorization + READY, seconds). Parked time waiting "
        "on human consent never counts against it. Falls to the "
        "deployment's setup_timeout; a requested value wins up to the "
        "deployment's slice room (slice_max_seconds minus "
        "per_attempt_timeout).")
    max_turns: int | None = Field(
        default=None, ge=1, le=WIRE_MAX_TURNS_CAP,
        description="Orchestrator decision-turn budget. Replaces the "
        "deployment default within this cap — human-in-the-loop runs "
        "(bot personas parked on authorization) can widen it.")
    max_total_agents: int | None = Field(
        default=None, ge=1, le=WIRE_MAX_TOTAL_AGENTS_CAP,
        description="Total spawn ceiling for the run.")
    max_width: int | None = Field(
        default=None, ge=1, le=16,
        description="Max concurrently in-flight agents (e.g. 10 tasks "
        "with max_width=3: only 3 run at a time, the rest queue).")
    max_attempts: int | None = Field(
        default=None, ge=1, le=3,
        description="Per-agent retry cap for this run (fresh-context attempts). "
        "Omitted falls to the deployment default (3). Non-retryable failures "
        "(e.g. a space quota ceiling) stop at the first attempt regardless.")


class AgentDefinitionRefDTO(BaseModel):
    """A reference to a stored agent definition."""

    model_config = ConfigDict(extra="forbid")

    definition_id: str = Field(
        max_length=128,
        description="The definition to provision this agent from.",
    )
    alias: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "Optional catalog name to spawn this agent under. Defaults to the "
            "definition's own name; a run may reference the same definition "
            "twice with distinct aliases."
        ),
    )


class SubmitRunDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant: str = Field(min_length=1, max_length=64)
    goal: str = Field(max_length=8000)
    agents: list[AgentSpecDTO] = Field(default_factory=list, max_length=64)
    agent_refs: list[AgentDefinitionRefDTO] = Field(
        default_factory=list,
        max_length=64,
        description=(
            "The roster as references to stored agent definitions. Supply "
            "either this or `agents`, never both: mixing them is refused."
        ),
    )
    provider: ProviderKind | None = None
    model: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=190)
    identity_bot_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "The Bot whose AgentPass identity this run's Bot agents run under. "
            "Required when any agent is bot-kind: a submit that omits it is "
            "refused with 400, and one naming a Bot the caller does not own, or "
            "that holds no issued passport, with 422. An llm-only run needs none "
            "and any value supplied is ignored."
        ),
    )
    options: RunOptionsDTO | None = None

    @model_validator(mode="after")
    def _exactly_one_roster_form(self) -> "SubmitRunDTO":
        # Neither form is a malformed request; both is ambiguous rather than a
        # merge, so it is refused instead of guessing which one wins.
        if self.agents and self.agent_refs:
            raise ValueError(
                "supply either 'agents' or 'agent_refs', not both"
            )
        if not self.agents and not self.agent_refs:
            raise ValueError("a submit needs 'agents' or 'agent_refs'")
        return self


class SubmitRunResponseDTO(BaseModel):
    run_id: str


class RunSummaryDTO(BaseModel):
    run_id: str
    row_id: int = 0
    """The stored row key, shown next to ``run_id`` in listings.

    Defaulted so an older payload (or a run built without a row) still parses;
    it is not an identity, only the submission order made visible.
    """
    status: str
    termination_reason: str | None = None
    final_answer: Any = None
    error: str | None = None
    started_at: int | None = None
    ended_at: int | None = None
    goal_excerpt: str = ""
    """The goal's prefix, for telling one run from another in a list.

    `goal` is up to 8000 characters; a full page of them would be the whole
    prompt each. The full text stays on ``GET /runs/{id}/detail``.

    Defaulted rather than required because an empty goal is a legitimate state:
    a run with no goal yields an empty excerpt, and a reader must be able to tell
    that apart from a missing field. Declaring it required would also publish a
    backward-incompatible change for a field that is purely additive.
    """


class RunListDTO(BaseModel):
    items: list[RunSummaryDTO]
    total: int
    page: int
    page_size: int


class AgentResultDetailDTO(BaseModel):
    payload: Any | None = None
    artifact_key: str | None = None
    routed_at: int


class BotAuthorizationDTO(BaseModel):
    """Pending human-authorization gate for one agent's bot (additive).

    Present on an agent exactly while its bot sits at the human gate
    (``state=AWAITING_AUTHORIZATION``): ``iframe_url``/``redirect_url``
    are the tcauthmng consent surfaces, ``deadline`` the window horizon as
    the ISO-8601 spelling the creation-job payload itself carries —
    empty strings when the payload cannot answer a fact.
    ``application_id`` is the passport application's identifier so a
    consumer can address the decision surface from this read alone.
    """
    state: str
    iframe_url: str
    redirect_url: str
    deadline: str
    application_id: str


class AgentAttemptDetailDTO(BaseModel):
    claim_epoch: int
    slice_retry_index: int
    executor_kind: str
    executor_id: str
    executor_name: str
    binding_state: str
    result_status: str | None = None
    result_message: str | None = None
    started_at: int | None = None


class AgentMessageDetailDTO(BaseModel):
    message_id: str
    agent_id: str
    seq: int
    role: str
    content: Any | None = None
    decision: Any | None = None
    token_usage: Any | None = None
    artifact_key: str | None = None
    created_at: int


class AgentCatalogSpecDTO(BaseModel):
    name: str
    role: str | None = None
    kind: str
    model: str | None = None
    engine: str | None = None
    definition_id: str | None = Field(
        default=None,
        description=(
            "The stored definition this entry came from; null for an inline "
            "roster. Reported per catalog entry, not per spawned agent: every "
            "spawn of one entry shares the same definition version."
        ),
    )
    definition_row_id: int | None = Field(
        default=None,
        description="The exact definition version (row id) captured at submit.",
    )


class AgentDetailDTO(BaseModel):
    agent_id: str
    name: str
    catalog_name: str | None = Field(
        default=None,
        description=(
            "The catalog entry this spawned agent came from. A spawned agent is "
            "named `{catalog_name}_{agent_id[:12]}`, so several agents can share "
            "one catalog entry — and therefore one definition version."
        ),
    )
    role: str | None = None
    kind: str
    status: str
    parent_agent_id: str | None = None
    depth: int
    spawn_round: int
    idempotency_key: str
    attempts: int
    bot_id: str | None = None
    bot_state: str | None = None
    error: str | None = None
    output: Any | None = None
    started_at: int | None = None
    ended_at: int | None = None
    result: AgentResultDetailDTO | None = None
    attempt_ledger: list[AgentAttemptDetailDTO] = Field(default_factory=list)
    messages: list[AgentMessageDetailDTO] = Field(default_factory=list)
    authorization: BotAuthorizationDTO | None = None


class RoundDetailDTO(BaseModel):
    round_id: str
    stage_index: int
    orchestrator_steps: int
    slowest_attempt_steps: int
    cohort_agent_ids: list[str]
    started_at: int
    ended_at: int | None = None


class GuardEventDetailDTO(BaseModel):
    event_id: str
    agent_id: str | None = None
    kind: str
    details: Any | None = None
    created_at: int


class RunDetailDTO(RunSummaryDTO):
    tenant: str
    goal: str
    provider: str
    agent_catalog: list[AgentCatalogSpecDTO] = Field(default_factory=list)
    agents: list[AgentDetailDTO] = Field(default_factory=list)
    rounds: list[RoundDetailDTO] = Field(default_factory=list)
    guards: list[GuardEventDetailDTO] = Field(default_factory=list)


class CancelResponseDTO(BaseModel):
    run_id: str
    status: str


class StreamEventDTO(BaseModel):
    event: str
    run_id: str
    data: dict[str, Any] = {}


class PageDTO(BaseModel):
    page: int = 1
    page_size: int = 20
    status: str | None = None

class CreateDefinitionDTO(BaseModel):
    """Create an agent definition for the authenticated caller."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    role: str = Field(default="", max_length=20000)
    instructions: str = Field(default="", max_length=20000)
    # Required, not defaulted. A definition's kind decides how it is executed
    # (an llm agent answers in-process; a bot agent provisions a persona), so a
    # caller that forgets it would silently get the other one — llm agents have
    # no persona, no engine and no manifest, and the mistake only surfaces much
    # later as a run that behaves nothing like the request. Asking for it makes
    # an omitted kind a 422 at the call site instead.
    kind: AgentKind
    model: str | None = Field(default=None, max_length=128)
    engine: str | None = Field(default=None, max_length=64)
    bot_type: str | None = Field(default=None, max_length=32)
    manifest_document: str = Field(
        default="", max_length=200_000,
        description=(
            "Full five-category bot_config_manifest YAML, validated once here "
            "and stored verbatim. Blank means \"no manifest\"."
        ),
    )


class UpdateDefinitionDTO(CreateDefinitionDTO):
    """Revise a definition: writes a new version under the same id."""

    definition_id: str = Field(min_length=1, max_length=128)
    model_config = ConfigDict(extra="forbid",)

    definition_row_id: int = Field(
        gt=0,
        description=(
            "The version you read (every read returns it). A stale value is "
            "refused with 409 and no version is created; the field exists so "
            "that refusal can name the version that moved on, not to detect the "
            "conflict itself. There is no revision counter — a version is a "
            "row, so this is its id."
        ),
    )


class DefinitionDTO(BaseModel):
    definition_id: str
    name: str
    role: str
    instructions: str
    kind: str
    model: str | None = None
    engine: str | None = None
    bot_type: str
    manifest_document: str
    manifest_digest: str
    owner_id: str
    creator_id: str
    modifier_id: str
    definition_row_id: int
    created_at: int


class DefinitionVersionDTO(BaseModel):
    """One version in a definition's history. Never carries a retired body."""

    definition_row_id: int
    is_live: bool
    manifest_digest: str
    creator_id: str
    modifier_id: str
    created_at: int


class DefinitionVersionsDTO(BaseModel):
    """A definition's whole history, plus whether the definition is deleted.

    ``deleted`` is stated rather than inferred. A deleted definition and a
    superseded version both retire a row the same way, so without this a caller
    would have to deduce "deleted" from every entry reporting ``is_live=false``
    — which is also what a definition whose only version was never edited looks
    like. The history itself is still returned either way: retired versions stay
    readable as history.
    """

    definition_id: str
    deleted: bool
    versions: list[DefinitionVersionDTO]


class DefinitionListDTO(BaseModel):
    items: list[DefinitionDTO]
    total: int
    page: int
    page_size: int


class DefinitionIdDTO(BaseModel):
    """The target of a POST-only definition read, version list, or delete."""

    model_config = ConfigDict(extra="forbid")

    definition_id: str = Field(min_length=1, max_length=128)


class QueryDefinitionsDTO(BaseModel):
    """Filter/paging for the definition listing (POST, so no query string)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, description="narrow by name")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
