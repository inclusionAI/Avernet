# SkillSet Projection Latency

## Summary

Activating or deactivating a SkillSet currently takes about five seconds. Roughly
two thirds of that is work the request did not need to do: the same device
address is resolved three times, the same mapping payload is sent to the device
twice, the same installation flush runs four times, and both the MCP allow-list
and the Passport manifest are rewritten with content identical to what was
already there. This feature removes that redundant work so the operation costs
what it actually is — one device write — without changing what the runtime ends
up holding.

## Motivation

A production trace of one `POST /skillset/deactivate` (staff 272471, bot
`default`, engine `openclaw`, ARCA sandbox) on 2026-08-27 took **4.98 s**
wall-clock:

| Span | Δ | What |
|---|---|---|
| 35.858 → 36.835 | 0.98 s | request context, permission interceptor, path params |
| 36.835 → 37.961 | 1.13 s | plan build: 2× `SkillService` construction, `get_set_mcp_servers`, `collect_bot_active_mcps` |
| 37.961 → 38.001 | 0.04 s | `queryAgentPassport` (Default CLI list) |
| 38.001 → 38.326 | 0.33 s | **device address resolution #1** — 4× `get_by_id` on one row |
| 38.326 → 39.209 | 0.88 s | `POST /api/skills/layout/mappings/publish` |
| 39.209 → 39.446 | 0.24 s | **device address resolution #2** — same 4 reads again |
| 39.446 → 40.143 | 0.70 s | `POST /api/skills/layout/mappings/verify` |
| 40.143 → 40.412 | 0.27 s | **device address resolution #3** — same 4 reads again |
| 40.412 → 40.593 | 0.18 s | `POST /api/mcp/filter-servers` |
| 40.593 → 40.791 | 0.20 s | `updatePassport` |
| 40.791 → 40.835 | 0.04 s | audit insert |

Three device round trips account for 1.76 s (35%). Re-resolving the same device
address three times accounts for 0.84 s (17%) of pure duplicate database reads.
The trace shows no `[sync_mcp_delivery] pushing MCP configuration` line, meaning
the MCP half of this projection changed nothing at all — yet it still spent
0.38 s writing identical state back to the device and to the Passport service.

Deactivation is an interactive control in the workbench. Five seconds of
spinner for an operation whose only real side effect is one filesystem write on
the device is a bad enough experience that users retry, which starts a second
five-second projection.

> **Note on spec conventions.** SDD's `specify` phase normally forbids code and
> file paths. This spec carries both because the change is a pure internal
> latency fix with no user-visible behavior change — the problems *are* code
> shapes, and stating them in product language would lose the precision needed
> to judge whether each fix is safe. The user asked explicitly for per-problem
> code snippets here. Sections **P1**–**P5** are that material; the remaining
> sections keep the standard product-level shape.

---

## Scope of the current change

**P1 and P2 are implemented now.** P3, P4 and P5 are deferred and tracked as
their own issues; their analysis stays below because those issues work from it.

| Spec | Issue | Deferred because |
|---|---|---|
| P3 | inclusionAI/Avernet#1621 | Independent of P1/P2; touches the mutation flow and plan build rather than the device boundary. |
| P4 | inclusionAI/Avernet#1622 | Changes observable device-facing behavior and has a live question about drift repair. |
| P5 | inclusionAI/Avernet#1623 | Same — changes behavior, and `/skillset/sync` against a Default Set becomes a no-op. |

> **P4's premise moved under it.** `70061fa` ("preserve MCP config on set
> deactivation") landed on the base while this change was in review and
> replaced deactivate's `scope_from_result=… released_mcp=result.mcp_codes`
> with a plain `scope=ProjectionScope(skills=True, mcp=True)`. Deactivate
> therefore no longer releases any MCP code, so P4's guard — skip when
> `claimed` and `released` are both empty — would now fire on **every**
> deactivate rather than only those whose codes were still supplied
> elsewhere. That is a materially larger behavior change than #1622
> describes; re-read the analysis against the current code before acting on
> it. P5's finding is unaffected: `skills=True` is still hard-coded on both
> commands.

