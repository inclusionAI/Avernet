# AgentSwarm: one identity per run

How a swarm run is authorized, how its personas run, and why the shape is what it
is. Written for whoever next changes this code, because three of the decisions below
contradict the model this module used to have.

## The shape

```
  ac_bots                          ONE row per run
    bot_id = agent_swarm_<hash>
    ext.passport = { agent_code, status: ISSUED }
        │
        │ 1:N
        ▼
  ac_entity_device_binding         one row per persona
    device_id      = BOT-...       the platform device; ws-info resolves it
    applied_by     = <worker id>   which instance this is
    apply_reason   = agentswarm_worker:<swarm bot id>
    status         = ACTIVE        a worker is only bound once its container is up
        │
        ▼
  platform container               boots, then receives its persona
```

A run applies for **one** AgentPass. Personas inherit it; none of them apply.

## Five decisions worth knowing

**1. One identity per run, one application.**
The first persona of a run establishes the identity — one `apply_agent_passport`, one
`ac_bots` record. Every later persona inherits it. The run's identity is what the
swarm exists to hold; personas are workers under it.

**2. The scope is the union of what personas declare.**
Because one identity serves all of them, its resource scope is the union of every
persona's MCP and CLI resources. The resource-scope write is a full replacement, so
it is re-submitted only when a persona actually adds a resource — never on a timer,
never unconditionally. The consequence to accept knowingly: **every worker can use
every tool any persona declared.** Per-persona least privilege is given up by this
design and is not promised later.

**3. A worker is a device binding, not a bot record.**
This is the correction that took the longest to find. A worker has **no** `ac_bots`
row: it is an `ac_entity_device_binding` under the run's bot, exactly as a caller
instance is a device under its service bot. Production runs this shape at scale — one
`ac_bots` row serving eleven bindings across eight `applied_by` values.

*Why it matters operationally:* `bot_status` and the chat path must read the
**binding**, not a bot row. An earlier version of this code read `ac_bots.device_id`
and failed with "bot not found for chat" for every worker, because there was no row
to find.

*Why the worker id is the discriminator:* status, chat and delete all receive only
the id `create_bot` returned. Keying the binding on anything else would mean
inverting that id back into its run — a guess. So `applied_by` is the worker id and
`entity_id` stays the run's owner.

**4. Persona content is stored, then delivered.**
The container platform does not materialize files, so a persona cannot ride along
with `create_bot`. Content is stored in the content-addressed store **before** the
container is requested (a persona whose content cannot be stored must not get a
container that boots blank) and written into the container **after** it is up,
through the identity port. Storing is plural by construction — blobs are addressed by
digest — which is why the content store can carry N personas where a bot's
configuration manifest could not: a manifest is one document per bot, and a second
persona's document overwrites the first.

**5. Teardown is scoped to the run, not the worker.**
A worker's teardown releases its container and never touches the run's identity.
Destroying an identity per worker would destroy it for every sibling at the first
teardown.

The identity is released at the run's **terminal point** — `BotPlatform.end_run`,
called from the run-terminal reconciliation once the workers are gone. That covers
completion, cancellation, stuck reclaim and forced failure.

It is not enough on its own. A run whose terminal write never lands (a worker killed
mid-slice, a finalizer fenced out by a newer claim) keeps its identity forever, so an
age-based sweeper releases identities still `ISSUED` past a stale cutoff. The road
releases promptly; the belt catches what the road missed.

Release is idempotent across processes — the decision comes from the stored row, not
from memory — and a failed release leaves the identity retryable rather than recorded
as done.

## Why this differs from what the module used to do

Before this change, each persona attempt created its own bot through
`submit_bot_creation_with_manifest`, which applied for its own AgentPass. A run of N
personas produced N bot records, N applications, up to N consent gates, and N
teardowns.

Production data shows what that costs: 72 worker bots and 67 active for a single
owner. That is the population this change is meant to stop growing.

## What a run never does

A run never waits for a human decision. Its identity is applied once, synchronously,
by its first persona; there is no asynchronous approval in the flow, so there is no
gate to park at. `AWAITING_AUTHORIZATION` is unreachable, and the adapter neither
raises the park marker nor branches on the gate state.

## Known gaps

- **The roster is discovered, not declared.** The seam that reaches this adapter
  carries one persona per call, so the union grows as personas arrive and the scope
  is not final at first application. A late persona that adds a resource causes a
  second application. Making the roster static means changing the seam.

  This has a second consequence worth naming: **validation is per persona, not per
  roster.** A persona is validated and stored before *its own* container is
  requested, so no worker ever boots blank — but a run cannot report "every invalid
  persona at once", because it does not know them all when it starts. The spec was
  written down to match this rather than promising batch validation the architecture
  cannot deliver.
- **A failed persona write fails that persona**, and the siblings proceed — but the
  failure is not yet reported *alongside* them as a per-persona result.
- **Persona delivery cannot reach the platform yet.** `IdentityService` resolves a
  worker's device from an `ac_bots` row, and a worker has none, so delivery raises
  `DeviceNotBoundError` against the real service. The delivery code and its tests are
  correct up to that boundary; the boundary itself is the open decision (give workers
  a bot row, or write through a path that does not need one).

- **Personas carry identity content only.** A persona that needed skills or tool
  configuration would need a delivery path that reads the bot record, which this
  design does not have.

- **The abandoned-identity sweeper has no caller.** It is built, bounded and tested,
  but this repository ships no scheduler — the artifact retention sweeper is in the
  same state. Until one exists, a run whose terminal write never lands keeps its
  identity and its bot row.