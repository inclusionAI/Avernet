# Plan — One Whole-Artifact Delivery per Projection for Teclaw Bots

Implements `spec.md` in this directory.

All paths are relative to `src/backend/src/agentclaw/community/` unless
prefixed with `tests/`, which is relative to `src/backend/`.

## Shape of the change

One predicate, read in two places, inside one file.

`BotRuntimeProjector` already branches on `engine == "teclaw"` three times —
`snapshot_skill_mappings:118`, `_build_plan:~332`, `_apply_skill_projection:417`
and `:426`. This change does not add a fourth ad-hoc string comparison; it
names the property those branches are really testing and routes the two new
decisions through it.

```
project() / project_mcp_and_cli() / project_for_cleanup()
  │
  ├─ _resolve_plan(...)                       ← unchanged; flushes, validates
  │
  ├─ whole_artifact = runtime_delivers_whole_artifact(engine)
  │
  ├─ Skill half
  │     per-domain : if scope.skills or retired_mappings   (unchanged)
  │     whole-artifact : if the scope declares anything at all
  │                      → _apply_skill_projection, whose teclaw branch is
  │                        already exactly one sync_runtime()
  │
  └─ MCP/CLI half   (if scope.mcp — unchanged trigger)
        per-domain : sync_mcp_projection(...)  then  update_passport(...)
        whole-artifact :          — skipped —  then  update_passport(...)
```

The Skill half is not renamed or restructured. On teclaw it is already
one-delivery-shaped (`_apply_skill_projection` returns straight after
`service.sync_runtime(...)`), so the whole change is *when* it runs and *what
stops running beside it*.

### Why the delivery rides the Skill half rather than a new method

Three alternatives were considered.

1. **Reuse `_apply_skill_projection` (chosen).** Its teclaw branch is already
   the single `sync_runtime` call the spec asks for, it already carries the
   Center-corpus refusal that must keep firing, and it already receives every
   argument it needs. A new delivery method would duplicate both the call and
   the refusal, and the two copies could drift.

2. **Move the decision into `SkillSetService.sync_mcp_projection`**, mirroring
   its own docstring — *"how many device writes an MCP projection takes, and
   in what order, is decided by the service that owns device resolution"*.
   Rejected: that method owns *how many device writes one MCP projection
   costs*. The question here is *how many projections to issue at all*, which
   is one level up and spans both halves. `SkillSetService` cannot answer it
   without knowing what the Skill half already did.

3. **Push it down into `TeclawDeviceSyncService` as delivery deduplication**
   (compose once, suppress identical repeats). Rejected: it would need a cache
   keyed on artifact bytes with a correctness-critical invalidation story, to
   suppress calls the caller should not have made. Not issuing the call is
   strictly simpler than making the call cheap.

### Where the predicate lives

A module-level helper in `core/skill_center/runtime_projection_contract.py`,
beside `ProjectionScope` — the module that already defines what a projection
means and is already imported by the projector:

```python
#: Engines whose ``DeviceSync`` carries the Bot's complete capability state in
#: one call. Authority: ``core/devices/services/teclaw_device_sync.py`` —
#: every ``sync_*`` there funnels into ``_compose_and_deliver``, which
#: recomposes the whole ``BotConfigArtifact`` from the DB and POSTs it,
#: discarding the method's own arguments.
_WHOLE_ARTIFACT_ENGINES = frozenset({"teclaw"})


def runtime_delivers_whole_artifact(engine: str) -> bool:
    """True when one runtime call carries every half of the projection.

    ``ProjectionScope`` selects *what is written* only where the halves have
    separate runtime endpoints. On a whole-artifact engine both halves ride in
    one document composed from the database, so a second call would restate a
    delivery the first already made in full — see this directory's spec.
    """
```

Not in `core/devices/`: the projector would then depend on the device layer
for a scheduling decision, and the fact is consumed only here. Not inline in
`bot_runtime_projector.py`: `runtime_projection_contract.py` is where the
projection's vocabulary already lives, and a future second whole-artifact
engine should be a one-line edit next to the definition of scope, not a
grep for `"teclaw"`.

