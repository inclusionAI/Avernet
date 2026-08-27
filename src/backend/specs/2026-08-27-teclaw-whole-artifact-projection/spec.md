# One Whole-Artifact Delivery per Projection for Teclaw Bots

## Summary

`BotRuntimeProjector.project` splits one projection into two independently
scoped halves — Skills and MCP/CLI — and writes only the halves the mutation
declared. That split was introduced by
`specs/2026-08-26-mcp-sync-and-passport-regressions` (problem 4) and is
correct for **per-domain** runtimes, where the two halves reach the device
through genuinely different endpoints: `POST /api/skills/symlink/bindpath`
for Skills, `POST /api/mcp` plus `POST /api/mcp/filter-servers` for MCP.
Sending an unchanged half there costs a real, avoidable device round trip.

Teclaw is not a per-domain runtime. Every `DeviceSync` entry point on
`TeclawDeviceSyncService` — `sync_symlinks`, `sync_single_mcp`,
`sync_remove_mcp`, `sync_all_mcp_servers`, `sync_bot_config` — funnels into
the same private `_compose_and_deliver`, which recomposes the Bot's complete
`BotConfigArtifact` from the database and POSTs it to `/api/v1/bot/apply`.
The method arguments are discarded; the artifact is the payload every time.

Against that runtime the scope split does not select *what* gets written. It
only selects *how many byte-identical copies of the same artifact* get
delivered. A Skill add carrying two MCP dependencies currently issues **four**
full composes and four POSTs. A device-activation reconcile on a Bot with
eight MCPs issues **ten**, eight of them concurrently.

This change makes one projection mean one delivery on a whole-artifact
runtime, without touching the per-domain path.

## Background: what a teclaw delivery actually is