Acceptance criteria below are tagged **[now]** or **[deferred]** accordingly.

---

## Problems

### P1 — The device address is resolved from scratch for every device call, and each resolution reads the same binding row four times

**Issue.** Follow one projection down four levels.

*Level 1 — the projection makes two runtime calls.*

```python
# src/backend/.../skill_center/services/bot_runtime_projector.py:598 — _apply_pool_mappings
            published = await self._pool_runtime.publish_mappings(      # call A
                bot_id=bot_id, user_id=owner_id, mappings=mappings,
                retired_mappings=retired_mappings, source_layout=source_layout,
                mapping_contract_version=contract,
            )
            verified = published and await self._pool_runtime.verify_mappings(   # call B
                bot_id=bot_id, user_id=owner_id, mappings=mappings,
                retired_mappings=retired_mappings, source_layout=source_layout,
                mapping_contract_version=contract,
            )
```

*Level 2 — each of those issues its own `_invoke`, and a center-corpus publish
issues one more before it even starts.*

```python
# src/backend/.../core/skills_pool/runtime.py:149 — publish_mappings (call A)
        if not await self._ensure_center_mappings(...):     # ← itself _invokes, see :391
            return False
        try:
            response = await self._invoke(
                bot_id=bot_id, user_id=user_id,
                path="/api/skills/layout/mappings/publish", body={...},   # device call
            )
```

```python
# src/backend/.../core/skills_pool/runtime.py:322 — verify_mappings (call B)
        try:
            response = await self._invoke(
                bot_id=bot_id, user_id=user_id,
                path="/api/skills/layout/mappings/verify", body={...},    # device call
            )
```

```python
# src/backend/.../core/skills_pool/runtime.py:391 — _ensure_center_mappings
#   (nested inside publish_mappings above; when it fires it goes first)
        try:
            response = await self._invoke(
                bot_id=bot_id, user_id=user_id,
                path="/api/skills/center/ensure", body={"items": items},  # device call
            )
```

*Level 3 — `_invoke` resolves the device from scratch, then does the IO.* There
is no parameter to pass a context in and no memo; the resolve is unconditional
and happens once per call, immediately before the network write.

```python
# src/backend/.../core/skills_pool/runtime.py:409
    async def _invoke(
        self, *, bot_id: str, user_id: str, path: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        context = self._resolver.resolve_for_bot(bot_id, user_id)   # ← DB work, every call
        return await self._transport.invoke(                        # ← the IO
            context.conn_info, "POST", path, body=body, timeout=30.0,
        )
```

So one ordinary projection resolves the same `(bot_id, user_id)` **twice**, and a
center-corpus projection **three times** — matching the trace, which shows the
four-read resolution block repeated before each of `publish`, `verify`, and
`filter-servers`.

*Level 4 — what one resolution costs.* Roughly six queries. `DeviceContextResolver.resolve_for_bot`
loads the binding, then hands it to the builder — which throws the record away
and starts again from the id:

```python
# src/backend/src/agentclaw/community/core/devices/services/device_context_resolver.py:78
binding = self._binding_repository.get_active_by_bot_and_owner(bot_id, user_id)   # query 1
...
raw_conn_info = builder.build(binding, user_id, device_uuid=device_uuid)          # ← has the record
...
bot = self._bot_repository.get_by_id_and_owner(bot_id, user_id)                   # query 6
```

```python
# .../conn_info_builders/arca_builder.py:26
def build(self, binding: DeviceBindingRecord, user_id: str, *, device_uuid=None):
    return self._device_service.get_device_connection_v2(
        binding_id=binding.id,      # ← only the id survives; the record is discarded
        user_id=user_id,
        nick_name=user_id,
    )
```

`DeviceService` is bound to `DeviceServiceRouter` (a subclass), so both calls
inside `get_device_connection_v2` route, and routing itself is a database read:

