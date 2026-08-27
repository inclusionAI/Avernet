# Plan: SkillSet Projection Latency

> **Scope of this change: P1 and P2 only.** The P3, P4 and P5 material below is
> retained as the working design for inclusionAI/Avernet#1621, #1622 and #1623
> respectively, and is not implemented here. Sections that mix the two are
> tagged inline.

## Approach

Five independent reductions, ordered by benefit-to-risk — **P1 and P2 land in
this change; P3–P5 are deferred to their issues**. Each removes duplicate
work without changing what desired state is persisted or what the runtime ends
up holding. Nothing becomes asynchronous; the mutate-project-compensate contract
is untouched.

The unifying move is **resolve once, thread the result** rather than adding
caches: no memo outlives the call that created it, so nothing can serve stale
device or Bot state. The two skip decisions (P4, P5) are derived from evidence
already in hand — a narrowed MCP scope, a before/after mapping comparison — not
from a `changed` boolean.

## Affected Components

- `src/backend/.../core/skills_pool/runtime.py` — resolves the device once per
  publish, absorbs the verify fallback (P1b, P2).
- `src/backend/.../core/skills_pool/ports.py` — the runtime protocol gains the
  combined entry point (P1b, P2).
- `src/backend/.../core/devices/services/conn_info_builders/arca_builder.py`,
  `.../devices/services/device_service.py`,
  `.../devices/services/device_service_router.py`,
  `.../devices/services/local_device_service.py`,
  `.../devices/services/baas_device_service.py` — stop re-reading a binding row
  the caller already holds (P1a).
