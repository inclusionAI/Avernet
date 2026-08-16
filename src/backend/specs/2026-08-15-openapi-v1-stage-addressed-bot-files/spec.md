# Public API — Stage-Addressed Per-Bot Files

## Summary

Five OpenAPI v1 operations read or write a file on a bot's device, and every one
of them can only reach the bot's **draft** workspace:

```text
GET  /openapi/v1/bots/{bot_id}/engine/config
PUT  /openapi/v1/bots/{bot_id}/engine/config
GET  /openapi/v1/bots/{bot_id}/identity
GET  /openapi/v1/bots/{bot_id}/identity/{file_type}
PUT  /openapi/v1/bots/{bot_id}/identity/{file_type}
```

Those are the **bot-first** addresses that
[#1074](https://github.com/inclusionAI/Avernet/pull/1074) establishes — its
Task 4 has already moved identity, and its Task 15 moves `engine-config` to
`/{bot_id}/engine/config` onto its own router. This feature lands after it and
targets those addresses only; the deprecated addresses #1074 leaves behind are
frozen and do not gain the parameter (see **Legacy addresses** below).

A service bot has up to three long-lived runtimes — draft, verify, online — and
the engine-runtime groups have addressed all three with `?stage=` since
2026-08-09. These five resolve `DeviceContextResolver.resolve_for_bot`, which
reads `ac_bots.binding_id`, so a published runtime is unreachable through them.

This adds `?stage=` to all five, with the same vocabulary, the same default and
the same refusals the connection API already publishes. The three reads serve
all three stages. The two writes take the parameter in order to **refuse** a
published one: a release is replaced by publishing again, never edited.

## Motivation

An operator who has published a service bot cannot answer the two questions
this surface exists to answer:

- *What configuration is my online release actually running?* The draft has
  moved on since the release was cut, so the draft's answer is not the release's.
- *Which identity files did verify receive?* The publish flow copies them into
  the released runtime; whether the copy is what the operator intended is only
  observable by reading the runtime.

Both are answerable today only through internal, `publish_id`-keyed routes that
the public surface does not expose and that an external tenant has no id for.

The write side has a sharper problem than "not supported". `PUT
…/engine/config` accepts a request that names no stage and writes the draft —
which is correct. Once `stage` exists on the read, a caller will send it on the
write, and the two dishonest answers are exactly the ones a surface must not
give: writing the published runtime forks it from the record that describes it,
and quietly writing the draft instead reports success for an edit the addressed
runtime never received.

There is also a latent correctness problem this change must not spread. Two
stage-selection rules exist in the codebase, and engine-config is on the weaker
one — see **Decisions**.

## User Stories

- As an operator debugging a released service bot, I read the engine
  configuration my **online** runtime is running, without needing a publish id.
- As an operator validating a release, I read the identity files on the
  **verify** runtime and compare them with the draft I authored.
- As an operator, when I try to edit a published runtime I am **refused with an
  error**, not told the write succeeded.
- As an owner of a personal bot, every one of these calls behaves exactly as it
  did before this change, because I never name a stage.
- As an integrator, the stage parameter means the same thing, takes the same
  values and fails the same way on these five operations as on the sixteen
  engine-runtime ones.

## Acceptance Criteria

### Which operations take the parameter

- `stage` is an **optional query parameter** on exactly the five operations
  listed in the Summary, and on the sixteen engine-runtime operations that
  already have it. Nowhere else.
- It is never a body field and never a path segment.
- Its values are the existing `RuntimeStage` enum — `draft`, `verify`, `online`.
  A value outside the enum is a `422` from the adapter, before any handler runs.

### Reads

- The default is `draft`. A request that names no stage is byte-for-byte the
  request that exists today: same device resolution, same query count, same
  errors.
- `stage=verify` and `stage=online` on a `service` bot resolve that stage's live
  runtime and read from it.
- Liveness is the rule in `core/engine_runtime/stage.py`, unchanged and not
  re-implemented: `online` while the newest publish record is at `SUCCESS`;
  `verify` while a record is at `VALIDATING`, or through the promoted record's
  **retained** verify binding while that binding is still `ACTIVE`.
- No fallback between stages, and none to the draft. A verify request is never
  answered by the online runtime, or by the workspace.
- `GET /openapi/v1/bots/{bot_id}/identity` reports every file from the **one**
  addressed runtime. A caller never sees a draft row beside a verify row.

### Legacy addresses

- The five retiring twins of the operations above — `…/bots/identity/{bot_id}`,
  `…/bots/identity/{bot_id}/{file_type}`, `…/bots/{bot_id}/engine-config` —
  **do not gain `stage`.** #1074 freezes their contract byte for byte: same
  parameters, same locations, same schemas, until the 2027-08-15 sunset. Adding
  one there would break the freeze, and it would also be pointless — a caller
  who wants a published runtime is a caller who can move to the new address.
- **This is not a claim about every deprecated address.** The retiring
  engine-runtime addresses *do* carry `stage`, because they always did: they are
  the same operations at a former address, mounted with the same response table
  and the same parameters, and #1074's parity promise is exactly that they keep
  it. The rule is "a retiring address keeps the contract it had", which for
  those means keeping `stage` and for these five means never acquiring it.
- A caller on a legacy address therefore keeps reading the draft, which is
  exactly what that address does today. Nothing regresses; the new capability
  simply lives only on the new addresses.
- A caller who sends `?stage=online` to a legacy address anyway reads the draft:
  FastAPI ignores an undeclared query parameter. That is the freeze behaving as
  specified rather than a silent substitution of the kind the writes refuse —
  the address never advertised the parameter, and the document is what a client
  is generated from.

### Writes

- `PUT …/engine/config` and `PUT …/{bot_id}/identity/{file_type}` accept
  `stage`, default `draft`, and write the draft.
- `stage=verify` or `stage=online` is refused with `409` and the fixed message
  **"The requested stage is read-only"**.
- The refusal is unconditional on bot type and on liveness. A published stage
  named on a personal bot, or a stage with no runtime up, is still this refusal
  on a write — publishing a runtime there would not make the write land.
- **Nothing is written anywhere.** The refusal is raised in core before the
  device is resolved, and the draft is not touched as a substitute.
- A `200` carrying a "no-op" flag is explicitly rejected as the answer.
  Automation that checks the status code would record the write as landed.

### Refusals and disclosure

- A published stage named on a `personal` bot, or a stage with no live runtime,
  is the existing `409` **"No live runtime at the requested stage"**
  (`EngineStageNotLiveError`). That mapping already exists; no second mapping is
  added for it.
- The read-only refusal is a **distinct** error and a distinct message from the
  not-live one. Merging them would tell a caller to publish a runtime and retry,
  which would not help.
- Neither refusal reveals anything a caller could not already learn: both run
  after the existing ownership guard, which still answers a bot that is not the
  caller's with the masked `404`.

### Published document

- Both descriptions are published: the read's existing `STAGE_DESCRIPTION`, and
  a write-specific one that states the draft is the only writable runtime.
  Publishing the read's text on a write would advertise verify/online as
  addressable there.
- `409` is already documented surface-wide; no per-route response table changes.

## In Scope

- `?stage=` on the five operations above.
- One new core error for the write refusal, mapped once in `ENVELOPE_ERRORS`.
- One shared core helper that turns a **published** stage into a device
  context, so the file surfaces and the forwarding surfaces cannot drift on
  which runtime a stage names. The draft stays the `resolve_for_bot` call it
  already is.
- A recorded decision on the two divergent stage-selection rules, and a comment
  at each call site that keeps them apart.
- Doc update in `docs/openapi-v1/README.md`.

## Out of Scope

Each of these was checked against the callers of
`DeviceContextResolver.resolve_for_bot`, not assumed. The audit covers all 31
call sites; an earlier pass of it was truncated and missed MCP, which is why
that group is enumerated below rather than absent.

- **The startup-script operations** (`GET`/`PUT`/`DELETE
  …/{bot_id}/startup-script`). Named as affected in the original request; they
  are **not**. `BotStartupScriptService` is backed by the
  `ac_bot_startup_script` table keyed `(env, entity_id, bot_id)` and never
  resolves a device context. There is one row per bot, not one per runtime, so a
  `stage` parameter there would be inert — three stages returning the same row —
  and inert parameters are worse than absent ones.
- **The resources group** (7 handlers). A genuine per-bot device-file surface
  with the same gap. It is `definition-only / NOT PUBLIC-READY` (its own router
  says so; `require_principal` is still a stub), and `ResourceFileService`
  already carries a `publish_id`-shaped stage path that would have to be
  reconciled with a `stage` parameter first — the Open Question below. Filed as
  follow-up, not folded in. (#1074 re-addresses it to
  `/openapi/v1/bots/{bot_id}/resources`; that is an address change, not a
  runtime one.)
- **The skills group** (6 handlers). Same gap, release-pending. Its
  `owner_entity_id` locator — the reason this group could not be stage-addressed
  on the same terms as the others — is fixed by #1074's Task 14, which renames it
  to `owner_id`, so that blocker is gone by the time this lands. What remains is
  scope: six operations whose device interaction is skill activation and
  package upload, not a file read, and which are pending their own release
  runbook. Deferred on those grounds rather than on the locator's.
- **Routines.** `CronRelayService` resolves `resolve_for_bot`, but a routine is
  not a per-bot file and its stage pin is already filed as
  [#908](https://github.com/inclusionAI/Avernet/issues/908).
- **The MCP group** (6 operations) — out for a different reason from the rest,
  and the reason is worth stating because the group *does* reach devices.

  **None of the six addresses a bot.** They are keyed by `server_code` and
  `user_id`: `/openapi/v1/bots/mcp/servers`, `/tenants`,
  `/servers/{server_code}`, `…/permissions`, and `GET`/`PUT …/config`. #1074
  keeps all six at their current addresses for exactly that reason — a bot id
  would have nothing to name.

  **But `PUT …/servers/{server_code}/config` is not bot-free in effect.**
  `write_unified_config` → `MCPSyncService.sync_mcp_detail_to_all_bots` lists
  every bot under the identity and calls `resolve_for_bot(bot_id, entity_id)`
  for each, pushing the config to that bot's device. One account-level write
  fans out to N bots.

  A `stage` parameter still does not belong there, and not merely because the
  group is out of scope. The operation names no single bot, so there is no one
  runtime for a stage to name; and what a published stage would ask for is a
  fan-out write to published runtimes, which is precisely what this feature's
  write rule refuses.

  **The fan-out reaches drafts only, and that is correct rather than a gap** —
  by the same rule, arrived at independently. A release inlines its MCP
  credentials into the artifact (`sync_service.py`: "凭据已内联进产物，改
  api_key 即改产物字节"), so a published runtime's MCP config changes by
  republishing, not by being written. That is this spec's D1 reasoning holding
  on a surface that never consulted it.
- **Internal `/api/...` routes.** They stay draft-only, explicitly, at their
  call sites.
- **Any change to what a published runtime contains.** This surface reads it; the
  publish flow writes it.

## Decisions

**D1 — The write refusal is a new error, not a reuse of an existing 409.**
Three 409s now live side by side and each answers a different question:
`BotOperationNotAllowedError` ("this bot cannot do this at all"),
`EngineStageNotLiveError` ("that runtime is not up"), and the new one ("that
runtime does not take writes"). The second is the tempting reuse and the wrong
one: it implies a retry after publishing.

**D2 — On a write, the read-only refusal wins over the not-live refusal.**
`PUT …?stage=online` against a personal bot answers "read-only", not "no live
runtime". The refusal is a property of the *operation*, true whatever the bot
and whatever is running; answering "no live runtime" would suggest that
publishing one would make the write work. It is also what lets the refusal run
before any lookup at all.

**D3 — `read_publish_config` stays on `select_stage_bind_id`.** This is the
trap the request flagged, and the answer is that the two rules are not
duplicates — they answer different questions:

| Question | Rule | Callers |
| --- | --- | --- |
| A **stage** was named — which runtime does it have? | `engine_runtime/stage.py::resolve_stage_bind_id` | the relay, the connection socket, and (new) the stage-addressed file reads |
| A **publish record** was named — which binding is its runtime? | `service_bot/repository/models.py::select_stage_bind_id` | `EngineConfigService.read_publish_config`, `IdentityService._read_from_publish_device`, `ResourceFileService._stage_bind_id` |

`GET /api/service-bot/publish/{publish_id}/engine-config` is keyed by
`publish_id`. `resolve_stage_bind_id` selects the *newest* record at a status
and ignores which record the caller named, so collapsing that route onto it
would answer a question about release 7 from whichever release is currently
newest — a regression, not a consolidation. The drift `stage.py`'s docstring
warns about ("two surfaces disagreeing on whether a runtime exists") is between
two callers answering the *same* question two ways, which after this change no
longer happens: every stage-keyed caller goes through `resolve_stage_bind_id`,
every record-keyed caller through `select_stage_bind_id`, and the split is
stated in both modules and at each call site.

**D4 — A write never resolves the bot facts a published stage would need.** The
write path takes only the stage name, refuses anything but the draft, and then
resolves the draft through the same owner-scoped call it always used. It never
reaches the branch that reads a bot row or a publish record, so there is no code
path from a write to a published runtime's binding — the guarantee is
structural, not a promise kept by a guard.

**D5 — `stage` is imported from `engine_runtime/params.py`, not re-declared.**
A second spelling of the same parameter is a second thing to keep in step. The
module keeps the vocabulary; the group does not own the parameter.

**D6 — The bot primary key always comes from an owner-scoped read.**
`resolve_stage_bind_id` is keyed on `ac_bots.id` because `bot_id` carries no
unique constraint and every user's first bot is called `default`, so a lookup by
`bot_id` alone can select another owner's row and hand back their running
device. The invariant is therefore about *which query* produced the key, not
about how few queries ran: it must come from a `(bot_id, owner_id)` read. Under
**D8** that read happens inside the service, on the published branch only — the
same ownership check the adapter performs, run again rather than threaded down.
A `bot_id`-only lookup is never acceptable, here or anywhere.

**D8 — One bot-identity model: `BotFacts`, with `stage` beside it as a `str`.**
This is the shape every stage-aware path in the codebase already uses — the
relay's `_resolve_device(bot_id, owner_id, facts, stage)` and
`_resolve_published_device(facts, owner_id, stage)`, and
`EngineRuntimeRelayProtocol.call(..., facts, stage)`. These services adopt it
rather than introducing a second projection of the same concept.

An earlier draft of this spec proposed a `StageAddress` value object carrying
`(stage, bot_pk, bot_type)`. It was rejected on review: those are a subset of
`BotFacts` fused with the stage, so it would have put two overlapping
bot-identity models next to each other for the sake of two services.

The consequence is that a published-stage request does one owner-scoped row read
inside the service instead of reusing the record the adapter already fetched.
That is the accepted cost. It buys back more than it spends: the adapters pass a
single `stage=` string and nothing else, the identity router needs no bot
repository and no conditional lookup, and no parameter has to be threaded
through four service layers. The draft path — every request that names no stage
— reads no extra row at all.

**D7 — This lands after #1074, on the bot-first addresses only.** Two
consequences, both deliberate:

- The five addresses in the Summary are the post-#1074 ones. The engine-config
  pair in particular moves house — #1074's Task 15 takes the two handlers out of
  `openapi_v1/bots/router.py` onto their own router mounted at
  `/openapi/v1/bots/{bot_id}/engine`, with the ordinary error table rather than
  the engine-runtime one. This feature adds the parameter wherever those
  handlers then live; it does not move them itself.
- The deprecated addresses do not get the parameter, per **Legacy addresses**
  above. Sequencing this before #1074 would mean adding `stage` to an address
  about to be deprecated and then adding it again to its replacement, and the
  deprecation freeze would make the first of those a contract violation.

## Open Questions

None blocking.

**Resolved — the `_is_engine_runtime` collision.** Raised here while #1074 was
still specification: its segment predicate would have matched the moved
`/openapi/v1/bots/{bot_id}/engine/config` and asserted `owner_id` on an
operation that publishes `user_id`. #1074 settled it by dropping the segment
test for a **path-set membership** test — `_engine_runtime_paths()` collects the
routes off `_ENGINE_RUNTIME_GROUPS` and the deprecated package's
`ENGINE_RUNTIME_GROUPS`, so the classification comes from what is mounted rather
than from what a path looks like. Task D4 builds on that shape: the five
operations here are simply not in that set, so they belong in
`_STAGE_ADDRESSED_ELSEWHERE`.

**Noted for the follow-up work:** when resources is stage-addressed,
`ResourceFileService`'s `publish_id` parameter and a new `stage` parameter would
coexist on the same service, and the precedence between them should be settled
the same way `IdentityService.get_bot_file` settles it here — record-keyed wins,
as the more specific address.