The three pre-existing `engine == "teclaw"` checks are **left alone**. They
test a different property — *"has no Center request contract"* and *"has no
Skills Pool mapping endpoint"* — which happen to be true of the same engine
today but are not the same statement. Collapsing them into the new predicate
would assert that any future whole-artifact engine also lacks a Center
contract, which nothing establishes.

## Changes

### 1. `core/skill_center/runtime_projection_contract.py`

Add `_WHOLE_ARTIFACT_ENGINES` and `runtime_delivers_whole_artifact(engine)` as
above. Export the function in `__all__`.

No change to `ProjectionScope`. Add one paragraph to its class docstring
recording that its per-domain guarantees ("a single-MCP add stays a single
device write", `claim_all_mcp`'s empty-container premise) describe engines
with separate runtime endpoints, and point at the predicate for the other
case — so the next reader does not have to rediscover spec Problem 3.

### 2. `core/skill_center/services/bot_runtime_projector.py`

**2a. `project` — force one delivery, drop the device MCP half.**

```python
whole_artifact = runtime_delivers_whole_artifact(engine)
declares_anything = bool(scope.skills or scope.mcp or retired_mappings)

if (whole_artifact and declares_anything) or scope.skills or retired_mappings:
    await self._apply_skill_projection(...)      # unchanged arguments
else:
    logger.info(...)                              # unchanged skip log
```

`declares_anything` keeps acceptance criterion 8: a scope declaring neither
half stays a no-op rather than becoming a delivery. No production caller
constructs such a scope today — every one sets `skills`, `mcp`, or both — so
this guard is conservatism, not a live path.

The existing skip-log branch keeps firing verbatim for per-domain engines. For
teclaw the skip is now unreachable whenever the scope declares anything, which
is the point.

**2b. `_apply_non_skill_projection` — skip `sync_mcp_projection` only.**

Take a new keyword-only `deliver_mcp_to_runtime: bool` and wrap the existing
`sync_mcp_projection` block:

```python
if deliver_mcp_to_runtime:
    ...existing claimed/released guard + sync_mcp_projection...
else:
    logger.info(
        "[BotRuntimeProjector] MCP runtime delivery folded into the "
        "whole-artifact projection: bot_id=%s, engine=%s, mcps=%s",
        bot_id, engine, len(codes),
    )
```

The Passport block after it is **not** moved, not re-indented and not
re-gated. That is what keeps acceptance criterion 4 mechanically true rather
than argued.

Note the `codes = set(projection.mcp_server_codes)` line stays above the
branch: the Passport block derives from `projection.mcp_server_codes`
independently, and keeping `codes` where it is avoids reordering anything.

**2c. All three entry points pass the flag.**

`project`, `project_mcp_and_cli` and `project_for_cleanup` each compute
`runtime_delivers_whole_artifact(engine)` from the `engine` their
`_resolve_plan` / `_resolve_cleanup_plan` already returns, and pass
`deliver_mcp_to_runtime=not whole_artifact`. This satisfies criterion 6
uniformly instead of only on the hot path.

- `project_mcp_and_cli`: for teclaw this leaves Passport-only. Its "a cutover
  task exclusively owns Skill mappings" premise cannot hold on teclaw anyway
  (spec Problem 3), and the path is unreachable for teclaw in practice —
  `_resolve_desktop_layout_authority` returns non-`None` only for
  `bot_type == "desktop"`, and `probe_bot` refuses teclaw Pool capability.
  Handled for uniformity, not because it fires.
- `project_for_cleanup`: already calls `service.sync_runtime(...)`
  unconditionally at `:240` before delegating, so on teclaw that call *is* the
  one delivery and only the device MCP half needs suppressing. Its
  Center-corpus refusal at `:238` is untouched.

**2d. Class docstring.** Extend the existing "Resolving and applying are
separated by the `ProjectionScope`" paragraph with the whole-artifact case, in
the same register as the surrounding comments.

### 3. No DI, no boundary, no contract changes

- No constructor parameter is added, so `di/modules/skill_center_module.py` is
  untouched.
- The new import is intra-module (`skill_center` → `skill_center`), so
  `core/skill_center/README.md`'s Context Boundary needs no new `consumes`
  entry and the Rule-22 architecture test is unaffected. `provides` gains
  nothing: `runtime_delivers_whole_artifact` is internal to the module.
- `BotRuntimeProjectorProtocol` and `api/bot_runtime_projector.py` keep their
  signatures; `deliver_mcp_to_runtime` is private to the implementation.
- `SkillSetService`, `MCPSyncService`, `TeclawDeviceSyncService`,
  `ConfigComposer` and every DI module are unmodified.

## Tests

### New — `tests/community/core/skill_center/test_skill_set_management_service.py`

The projector's unit-level fakes already live here (`_RuntimeFactory`,
`_TeclawRuntimeBots`, `_TeclawRuntimeSkills`, `_McpInstallations`,
`_RuntimePool`, `_RuntimeLayouts`, `_RuntimePassport`,
`_RuntimeCallerIdentity`), and the two existing teclaw projector tests are at
`:2493` and `:2519`. New cases go beside them.