`core/devices/services/teclaw_device_sync.py` (landed in #1592, `6775f7e`)
is explicit about it. `sync_symlinks` carries the comment *"symlinks ignored:
teclaw re-pulls the whole composed artifact"*, and the MCP block is prefaced
with *"teclaw consumes the whole composed artifact, not a per-MCP delta, so
every MCP method re-composes and re-delivers the full artifact (same path as
`sync_symlinks`)"*.

One `_compose_and_deliver` call performs, in order:

1. `ConfigComposer.compose(ComposeRequest(...))` — which fans out through
   `ConfigComposerInputCollector` to `get_active_skills`,
   `collect_bot_active_mcps` + `build_mcp_sync_payload` per server,
   `ResourceService.list_resources`, `IdentityService`, and the channel
   overrides reader. Several DB reads and a per-MCP config merge.
2. `enrich_engine_ext(...)` — identity/stage decoration.
3. `BaasService.get_bind_id` + `BaasService.get_http_info` — a binding lookup
   and a gateway round trip to resolve the container URL and proxypass token.
4. `POST {info.http_url}` with the artifact as the body, 30s timeout.
5. `_record_draft_artifact` — a best-effort **write** of the delivered
   artifact onto the Bot's DRAFT publish row.

Every one of those five steps repeats per redundant delivery, including the
DB write in step 5.

The critical property for this spec is in step 1: the composer reads the
**database**, never the arguments passed to the `sync_*` method. The
collector calls `svc.get_active_skills(...)` and
`svc.collect_bot_active_mcps(...)` directly
(`core/config_compose/services/collector.py:150,194`). So the content of a
teclaw delivery is a pure function of persisted desired state at the moment
of the call — identical for `sync_symlinks` and `sync_all_mcp_servers`,
identical no matter which half the caller believed it was writing.

## Background: the plan is resolved before anything is written

`BotRuntimeProjector.project` resolves the whole plan up front, deliberately:

> Resolving and applying are separated by the `ProjectionScope`: the plan is
> always built whole — it is read-only, and every pre-flight failure in it
> must happen before anything is written — while the scope decides which
> halves are *written*.

`_build_plan` reaches desired state through
`BotCapabilityStateReader.active_skill_assets`, and every read on that reader
flushes SkillSet configuration into Installation first
(`core/skill_center/services/bot_capability_state_reader.py:64`). The
composer, when it runs later, re-enters the same reader.

The consequence is the one this change rests on: **by the time `_resolve_plan`
returns, persisted desired state is final and flushed, so a single delivery
issued after it necessarily carries the complete, current state of both
halves.** There is no ordering in which a second delivery could carry
something the first did not.

## Problem

### Problem 1 — a projection issues between two and N+2 identical deliveries

`project` runs the Skill half through `_apply_skill_projection`, whose teclaw
branch is one `service.sync_runtime(...)` → `sync_symlinks` → one delivery.
It then runs the MCP half through `_apply_non_skill_projection` →
`SkillSetService.sync_mcp_projection`, which is documented as *"One call, not
two"* but is two calls to the device layer:

- `sync_mcp_delivery(claimed, released)` — pushes each claimed MCP through
  `MCPSyncService.sync_mcp_details_for_bot`, which fans out one
  `plugin.sync_single_mcp` **per MCP** at concurrency 10
  (`core/mcp/services/sync_service.py:50,333`); and calls
  `remove_mcp_detail` → `plugin.sync_remove_mcp` once **per released** MCP.
- `sync_mcp_desired_state(declared)` — one `plugin.sync_all_mcp_servers`.

On teclaw each of those is a full compose + POST. Deliveries per projection:

| Mutation | Scope | Deliveries today | Wanted |
| --- | --- | --- | --- |
| Skill add, no MCP deps | `skills=True` | 1 | 1 |
| Skill add, 2 MCP deps | `skills=True, claimed={a,b}` | **4** | 1 |
| `activate_mcp` | `mcp=True, claimed={x}` | **2** | 1 |
| `deactivate_mcp` | `mcp=True, released={x}` | **2** | 1 |
| Skill-set activate, 5 members | `skills=True, mcp=True, claimed=5` | **7** | 1 |
| Device-activated reconcile, 8 MCPs | `everything()` | **10** | 1 |

The compensation path in `MutationProjectionFlow._project_or_compensate`
re-projects with `scope.inverted()` on failure, so a failed mutation pays the
same multiplier twice.

`sync_mcp_details_for_bot` fans out at concurrency 10, so the
device-activation row is up to eight *concurrent* composes of the same
artifact for one Bot, each ending in a POST and a DRAFT-row write.

### Problem 2 — per-MCP catalogue lookups that teclaw cannot use

Before delivering, `SkillSetService.sync_mcp_delivery` calls
`self.mcp_center.get_mcp_detail(server_code)` for every claimed code and
fails the whole projection when one returns empty
(`core/skill_center/services/skill_set_service.py:569-581`). The resulting
`entries` are handed to `sync_mcp_details_for_bot`, which hands each one to
`plugin.sync_single_mcp(mcp_data=...)` — and `TeclawDeviceSyncService`
ignores the argument. For a teclaw Bot these are N catalogue round trips
whose results are discarded, plus a failure mode that cannot affect what the
container ends up with.

### Problem 3 — the scope contract makes promises teclaw cannot keep

`ProjectionScope`'s documented guarantees are per-domain-shaped and are
simply false against a whole-artifact runtime:

- *"a single-MCP add stays a single device write"* — it is a whole-artifact
  write carrying every Skill too.
- `claim_all_mcp`: *"a freshly active container holds no MCP configuration,
  so there is nothing to refresh against"* — teclaw inlines every MCP's
  endpoint, `api_key` and headers into the artifact, so one delivery is
  already complete. The flag's reason for existing does not apply.
- `sync_mcp_projection`'s ordering invariant — *"configuration lands before
  the allow-list cites it, and is withdrawn only after the allow-list stops
  covering it"* — is vacuous when both ride in the same document.
- `project_mcp_and_cli`'s premise, *"a cutover task exclusively owns Skill
  mappings"*, cannot hold: any teclaw delivery republishes the Skill half by
  construction.

The behaviour should be stated once, in the projector, rather than left as an
invariant every future caller has to rediscover.

## Goals

1. One projection against a whole-artifact runtime issues exactly one
   artifact delivery, whatever the scope declares.
2. That delivery still carries both halves — it is issued after
   `_resolve_plan`, over flushed desired state.
3. Nothing changes for per-domain runtimes (`openclaw`, `claude_code`,
   `aicoding`, `hermes`): the scope split, the claimed/released guard, and
   the delivery/declaration ordering stay exactly as they are.
4. The Passport update keeps its current trigger and payload on every engine.
5. Each engine's runtime contract is **implemented by that engine**, behind a
   protocol the projector resolves from a registry. `BotRuntimeProjector`
   stops testing engine identity anywhere: no `engine == "teclaw"` string
   survives in it, and adding an engine whose runtime differs becomes a new
   implementation plus a registry entry, not an edit to shared code.

## Non-goals

- Changing what the teclaw artifact contains, or how `ConfigComposer`
  composes it.
- Changing `ProjectionScope`'s shape, or how callers populate it. Callers keep
  declaring what their mutation changed; only the projector's reading of that
  declaration becomes engine-aware.
- Teclaw Center-corpus (`center://`) delivery. Still refused, at the same two
  moments as today — plan resolution and delivery — but the refusal now lives
  in the teclaw implementation's `validate_plan` rather than in three
  scattered engine-string checks. Phase 2 concern.
- Per-engine variation beyond what exists. The registry ships exactly two
  implementations, which is what the code already has; it is not an invitation
  to split per-engine behaviour that is currently shared.
- Skills Pool mapping publication. Teclaw never takes that path —
  `CurrentRuntimeLayoutProbeService.probe_bot` returns
  `engine_has_no_filesystem_pool_layout` for teclaw
  (`core/skill_center/services/runtime_layout_probe.py:83`), so it can never
  reach `POOL_ACTIVE`.
- The two adjacent defects recorded under *Out of scope, reported* below.

## Acceptance criteria

1. **One delivery per projection.** For a Bot whose `active_engine` is
   `teclaw`, a single `project(...)` call reaches the runtime exactly once,
   for every `ProjectionScope` a production caller constructs: `skills` only,
   `mcp` only with `claimed_mcp`, `mcp` only with `released_mcp`, both halves,
   and `ProjectionScope.everything()`.
2. **The delivery is not conditional on the Skill flag.** An MCP-only scope
   (`skills=False, mcp=True`) still delivers exactly once on teclaw — today
   it reaches the runtime only through the MCP half.
3. **No per-MCP device traffic on teclaw.** `sync_mcp_projection`,
   `sync_mcp_delivery` and `sync_mcp_desired_state` are not called at all for
   a teclaw Bot, so no `get_mcp_detail` catalogue lookup is issued on their
   behalf either.
4. **The Passport is unchanged.** For teclaw, `update_passport` is called
   under exactly the conditions it is called today (`scope.mcp` true), with
   an identical `resource_scope` — `mcp_codes`, `mcp_items` carrying resolved
   `identity_mode`, and `cli_items`.
5. **Per-domain engines are untouched.** For a non-teclaw Bot, the number,
   order and arguments of every runtime and Passport call are byte-identical
   to current behaviour, including the claimed/released guard against the
   projected set and the skip-logging when a half is not declared.
6. **No engine identity test survives in the projector.** All four current
   `engine == "teclaw"` checks — `snapshot_skill_mappings:118` and
   `_build_plan:332` (the Center-corpus contract, at plan-resolution time) and
   `_apply_skill_projection:417,426` (delivery) — are gone, replaced by calls
   through the engine's own implementation of the projection protocol. A
   grep for `teclaw` in `bot_runtime_projector.py` returns nothing.

7. **Failure still fails closed.** A teclaw delivery that returns
   `{"success": False}` — compose error, missing `bind_id`, HTTP status or
   request error, all of which `TeclawDeviceSyncService` converts into a
   result dict rather than an exception — still raises
   `SkillSetRuntimeReconcileError`, so `MutationProjectionFlow` compensates
   exactly as it does today.
8. **An empty scope stays a no-op.** A scope declaring neither half and
   carrying no retired mappings delivers nothing on teclaw, as it does today.

## Out of scope, reported

Two defects found while establishing the above. Neither is caused by this
change, neither is fixed by it, and both survive it unchanged.

1. **Skill-declared MCP dependencies never reach a teclaw container.**
   `RuntimeProjectionResolver.resolve` folds
   `mcp_dependency_codes(asset.mcp_dependencies)` into
   `projection.mcp_server_codes` (`core/skill_center/runtime_resolver.py:85`),
   but the artifact's MCP list comes from `collect_bot_active_mcps`, whose
   union is *default policy ∪ installed* with no dependency term. A Skill's
   `mcp_dependencies` are therefore absent from the artifact today. This
   change neither helps nor hurts: the artifact already ignores
   `projection.mcp_server_codes` on both the current and the proposed path.

2. **`strict_policy_context` diverges between the projector and the composer.**
   The projector calls `collect_bot_active_mcps(strict_policy_context=True)`
   so that a failed policy-context lookup raises rather than being read as an
   empty Default policy — explicitly, *"a transient dependency outage cannot
   remove template-only MCPs"*. The composer's collector calls the same method
   without the flag, so the recompose can fail **open** on the same outage and
   deliver an artifact missing template-only MCPs. On teclaw the composer is
   the one that decides the payload, so the projector's fail-closed guard does
   not protect the delivered content.

Both want their own spec.