```python
# src/backend/src/agentclaw/community/core/devices/services/device_service.py:1712
device_result = self.get_device(binding_id=binding_id)      # router → get_by_id (2), provider → get_by_id (3)
...
result = self.get_device_connection(binding_id=binding_id, operator=operator)
                                                            # router → get_by_id (4), provider → get_by_id (5)
```

```python
# src/backend/src/agentclaw/community/core/devices/services/device_service_router.py:179
def _get_provider_for_binding(self, binding_id: int) -> DeviceService:
    record = self._repo.get_by_id(binding_id)   # a full row read to learn one column
    ...
    return self._providers[record.device_provider]
```

That is exactly the four `[get_by_id] id=1381686` lines the trace shows per
device call, ~40 ms apart, three times over.

**How the fix should work.** Two independent reductions:

1. *Within one resolution*, thread the record that has already been loaded.
   `ArcaConnInfoBuilder` holds a `DeviceBindingRecord`; `get_device_connection_v2`
   should accept it and use `record.device_props` / `record.device_provider`
   directly instead of re-reading, and pass it through to
   `get_device_connection`. Four reads collapse to zero extra reads.
2. *Across the projection*, resolve once. One projection currently resolves two
   or three times for the same `(bot_id, user_id)`. `publish_mappings` and
   `verify_mappings` should share one resolved context for the duration of a
   single projection, rather than each resolving independently.

**Constraint.** The memo must be scoped to one projection. A resolver-level or
process-level cache would serve a stale sandbox address after a device is
re-bound, which is worse than the latency it saves.

---

### P2 — `verify` is a second full round trip carrying a byte-identical payload

**Issue.** `_apply_pool_mappings` publishes, then immediately re-sends the same
`mappings`, `retired_mappings`, `source_layout`, and contract version to a second
endpoint purely to be told the publish worked:

```python
# src/backend/src/agentclaw/community/core/skill_center/services/bot_runtime_projector.py:600
published = await self._pool_runtime.publish_mappings(
    bot_id=bot_id, user_id=owner_id, mappings=mappings,
    retired_mappings=retired_mappings, source_layout=source_layout,
    mapping_contract_version=contract,
)
verified = published and await self._pool_runtime.verify_mappings(
    bot_id=bot_id, user_id=owner_id, mappings=mappings,          # ← identical arguments
    retired_mappings=retired_mappings, source_layout=source_layout,
    mapping_contract_version=contract,
)
```

Both calls land on the same device, run the same contract decoding, and inspect
the same filesystem. The engine's two handlers differ only in which plugin method
they invoke:

```python
# src/engine/src/engine/community/api/skills/router.py:289
result = await plugin.publish_pool_mappings([...], **layout_kwargs)
return ApiResponse(success=result.published, data=result.to_data(), ...)

# src/engine/src/engine/community/api/skills/router.py:255
result = await plugin.verify_pool_mappings([...], **layout_kwargs)
return ApiResponse(success=result.valid, data=result.to_data(), ...)
```

Cost in the trace: **0.70 s**, 14% of the request, to re-assert what was just
written.

**How the fix should work.** The engine's publish path performs the verification
inline and reports it in its response — for example `data.verified: true` — so
the backend can accept the publish as verified and skip the second call:

```python
outcome = await self._pool_runtime.publish_and_verify_mappings(...)
if not outcome.verified:                       # never `or outcome.reported_inline`:
    raise SkillSetRuntimeReconcileError()      # a runtime can report inline *and* fail
```

**Compatibility is the whole design here.** Device runtimes are deployed
independently and older ones will not set the field. Absence of the signal must
mean "not verified inline", falling back to today's separate `verify` call —
never "assume verified". No probe round trip may be added to discover the
capability; that would spend the round trip this problem exists to remove.

**Staged benefit.** The saving materialises only for bots on an engine build
that reports the signal. Backend and engine changes ship together; the latency
improvement follows the device rollout.

---

### P3 — `flush_installations` runs four times per mutation

**Issue.** Every read through `BotCapabilityStateReader` flushes SkillSet
configuration into Installation first — by design:

