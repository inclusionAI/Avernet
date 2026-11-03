from __future__ import annotations

from typing import Protocol, runtime_checkable

# ``AgentId`` / ``AgentSpec`` / ``Run`` / ``SubagentResult`` / ``TaskEnvelope``
# (core.agentswarm.domain.models) use postponed annotations below —
# plugin_api/ must not import core/ (layer rule), so no
# ``from agentclaw.community.core... import`` lives here.


class ParkedAwaitingAuthorization(RuntimeError):
    """``setup()`` parked its persona at a human authorization gate.

    The subagent raises this — instead of polling its ready budget away —
    when the provisioned identity reports it is awaiting a human
    authorization that only that human can complete. It is a wait state, an outcome
    distinct from both progress and failure, so the runtime treats it
    accordingly:

    - no failed-attempt increment, no timeout journal entry, and no new
      attempt idempotency key or replacement identity — the parked persona
      is the one the authorization is pending for;
    - parked time never counts against the setup clock, so the park
      may legitimately outlast ``setup_timeout``;
    - the pool rechecks the gate by re-invoking the **same** subagent's
      ``setup()`` as a cheap status continuation: an implementation that
      parks MUST therefore be re-entrant — keep the minted identity, skip
      the create leg, and continue the provisioning poll — so the same
      setup continuation finishes with the same identity once the
      authorization resolves. Authorization transitions then decide the
      outcome: issuance resumes toward READY, and the rejection /
      window-elapse states surface as terminal provisioning errors.

    The message carries the human-readable facts (bot id, window
    deadline); approval itself happens only on the official consent page,
    never through this seam.
    """

    def __init__(self, message: str, *, bot_id: str | None = None) -> None:
        super().__init__(message)
        # ``None`` normalizes to the pre-attribute empty spelling so a
        # marker double carrying no identity keeps working and the runtime
        # reads a plain ``str`` (never branches on the type).
        self.bot_id = bot_id if bot_id is not None else ""


@runtime_checkable
class Subagent(Protocol):
    name: str
    # Additive, non-abstract executor surfaces (per-attempt persona
    # identity, D-J): attribute reads are optional getattr by the caller
    # and empty-string by convention until setup() lands the identity —
    # bot-kind exposes the minted platform persona (ac_-prefixed id,
    # persona name), llm-kind the bare per-attempt digest with no
    # platform binding. The pool reads them defensively so doubles that
    # pre-date the surface keep working unmodified (they simply journal
    # nothing); any producer that does pass runtime_checkable isinstance
    # checks is expected to carry the attributes from this change on.
    executor_id: str
    executor_name: str

    async def setup(self) -> None: ...
    async def run_task(self, envelope: TaskEnvelope) -> SubagentResult: ...  # noqa: F821
    async def teardown(self) -> None: ...
    async def halt(self) -> None: ...

@runtime_checkable
class SubagentFactory(Protocol):

    def create(self, spec: AgentSpec, run: Run,  # noqa: F821
               agent_id: AgentId, attempt: int = 1) -> Subagent:  # noqa: F821
        """Build one subagent for a spawned agent row.

        ``agent_id`` is the spawn\'s durable identity: stable across attempts
        AND across D16 respawns of an interrupted agent, and unique between
        two same-named spawns. Subagents that provision remote identities
        (the bot platform kind) derive their platform idempotency key from
        it — the name alone collides: two spawns of one catalog persona
        would share a single remote identity, and one\'s teardown would
        delete the other\'s.

        ``attempt`` (defaulted, D-J) is the spawn\'s in-slice retry index:
        the persona idempotency key is
        ``f"{run_id}:{agent_id}:{attempt}"``, so a same-slice retry mints
        a fresh persona and a cross-slice replay re-derives the crashed
        slice\'s. Out-of-swarm callers keep the default and the
        attempt-less identity becomes the attempt-1 identity.
        """
        ...