`_RuntimeFactoryService` (`:474`) records `desired_skills` and `mcp_codes` as
last-write-wins **scalars**, so neither can distinguish one call from four.
It already records `deliveries: list[tuple[frozenset, frozenset]]` — one
append per `sync_mcp_delivery` — with a comment explaining exactly why a list
and not a scalar. Follow that idiom rather than adding integer counters:

- add `runtime_syncs: list[list[dict]]`, appended in `sync_runtime`;
- add `mcp_projections: list[tuple[frozenset[str], frozenset[str], set[str]]]`,
  appended in `sync_mcp_projection` **before** it delegates, so the existing
  deliver-before-declare composition at `:512-514` is preserved verbatim.

Keep `desired_skills`, `mcp_codes`, `deliveries` and `collect_calls` exactly
as they are, so no existing assertion in the file changes.

A second, unrelated `sync_mcp_projection` stub exists at `:2865` inside a
different fake; leave it alone unless a test in this group uses that fake.

Cases, one per acceptance criterion:

1. `test_teclaw_projects_the_whole_artifact_once_per_scope_shape` —
   parametrised over `ProjectionScope(skills=True)`,
   `ProjectionScope(mcp=True, claimed_mcp={"x"})`,
   `ProjectionScope(mcp=True, released_mcp={"x"})`,
   `ProjectionScope(skills=True, mcp=True, claimed_mcp={"x"})`, and
   `ProjectionScope.everything()`. Asserts `len(runtime_syncs) == 1` and
   `mcp_projections == []` for every shape. (criteria 1, 2, 3)
2. `test_teclaw_mcp_only_scope_still_delivers_the_skill_bearing_artifact` —
   `skills=False, mcp=True`; asserts the delivery happened and that the
   `desired_skills` it carried are the projection's, not empty. Pins the
   behaviour change most likely to be "optimised" back out. (criterion 2)
3. `test_teclaw_still_updates_the_passport_with_identity_coloured_items` —
   asserts `update_passport` called once with `mcp_codes`, `mcp_items`
   carrying `identity_mode`, and `cli_items`; and that an `mcp=False` scope
   does not call it. (criterion 4)
4. `test_teclaw_empty_scope_delivers_nothing` — `ProjectionScope()`;
   `runtime_syncs` and
   `mcp_projections` both empty, no Passport call. (criterion 8)
5. `test_teclaw_failed_delivery_raises_reconcile_error` — fake
   `sync_runtime` returns `False`; expects `SkillSetRuntimeReconcileError`
   and no Passport call. (criterion 7)
6. `test_teclaw_cleanup_and_non_skill_entry_points_skip_mcp_runtime_delivery`
   — `project_for_cleanup` and `project_mcp_and_cli` on a teclaw Bot:
   `mcp_projections == []`, Passport still updated. (criterion 6)