```python
# src/backend/src/agentclaw/community/core/skill_center/services/bot_capability_state_reader.py:56
def active_skill_assets(self, *, bot_id, owner_id, bot=None):
    bot = self._bot(bot_id=bot_id, owner_id=owner_id, bot=bot)
    self._flush(bot=bot, bot_id=bot_id, owner_id=owner_id)      # ← full plan resolve
    return tuple(self._pool_skills.list_bot_installed_assets(...))
```

One deactivate reaches that flush four times:

```python
# src/backend/.../_mutation_flow.py:129 — flush #1 (pre-mutation, genuinely distinct)
previous_mappings = await self._runtime.snapshot_skill_mappings(bot_id=bot_id, owner_id=owner_id)
result = mutation()
...
# src/backend/.../_mutation_flow.py:160 — flush #2 (post-mutation)
current_mappings = await self._runtime.snapshot_skill_mappings(bot_id=bot_id, owner_id=owner_id)
await self._runtime.project(...)                    # → _resolve_plan → _build_plan
```

```python
# src/backend/.../bot_runtime_projector.py:_build_plan — flush #3, same post-mutation state
skill_assets = tuple(self._reader.active_skill_assets(bot_id=bot_id, owner_id=owner_id, bot=bot))
...
effective_mcp_entries = service.collect_bot_active_mcps(...)   # → flush #4
...
installed_mcp_server_codes=frozenset(
    self._repository.list_installed_mcps(bot_id=bot_id, owner_id=owner_id)   # ← 2nd installed-MCP read
),
```

```python
# src/backend/.../skill_set_service.py:1742 — flush #4
def _installed_mcp_codes(self, *, entity_id, bot_id, user_id) -> frozenset:
    return self._reader.active_mcp_server_codes(bot_id=bot_id, owner_id=user_id)   # flushes again
```

Each flush is a `_resolve_flush_plan`: one query per SkillSet row, plus
per-set membership and Default-Set exclusion queries. Flushes #2, #3 and #4 all
observe the identical post-mutation state and produce identical results.
`list_installed_mcps` likewise runs twice over unchanged data.

**How the fix should work.** Resolve the post-mutation state once and thread it.
`_project_or_compensate` already computes `current_mappings` — the same value
`_build_plan` recomputes as `skill_assets`. Passing the resolved assets (and the
installed-MCP set) into the plan build collapses flushes #2–#4 into one, taking
the per-mutation total from four to two: one before the mutation, one after.

**Constraint.** Flush #1 must stay a separate read. It observes pre-mutation
state and is what makes rollback able to retire already-published mappings.
Any memo must be scoped to one request and must not survive the mutation that
invalidates it.

---

### P4 — The MCP half rewrites the device allow-list and the Passport manifest when nothing changed

**Issue.** `_apply_non_skill_projection` narrows the mutation's declared MCP
scope against what the projection actually resolved:

```python
# src/backend/.../bot_runtime_projector.py:531
claimed = scope.claimed_mcp & codes       # a claim that did not survive projection
released = scope.released_mcp - codes     # a release something else still supplies
```

When both come out empty, the projected MCP set is provably identical to what it
was before the mutation — nothing entered it, nothing left it. The trace shows
exactly that case: no `[sync_mcp_delivery] pushing MCP configuration` line,
because the deactivated Set's MCPs are all still supplied by the Default policy.
Both device-facing writes still run:

```python
# src/backend/.../bot_runtime_projector.py:544
if not await service.sync_mcp_projection(claimed=claimed, released=released, declared=codes):
    raise SkillSetRuntimeReconcileError()      # → POST /api/mcp/filter-servers, 0.18 s
...
# src/backend/.../bot_runtime_projector.py:558
self._passport.update_passport(                # → updatePassport, 0.20 s
    bot_id=bot_id, user_id=owner_id, engine_type=engine,
    resource_scope={"mcp_codes": [...], "mcp_items": mcp_items, "cli_items": effective_cli_items},
)
```