- *(deferred → #1621, #1623)* `src/backend/.../core/skill_center/services/_mutation_flow.py`
  — reads post-mutation state once, decides whether Skills moved (P3, P5).
- `src/backend/.../core/skill_center/services/bot_runtime_projector.py` — calls
  the combined publish entry point (P2, **now**); accepts pre-resolved state and
  skips an unchanged MCP half (P3, P4, *deferred*).
- *(deferred → #1621)* `src/backend/.../core/skill_center/runtime_projection_contract.py`,
  `src/backend/.../api/bot_runtime_projector.py` — projector protocol.
- *(deferred → #1621)* `src/backend/.../core/skill_center/services/skill_set_service.py`
  — accepts pre-resolved installed MCP codes (P3).
- `src/engine/.../plugins/skills_pool/layout_activation.py` — publish verifies
  inline and says so (P2). One file covers every engine: the openclaw, hermes,
  claude_code and aicoding wrappers all delegate here, and
  `plugins/openclaw/layout_activation.py:1` is a module alias for it.
- `src/engine/.../core/skills/models.py`,
  `src/engine/.../core/adapters/{openclaw,claude_code}/skills.py` — carry the
  inline-verification flag to the wire (P2).

## Data Model Changes

None. No table, column, index, or migration is touched.

## API / Interface Changes

No HTTP request or response shape of the control plane changes. Two internal
protocols and one device-facing response body change, all additively.

### P2 — engine publish response gains an inline verification flag

```jsonc
// POST /api/skills/layout/mappings/publish → 200  (data object)
{
  "published": true,
  "verified": true,        // NEW — absent on runtimes that predate this change
  "evidence": { "total": 12, "created": [], "updated": [], "kept": [], "removed": [] }
}
```

`verified` is a three-state signal read as: `true` → the runtime verified
inline, skip the separate call; `false` → publication landed but verification
failed, treat as not converged; **absent** → the runtime does not support inline
verification, fall back to a separate `/verify` call. Absence must never be read
as `true`.

### P1b + P2 — one combined runtime entry point

```python
# src/backend/.../core/skills_pool/models.py (new)
@dataclass(frozen=True)
class MappingPublishOutcome:
    published: bool
    verified: bool
    verified_inline: bool = False   # evidence for logging/metrics, not control flow
```

```diff
# src/backend/.../core/skills_pool/ports.py — SkillsPoolRuntimeProtocol
+    async def publish_and_verify_mappings(
+        self,
+        *,
+        bot_id: str,
+        user_id: str,
+        mappings: list[PoolSkillMapping],
+        retired_mappings: Sequence[PoolSkillMapping] = (),
+        source_layout: SkillMappingSourceLayout = SkillMappingSourceLayout.POOL,
+        mapping_contract_version: str = "skills-pool-mapping-v2",
+    ) -> MappingPublishOutcome: ...
```

`publish_mappings` and `verify_mappings` stay on the protocol unchanged. The
three other publish→verify pairs (`reconcile_service.py:868`,
`recovery_service.py:499`, `mapping_convergence.py:98`) keep working as-is and
can adopt the combined call later; `MappingPublishOutcome` keeps `published` and
`verified` separate precisely so they can, since they report `PUBLISH_FAILED`
and `VERIFY_FAILED` as distinct outcomes.

### P3 — projector accepts pre-resolved Skill assets *(deferred → #1621)*

```diff
# src/backend/.../core/skill_center/runtime_projection_contract.py:92
#  (mirrored in api/bot_runtime_projector.py)
+    async def snapshot_skill_assets(
+        self, *, bot_id: str, owner_id: str
+    ) -> tuple[RegisteredSkillAsset, ...]:
+        """Return current desired Skill assets without publishing them."""
+        ...
+
     async def project(
         self,
         *,
         bot_id: str,
         owner_id: str,
         retired_mappings: Sequence[PoolSkillMapping] = (),
         scope: ProjectionScope,
+        skill_assets: Sequence[RegisteredSkillAsset] | None = None,
     ) -> None:
```

`skill_assets=None` keeps today's behavior (resolve internally), so
`SkillSymlinkListener`'s direct `project(...)` call
(`di/modules/skill_center_module.py:918`) is unaffected.

### P1a — device connection accepts an already-loaded binding record

```diff
# src/backend/.../core/devices/services/device_service.py:1679
 def get_device_connection_v2(
     self, user_id: str, nick_name: str, binding_id: int,
     operator_tenant_id: str = "default",
+    *, record: DeviceBindingRecord | None = None,
 ) -> dict:
```

```diff
# base device_service.py:950, router :660, local_device_service.py:757,
# baas_device_service.py:769 — same additive keyword on all four
 def get_device_connection(
     self, *, binding_id: int, operator: OperatorContext,
     port: int | None = None, ttl: int | None = None,
     device_uuid: str | None = None, ws_conn_mode: str | None = None,
     path: str | None = None,
+    record: DeviceBindingRecord | None = None,
 ) -> DeviceConnectionInfo:
```

The published Service API protocol is permissive — `api/device_service.py:20`
declares `def get_device_connection(self, *args, **kwargs)` — so no Service API
contract change is required. `local` and `baas` accept `record` and may ignore
it; only the base (arca) path consumes it in this change.

## Key Files & Functions

### P1a — collapse four binding reads to zero extra reads

```diff
# src/backend/.../devices/services/conn_info_builders/arca_builder.py:20
     def build(self, binding: DeviceBindingRecord, user_id: str, *, device_uuid=None):
         return self._device_service.get_device_connection_v2(
             binding_id=binding.id,
             user_id=user_id,
             nick_name=user_id,
+            record=binding,          # the resolver already loaded it
         )
```

```diff
# src/backend/.../devices/services/device_service.py:1710 — get_device_connection_v2
-        device_result = self.get_device(binding_id=binding_id)
+        device_result = record if record is not None else self.get_device(binding_id=binding_id)
         device_props = getattr(device_result, 'device_props', {}) or {}
...
-        result = self.get_device_connection(binding_id=binding_id, operator=operator)
+        result = self.get_device_connection(
+            binding_id=binding_id, operator=operator, record=record,
+        )
```

```diff
# src/backend/.../devices/services/device_service_router.py:179
-    def _get_provider_for_binding(self, binding_id: int) -> DeviceService:
-        record = self._repo.get_by_id(binding_id)
+    def _get_provider_for_binding(
+        self, binding_id: int, *, record: DeviceBindingRecord | None = None
+    ) -> DeviceService:
+        record = record if record is not None else self._repo.get_by_id(binding_id)
```

```diff
# src/backend/.../devices/services/device_service.py:978 — get_device_connection
-        record = self._repo.get_by_id(binding_id)
+        record = record if record is not None else self._repo.get_by_id(binding_id)
         if record is None:
             raise DeviceNotFoundError(f"binding {binding_id} not found")
```

The router's `get_device_connection` (`:660`) and `get_device` (`:412`) pass
`record` through to `_get_provider_for_binding` and to the provider.

### P1b — one device resolution per projection

```diff
# src/backend/.../core/skills_pool/runtime.py:409
     async def _invoke(
-        self, *, bot_id: str, user_id: str, path: str, body: dict[str, Any],
+        self, *, bot_id: str, user_id: str, path: str, body: dict[str, Any],
+        context: DeviceContext | None = None,
     ) -> dict[str, Any]:
-        context = self._resolver.resolve_for_bot(bot_id, user_id)
+        context = context if context is not None else self._resolver.resolve_for_bot(bot_id, user_id)
         return await self._transport.invoke(context.conn_info, "POST", path, body=body, timeout=30.0)
```

```python
# src/backend/.../core/skills_pool/runtime.py (new method)
async def publish_and_verify_mappings(self, *, bot_id, user_id, mappings,
                                      retired_mappings=(), source_layout=..., 
                                      mapping_contract_version=...) -> MappingPublishOutcome:
    context = self._resolver.resolve_for_bot(bot_id, user_id)   # ← the only resolution
    if not await self._ensure_center_mappings(..., context=context):
        return MappingPublishOutcome(published=False, verified=False)
    published, inline = await self._publish(..., context=context)   # reads data["verified"]
    if not published:
        return MappingPublishOutcome(published=False, verified=False)
    if inline is True:
        return MappingPublishOutcome(published=True, verified=True, verified_inline=True)
    verified = await self._verify(..., context=context)          # absent/false → fall back
    return MappingPublishOutcome(published=True, verified=verified)
```

`_ensure_center_mappings`, the publish call, and the fallback verify all take
`context=`, so a center-corpus projection drops from three resolutions to one.

```diff
# src/backend/.../skill_center/services/bot_runtime_projector.py:598 — _apply_pool_mappings
-            published = await self._pool_runtime.publish_mappings(...)
-            verified = published and await self._pool_runtime.verify_mappings(...)
+            outcome = await self._pool_runtime.publish_and_verify_mappings(
+                bot_id=bot_id, user_id=owner_id, mappings=mappings,
+                retired_mappings=retired_mappings, source_layout=source_layout,
+                mapping_contract_version=contract,
+            )
         except Exception as exc:
             raise SkillSetRuntimeReconcileError() from exc
-        if not verified:
+        if not outcome.verified:
             raise SkillSetRuntimeReconcileError()
```

### P2 — engine verifies inline

```diff
# src/engine/.../plugins/skills_pool/layout_activation.py:114
 @dataclass(frozen=True, slots=True)
 class MappingPublishResult:
     published: bool
     evidence: dict[str, object]
+    verified: bool | None = None     # AFTER evidence: evidence has no default

     def to_data(self) -> dict[str, object]:
-        return {"published": self.published, "evidence": self.evidence}
+        data = {"published": self.published, "evidence": self.evidence}
+        if self.verified is not None:
+            data["verified"] = self.verified
+        return data
```

The three early `MappingPublishResult(published=False, ...)` returns
(`:2371`, `:2379`, `:2387`, `:2413`, `:2423`) keep `verified=None`, which is
correct: a failed publish is never verified, and
`publish_and_verify_mappings` returns before the fallback on `published=False`.

```diff
# src/engine/.../plugins/skills_pool/layout_activation.py:2431 — publish_pool_mappings tail
-    return MappingPublishResult(
-        published=True,
-        evidence={"total": len(plan.managed), "created": created, ...},
-    )
+    verification = verify_skill_mappings(
+        mappings=mappings, retired_mappings=retired_mappings, home=home,
+        engine=engine, source_layout=source_layout,
+        additional_retirement_roots=additional_retirement_roots,
+    )
+    return MappingPublishResult(
+        published=True,
+        verified=verification.valid,
+        evidence={"total": len(plan.managed), "created": created, ...,
+                  "verification": verification.evidence},
+    )
```

Inline verification is cheap: `verify_skill_mappings` (`:2288`) re-uses the same
`_Layout.for_engine` / `_retirement_plan` / `_mapping_plan` helpers the publish
just ran, then `lstat`s the symlinks it just wrote — all local, all warm in the
page cache. The expensive part of today's double call is the network round trip,
not the verification.

```diff
# src/engine/.../core/skills/models.py:299 — PoolMappingPublishResult
 @dataclass
 class PoolMappingPublishResult:
     published: bool
     evidence: dict[str, Any] = field(default_factory=dict)
+    verified: bool | None = None

     def to_data(self) -> dict[str, Any]:
-        return {"published": self.published, "evidence": self.evidence}
+        data = {"published": self.published, "evidence": self.evidence}
+        if self.verified is not None:
+            data["verified"] = self.verified
+        return data
```

`verified` is omitted rather than sent as `null` when unknown, so the field's
presence is itself the capability signal.

**Two `to_data()` hops, not one.** The plugin port returns the *plugin*
dataclass's dict (`plugins/openclaw/_skills.py:119`), the adapter rebuilds the
*core* model from it (`core/adapters/openclaw/skills.py:483`), and the router
serialises that (`api/skills/router.py:317`). `verified` has to survive all
three:

```diff
# src/engine/.../core/adapters/openclaw/skills.py:483 (claude_code/skills.py is the mirror)
         raw = await self._port.publish_pool_mappings(payload)
         return PoolMappingPublishResult(
             published=raw.get("published") is True,
             evidence=dict(raw.get("evidence") or {}),
+            verified=(raw["verified"] is True) if "verified" in raw else None,
         )
```

### P3 — resolve post-mutation state once *(deferred → #1621)*

```diff
# src/backend/.../skill_center/services/_mutation_flow.py:129
-        previous_mappings = await self._runtime.snapshot_skill_mappings(bot_id=..., owner_id=...)
+        previous_assets = await self._runtime.snapshot_skill_assets(bot_id=..., owner_id=...)
+        previous_mappings = build_logical_skill_mappings(previous_assets)
         result = mutation()
```

```diff
# src/backend/.../skill_center/services/_mutation_flow.py:160 — _project_or_compensate
-            current_mappings = await self._runtime.snapshot_skill_mappings(bot_id=..., owner_id=...)
+            current_assets = await self._runtime.snapshot_skill_assets(bot_id=..., owner_id=...)
+            current_mappings = build_logical_skill_mappings(current_assets)
             await self._runtime.project(
                 bot_id=bot_id, owner_id=owner_id,
                 retired_mappings=retired_logical_skill_mappings(...),
                 scope=scope,
+                skill_assets=current_assets,      # ← flush #3 no longer needed
             )
```

```diff
# src/backend/.../skill_center/services/bot_runtime_projector.py — _build_plan
-        skill_assets = tuple(self._reader.active_skill_assets(bot_id=..., owner_id=..., bot=bot))
+        skill_assets = (
+            tuple(prefetched_assets) if prefetched_assets is not None
+            else tuple(self._reader.active_skill_assets(bot_id=..., owner_id=..., bot=bot))
+        )
+        # One installed-MCP read serves both the Default union and the resolver.
+        installed_mcp_codes = frozenset(
+            self._repository.list_installed_mcps(bot_id=bot_id, owner_id=owner_id)
+        )
         ...
-        effective_mcp_entries = service.collect_bot_active_mcps(..., strict_policy_context=True)
+        effective_mcp_entries = service.collect_bot_active_mcps(
+            ..., strict_policy_context=True, installed_codes=installed_mcp_codes,
+        )
```

```diff
# src/backend/.../skill_center/services/skill_set_service.py:1581 — collect_bot_active_mcps
     def collect_bot_active_mcps(self, entity_id, bot_id, user_id, entity_type="staff",
-                                engine_type=None, *, strict_policy_context=False):
+                                engine_type=None, *, strict_policy_context=False,
+                                installed_codes: frozenset[str] | None = None):
...
             installed_codes=(
-                self._installed_mcp_codes(entity_id=entity_id, bot_id=bot_id, user_id=user_id)
+                installed_codes if installed_codes is not None
+                else self._installed_mcp_codes(entity_id=entity_id, bot_id=bot_id, user_id=user_id)
             ),
```

Result: flushes go 4 → 2 (one pre-mutation at `_mutation_flow.py:129`, one
post-mutation at `:160`), `list_installed_mcps` goes 2 → 1. The pre-mutation
flush must stay separate — it is what lets a rollback retire already-published
mappings.

### P4 — skip an MCP half that changed nothing *(deferred → #1622)*

```diff
# src/backend/.../skill_center/services/bot_runtime_projector.py:531 — _apply_non_skill_projection
             claimed = scope.claimed_mcp & codes
             released = scope.released_mcp - codes
             ...
+            if not claimed and not released:
+                # Nothing entered or left the projected set, and both device
+                # writes are whole-snapshot: re-sending restates what is
+                # already there. The deliberate repair path is claim_all_mcp.
+                logger.info(
+                    "[BotRuntimeProjector] MCP/CLI projection skipped, projected "
+                    "set unchanged: bot_id=%s, engine=%s, declared=%s",
+                    bot_id, engine, len(codes),
+                )
+                return
```

Placed inside the `else` branch so `claim_all_mcp` — which takes
`claimed = frozenset(codes)` — is structurally unable to reach it.

### P5 — skip a Skill half whose mapping set is unchanged *(deferred → #1623)*

```diff
# src/backend/.../skill_center/services/_mutation_flow.py — _project_or_compensate
             retired = retired_logical_skill_mappings(list(previous_mappings), list(current_mappings))
+            # Evidence, not the mutation's word: the same reasoning that lets
+            # retirements override a scope that declares no Skill change.
+            if set(current_mappings) == set(previous_mappings) and not retired:
+                scope = replace(scope, skills=False)
             await self._runtime.project(..., retired_mappings=retired, scope=scope, ...)
```

Narrowing the scope rather than returning early is deliberate: the MCP half must
still run (and P4 decides independently whether it writes anything). The skip
lives in the flow, not in `BotRuntimeProjector.project`, so the device-activation
reconcile — which calls `project` directly with `ProjectionScope.everything()`
and never passes through the flow — keeps publishing unconditionally.

`PoolSkillMapping` is `@dataclass(frozen=True, slots=True)`
(`core/skills_pool/models.py:41`), so it is hashable and the set comparison is
sound.

## Dependencies

None. No new packages, no version bumps, no new internal services.

## Risks & Mitigations

- **Risk:** A stale device context is reused after a re-bind mid-projection.
  **Mitigation:** The context is a local variable in one method call, never an
  instance attribute or a process cache. It cannot outlive the projection.

- **Risk:** An older device runtime is read as inline-verified because the field
  is missing.
  **Mitigation:** Absence is a distinct state from `false`; the engine omits the
  key entirely when unknown, and the backend treats anything other than `True`
  as "must verify separately". Covered by an explicit test.

- **Risk:** P4 removes an incidental drift-repair mechanism.
  **Mitigation:** `claim_all_mcp` on device activation stays the deliberate
  repair path and is structurally excluded from the skip. Flagged as an open
  question in the spec; if an explicit reconcile endpoint is wanted, it is
  additive and does not block this change.

- **Risk:** P5 makes `POST /skillset/sync` against a Default Set a full no-op,
  breaking a client that uses it as a manual resync.
  **Mitigation:** Spec open question. If a client does rely on it, P5 lands with
  `legacy_activate` opted out of the skip (it already has its own
  `action="skill_set_sync"` code path) until a real resync endpoint exists.

- **Risk:** Adding `record` to `get_device_connection` breaks a subclass override
  that does not accept it.
  **Mitigation:** All three overrides (base, local, baas) plus the router are
  edited together with a defaulted keyword-only parameter. The Service API
  protocol is `*args, **kwargs`, so nothing outside is affected.

- **Risk:** P3's threading passes assets resolved before a concurrent write.
  **Mitigation:** No wider a window than today — `_build_plan` already read
  those assets a few milliseconds later in the same request. The mutation's own
  row locks are unchanged.

## Alternatives Considered

- **Cache `DeviceContext` in `DeviceContextResolver` keyed by `(bot_id, user_id)`.**
  Rejected: the resolver is a singleton, so the cache would outlive the request
  and serve a dead sandbox address after a re-bind. A TTL only narrows the
  window.
- **`contextvars`-scoped device session on `SkillsPoolRuntime`.** Correct under
  asyncio but adds ambient state for a problem an explicit parameter solves.
  Rejected in favour of `publish_and_verify_mappings` owning the resolution.
- **Resolve the context in `BotRuntimeProjector` and pass it into the runtime.**
  Rejected: it puts device resolution in the projector, contradicting the stated
  boundary at `skill_set_service.py:530` ("this service — not the projector —
  owns device resolution").
- **Negotiate inline verification through the existing `probe` endpoint.**
  Rejected: `probe` is a device round trip, and it currently runs only for
  center-corpus mappings. Spending a round trip to learn whether we can save one
  is a net loss. A field on the publish response costs nothing.
- **Skip projection when `mutation.changed is False`.** Rejected in favour of
  P5's snapshot comparison. `changed` describes the row, not the payload; a Set
  whose membership shifted while its `is_active` column did not would be wrongly
  skipped. Comparing mapping sets skips only when the bytes we would send are
  identical to the bytes already published.
- **Make projection asynchronous / fire-and-forget.** Out of scope by the spec;
  it would change the synchronous success contract callers depend on.

## Rollout

No migration, no feature flag. Backend and engine changes ship in the same PR;
the engine half is inert until devices update.

```bash
# order is irrelevant — each half is independently backward compatible
# backend without engine : `verified` absent → falls back to a separate /verify (today's behavior)
# engine without backend : `verified` present → ignored by the old client
```

Latency improves in two steps: P1, P3, P4, P5 take effect on backend deploy; P2
takes effect per-bot as sandbox images pick up the new engine.

## Test Strategy

Unit and contract tests. No manual device testing is required — the transport is
mocked in every layer below.

```python
# src/backend/tests/community/core/skills_pool/test_runtime.py
def test_publish_and_verify_resolves_device_once():            # P1b: resolver called 1×, transport 2×
def test_publish_and_verify_skips_verify_when_inline_verified():  # P2: data.verified True → 1 transport call
def test_publish_and_verify_falls_back_when_verified_absent():    # P2: no key → separate /verify
def test_publish_and_verify_treats_verified_false_as_unverified():
def test_center_ensure_shares_the_resolved_context():          # P1b: 3 calls, 1 resolution
```

```python
# src/backend/tests/community/core/devices/services/test_device_context_resolver.py
def test_arca_resolution_reads_the_binding_row_once():         # P1a: repo.get_by_id call count == 0 extra
```

```python
# src/backend/tests/community/core/skill_center/test_skill_set_management_service.py
# (this file already exercises MutationProjectionFlow and BotRuntimeProjector)
def test_flush_runs_once_before_and_once_after_the_mutation(): # P3
def test_unchanged_mapping_set_skips_the_skill_projection():   # P5
def test_retired_mappings_still_force_the_skill_projection():  # P5 guard
def test_device_activation_reconcile_still_publishes():        # P5: project() direct path
def test_empty_claim_and_release_skips_allow_list_and_passport():  # P4
def test_claim_all_mcp_still_declares_the_full_set():              # P4 guard
def test_prefetched_skill_assets_skip_the_reader():                # P3
```

```python
# src/backend/tests/community/contracts/test_bot_runtime_projector.py
# extend _RecordingReconciler (line 20) with snapshot_skill_assets; assert both
# the Service API protocol and the core protocol still accept BotRuntimeProjector.
```

```python
# src/engine/src/engine/community/plugins/openclaw/tests/test_layout_activation.py
# (plugins/openclaw/layout_activation.py is a module alias for the shared
#  skills_pool implementation, so this file covers the real code)
def test_publish_reports_inline_verification():                # P2
def test_publish_reports_verified_false_on_a_broken_symlink():  # P2
```

**Existing tests that must be updated, not just added to:**

- `test_skill_set_management_service.py:259` — the `_Runtime` double needs
  `snapshot_skill_assets` and a `skill_assets=` parameter on `project`.
- `test_skill_set_management_service.py:1609`
  `test_existing_claude_code_skill_set_deactivate_uses_full_projection` and
  `:821` `test_deactivate_retires_mappings_removed_from_the_runtime_projection`
  assert the exact `ProjectionScope` reaching `project`. Both use snapshots
  where mappings genuinely change, so P5 leaves `skills=True` and they should
  still pass — verify rather than assume.
- `test_skill_set_management_service.py:1806`
  `test_runtime_projection_flushes_installations_first` pins flush ordering;
  P3 changes the count, so its assertion needs re-reading against the new
  two-flush shape.
- `tests/community/core/skill_center/services/test_collect_bot_active_mcps_union.py`
  — new optional `installed_codes` parameter.

```python
# src/engine/src/engine/community/api/tests/test_skills_router.py
def test_publish_response_omits_verified_when_unknown():       # P2 wire shape
```

Latency acceptance is measured, not asserted in CI: re-run one deactivate
against a prepub bot after deploy and compare the log timeline to the
2026-08-27 trace in `spec.md`.