7. `test_per_domain_engine_keeps_the_scope_split` — an `openclaw` Bot with
   `ProjectionScope(mcp=True, claimed_mcp={"x"})`: `runtime_syncs == []`
   and `len(mcp_projections) == 1`, i.e. the Skill half is still skipped.
   This is the regression guard for criterion 5 and the most important test
   in the group. (criterion 5)

### Existing — must keep passing untouched

- `test_teclaw_v4_rejects_center_without_any_center_runtime_request:2493` —
  still raises before any runtime call. The Center refusal is upstream of
  everything this change touches.
- `test_teclaw_v4_repo_projection_uses_artifact_runtime_not_pool_mapping:2519`
  — already asserts `desired_skills` and empty Pool calls under
  `ProjectionScope.everything()`; unchanged by this work.
- `test_non_skill_projection_never_writes_skill_mappings:2552` — an
  **openclaw** bot (`_RuntimeBots`), so `project_mcp_and_cli` must still call
  `sync_mcp_projection` and still not touch skills. Direct cover for
  criterion 5 on the non-`project` entry point.
- `tests/community/contracts/test_bot_runtime_projector.py` — Service API
  conformance; signatures unchanged, so it should pass with no edit. If it
  fails, the implementation leaked a private parameter into the protocol.
- `tests/community/core/devices/test_teclaw_device_sync.py` — untouched file,
  untouched behaviour; run as evidence the delivery contract is unchanged.

## Validation

```bash
cd src/backend
.venv/bin/python -m pytest \
  tests/community/core/skill_center/ \
  tests/community/contracts/test_bot_runtime_projector.py \
  tests/community/core/devices/test_teclaw_device_sync.py \
  tests/community/core/mcp/ \
  tests/community/architecture/ -q
```

Then the backend SAST/lint gate the pre-push hook runs:

```bash
scripts/ci/python_sast_local.sh   # repo root, invoked by scripts/ci/pre_push.sh
```

**Environment note.** `uv sync --frozen` cannot run here: `uv.lock` pins
`mirrors.aliyun.com`, which this sandbox's network policy answers `403` to.
The venv was populated with `UV_DEFAULT_INDEX=https://pypi.org/simple uv sync`
and `uv.lock` was restored from backup immediately afterwards — **`uv.lock`
must not appear in the final diff.** Check with `git status` before every
commit. Run pytest as `.venv/bin/python -m pytest`, not `uv run pytest`,
which would try to re-lock against the blocked mirror.

Baseline before any edit: 123 passed across
`test_skill_set_management_service.py` (77),
`test_teclaw_device_sync.py` + `test_bot_runtime_projector.py` +
`test_direct_activation_service.py` (46).

## Risks

**A teclaw Bot whose MCP configuration reaches the container only through
`sync_single_mcp`.** This would break if true. It is not: `sync_single_mcp`
ignores `mcp_data` entirely and delivers the composed artifact, into which
`McporterComposer` inlines each MCP's endpoint, `api_key` and headers — the
mechanism `sync_service.py:571` relies on when it notes *"凭据已内联进产物，改
api_key 即改产物字节"*. Covered by the existing
`test_mcp_methods_redeliver_the_whole_artifact_and_return_bool`.

**Losing the `get_mcp_detail` catalogue check.** For teclaw, an unknown
server code currently fails the projection at `sync_mcp_delivery:569-581`.
After this change that check no longer runs on teclaw — but the composer's own
collector raises `McpDetailUnavailableError` on the same condition
(`config_compose/services/collector.py:62`), `_compose_and_deliver` converts
it into `{"success": False}`, and `sync_runtime` returning falsy still raises
`SkillSetRuntimeReconcileError`. Fail-closed is preserved, one layer down.
Test 5 pins the conversion.

**A future engine added to `_WHOLE_ARTIFACT_ENGINES` that is not
whole-artifact.** Mitigated by the docstring naming
`teclaw_device_sync.py` as the authority, and by test 7 pinning per-domain
behaviour for a named engine.

**Ordering.** `sync_mcp_projection`'s "configuration before allow-list"
invariant is not weakened, because on teclaw both were already the same
document; the invariant continues to hold, unmodified, on every per-domain
engine.