The trace's own numbers confirm the writes were no-ops: 12 codes declared to the
device and 11 to the Passport, both identical to the values already held.

**How the fix should work.** When `claimed` and `released` are both empty after
the guard, skip both writes and log the skip. This is the principle the module
already states for scope-based skipping, applied one level deeper:

> *"A mutation that changed one half has nothing to say to the other, and both
> halves are whole-snapshot writes: re-sending the unchanged one costs a device
> round trip (or a Pool publish plus verify) to restate what is already there."*
> — `bot_runtime_projector.py:141`

**Explicit behavior change.** Today's unconditional re-declaration incidentally
repairs device drift. Skipping it gives that up for unchanged projections. The
deliberate repair path is unaffected: the device-activated listener sets
`scope.claim_all_mcp`, which takes the `claimed = frozenset(codes)` branch above
and always declares the full set to a freshly started container.

---

### P5 — The Skill half publishes even when the mapping set is provably unchanged

**Issue.** Both activate and deactivate hard-code `skills=True` regardless of
whether any Skill actually moved:

```python
# src/backend/.../skill_set_management_service.py — deactivate, after 70061fa
scope=ProjectionScope(skills=True, mcp=True),

# …and activate, which still names its claimed codes
scope_from_result=lambda result: ProjectionScope(
    skills=True, mcp=True, claimed_mcp=result.mcp_codes
),
```

so `project` always takes the publishing branch:

```python
# src/backend/.../bot_runtime_projector.py:154
if scope.skills or retired_mappings:
    await self._apply_skill_projection(...)     # → publish (+ verify)
```

Deactivating a Set that is already inactive, or one holding no Skills, therefore
still pays a full publish and verify. `changed` is computed and then never
consulted for this decision:

```python
# src/backend/.../capability_desired_state.py:522
changed = bool(row.is_active) != active
```

**How the fix should work.** Decide from evidence, not from a flag. The flow
already holds both snapshots and already trusts them over the declared scope for
retirements:

> *"`retired_mappings` overrides the Skill flag rather than trusting it: those
> retirements were computed from the actual before/after snapshots, so they are
> evidence that Skills moved."* — `bot_runtime_projector.py:148`

Extend the same reasoning symmetrically: when `current_mappings == previous_mappings`
and there are no retirements, the mapping set has provably not moved and the
Skill half has nothing to publish.

```python
skills_moved = set(current_mappings) != set(previous_mappings) or bool(retired)
```

Comparing snapshots rather than reading `changed` is what makes this safe: it
skips only when the payload that would be sent is identical to the one already
published, whatever the mutation claimed about itself.

---

## User Stories

- As a workbench user, I want deactivating a SkillSet to complete promptly, so
  that I do not assume it hung and retry it.
- As a workbench user, I want a repeated deactivate of an already-inactive Set to
  return quickly, so that an accidental double-click is cheap.
- As an operator, I want one control-plane mutation to make the minimum number of
  device round trips, so that a slow or loaded sandbox degrades one request
  instead of three.
- As a backend engineer, I want the projection to read each piece of Bot state
  once per request, so that added database latency does not multiply by four.

## Acceptance Criteria

Behavioral criteria are stated as counts because they are deterministically
testable; the latency targets are the observed outcome those counts produce.

- [ ] **[now, P2]** A SkillSet activate or deactivate that changes Skills makes
      **at most one** device call to publish mappings when the runtime reports
      inline verification, and at most two when it does not.
- [ ] **[now, P1]** A SkillSet activate or deactivate resolves the device address
      **exactly once**, no matter how many device calls the projection makes.
- [ ] **[now, P1]** One device address resolution reads the binding row **exactly
      once**.
- [ ] **[deferred → #1621]** `flush_installations` runs **at most twice** per
      mutation — once before the mutation, once after — and `list_installed_mcps`
      runs at most once after.
- [ ] **[deferred → #1622]** When a mutation's claimed and released MCP codes are
      both empty after narrowing against the projected set, **no** MCP allow-list
      declaration and **no** Passport update is sent, and the skip is logged.
- [ ] **[deferred → #1623]** When the post-mutation mapping set equals the
      pre-mutation set and there are no retirements, **no** mapping publish or
      verify is sent, and the skip is logged.
- [ ] **[now]** A projection whose device write fails still compensates desired
      state and still counter-projects, unchanged from today.
- [ ] **[now, P2]** A device runtime that does not report inline verification
      still gets the separate verify call, and a publish that is not verified by
      either route still raises `SkillSetRuntimeReconcileError`.
- [ ] **[deferred → #1622]** The device-activated listener path (`claim_all_mcp`)
      still declares the full MCP set to a freshly active container.
- [ ] **[now]** Measured against the 2026-08-27 trace shape (one Set, ~12 MCPs,
      ARCA sandbox): P1 and P2 together remove one device round trip and two of
      three address resolutions, an expected **≈1.3 s** off the 4.98 s baseline
      once devices carry the inline-verification signal. The **≤ 2.5 s** and
      **≤ 1.0 s** end-state targets need the deferred items and are not claimed
      by this change.

## In Scope

- Backend: `SkillsPoolRuntime`, `DeviceContextResolver`, `ArcaConnInfoBuilder`,
  `DeviceService.get_device_connection_v2`, `DeviceServiceRouter`.
- Backend: `MutationProjectionFlow`, `BotRuntimeProjector`,
  `BotCapabilityStateReader`, `SkillSetManagementService`.
- Engine: the `/api/skills/layout/mappings/publish` handler and the skills
  plugin protocol, to report inline verification.
- Both SkillSet activate and deactivate, and the direct-activation service where
  it shares `MutationProjectionFlow`.

## Out of Scope

- The ~40 ms cost of an individual primary-key read. Every repository method
  opens its own ORM session; 40 ms for a `get_by_id` points at connection
  checkout or tenant binding, not the query. Reducing the *number* of reads is
  in scope here; making each read cheaper is a separate investigation into pool
  configuration.
- The ~0.98 s spent before any projection work begins (request context,
  collaborator permission interceptor, path-parameter recovery). Worth its own
  trace; nothing in this change touches it.
- The double audit path visible in the trace — the service inserts its own
  collaboration-log row and the interceptor then decides not to log — which is a
  correctness/tidiness question, not a latency one (0.04 s).
- Changing what desired state a mutation persists, what the runtime ends up
  holding, or any HTTP request or response shape of the control-plane API.
- Making projection asynchronous or moving it off the request path. The
  synchronous mutate-project-compensate contract stays exactly as it is.
- Teclaw engine delivery, which uses `sync_runtime` rather than the Pool mapping
  endpoints and is unaffected beyond inheriting P3 and P5.

## Open Questions

> Questions 1 and 3 below moved with their problems: the P4 drift-repair
> question now lives in #1622 and the `/skillset/sync` question in #1623. They
> stay here for continuity of the analysis. Only the P2 question is live for the
> current change.

- **P4 drift repair.** *(moved to #1622)* Skipping the unchanged allow-list write gives up an
  incidental repair mechanism. Is `claim_all_mcp` on device activation
  sufficient as the deliberate repair path, or should an explicit reconcile
  entry point be added before the skip lands?
- **P2 rollout ordering.** *(live for this change)* Backend and engine ship together, but devices update
  on their own schedule. Is a metric on how often the fallback `verify` path is
  taken wanted, so the rollout's progress is observable?
- **P5 and `POST /skillset/sync`.** *(moved to #1623)* A Default Set returns early from
  `set_skill_set_active` with `changed=False` and no MCP codes, so under P5 a
  sync against a Default Set becomes a complete no-op. The device-activation
  reconcile is safe — it calls `BotRuntimeProjector.project` directly with
  `ProjectionScope.everything()` and never passes through
  `MutationProjectionFlow`, so the snapshot comparison cannot reach it. The
  remaining question is whether any client treats `/skillset/sync` as a manual
  "resync my device" button today. If so, that intent needs its own endpoint
  rather than riding on a mutation that changes nothing.
