# Plan — One Whole-Artifact Delivery per Projection for Teclaw Bots

Implements `spec.md` in this directory.

All paths are relative to `src/backend/src/agentclaw/community/` unless
prefixed with `tests/`, which is relative to `src/backend/`.

## Shape of the change

**One branch, immediately after `_resolve_plan` in `project`, and an early
return.** That line is where the justification becomes true and nowhere
earlier: `_resolve_plan` is what flushes SkillSet configuration into
Installation (via `BotCapabilityStateReader`), so the moment it returns,
persisted desired state is final. On a whole-artifact engine that is the only
fact the runtime needs — the composer reads the database, not the caller's
arguments — so the scope has nothing left to select.

```
async def project(...):
    (service, bot, engine, projection, cli_items, identity_modes) = _resolve_plan(...)

    if runtime_delivers_whole_artifact(engine):        ←  the one branch
        await self._apply_whole_artifact_projection(...)
        return

    ── everything below is today's code, byte-for-byte ──
    if scope.skills or retired_mappings:  _apply_skill_projection(...)
    else:                                 skip-log
    if scope.mcp:                         _apply_non_skill_projection(...)
    else:                                 skip-log
```

Past the early return, **no per-domain code knows teclaw exists**. That is the
half of this that is easy to get wrong: it is not enough to add a branch at the
top if `_apply_skill_projection` keeps its own `engine == "teclaw"` arms. Those
two arms (`:417-423` Center refusal, `:426-432` the `sync_runtime` delivery)
*move* into the new method rather than being called into, which leaves
`_apply_skill_projection` purely per-domain — Pool vs legacy — and lets its now
unused `engine` parameter go.

### New private methods

**`_apply_whole_artifact_projection(...)`** — the whole teclaw path in one
readable method:

```python
# The Center refusal, moved verbatim from _apply_skill_projection:417.
if any(m.corpus == "center" for m in [*projection.skill_mappings, *retired_mappings]):
    raise SkillSetRuntimeReconcileError()

if not (scope.skills or scope.mcp or retired_mappings):
    logger.info(...)          # nothing declared: stay a no-op, as today
    return

# One delivery. Carries both halves by construction — see the class docstring.
if not service.sync_runtime(desired_skills=self._desired_skills(projection)):
    raise SkillSetRuntimeReconcileError()

if scope.mcp:
    self._apply_passport_projection(...)
```

**`_apply_passport_projection(...)`** — a pure extraction of the `try/except`
tail of `_apply_non_skill_projection` (`:549-573`), called unchanged from
there *and* from the new method. Extraction rather than a copy: there must
stay exactly one implementation of the identity-coloured `resource_scope`,
because that block is the fix from
`specs/2026-08-26-mcp-sync-and-passport-regressions` problem 1 and a second
copy could silently drift back to asserting `identityMode: "owner"`.

### Why the branch is here and not elsewhere

Four alternatives were considered.

1. **Early branch in `project`, right after `_resolve_plan` (chosen).** States
   the decision once, at the altitude that spans both halves, at the exact
   point its precondition (flushed desired state) is established.

2. **A widened boolean on the Skill half plus a `deliver_mcp_to_runtime` flag
   threaded into `_apply_non_skill_projection`** — the first draft of this
   plan. Rejected: it spreads one decision across two places, produces the
   unreadable `(whole_artifact and (scope.skills or scope.mcp or
   retired_mappings)) or scope.skills or retired_mappings`, and adds a
   behaviour-controlling boolean parameter that makes one method do two jobs.

3. **Move the decision into `SkillSetService.sync_mcp_projection`**, mirroring
   its docstring — *"how many device writes an MCP projection takes, and in
   what order, is decided by the service that owns device resolution"*.
   Rejected: that method owns how many device writes *one MCP projection*
   costs. The question here is how many projections to issue at all, which
   spans both halves; `SkillSetService` cannot answer it without knowing what
   the Skill half already did.

4. **Deduplicate inside `TeclawDeviceSyncService`** (compose once, suppress
   identical repeats). Rejected: a cache keyed on artifact bytes with a
   correctness-critical invalidation story, to suppress calls the caller
   should not have made. Not issuing the call is simpler than making it cheap.

### Which entry points get it

`project` only — it is the only one a teclaw Bot reaches:

- **`project_for_cleanup` has no production caller.** `grep` over
  `src/backend/src` finds it only in `api/bot_runtime_projector.py:43`,
  `runtime_projection_contract.py:131` and its own implementation. Adding
  whole-artifact handling to a method nothing calls is speculation.
- **`project_mcp_and_cli` has exactly one**, at
  `di/modules/skill_center_module.py:926`, feeding `SkillSymlinkListener`'s
  `runtime_non_skill_reconcile`. That fires only when
  `_resolve_desktop_layout_authority` returns `"transition"`, and it returns
  non-`None` only for `bot_type == "desktop"`. Teclaw is additionally never
  Pool-capable — `CurrentRuntimeLayoutProbeService.probe_bot` answers
  `engine_has_no_filesystem_pool_layout` (`runtime_layout_probe.py:83`).
  Beyond unreachable, the method's premise — *"a cutover task exclusively owns
  Skill mappings"* — is incoherent for a whole-artifact engine, where MCP
  cannot be delivered without redelivering Skills. "Collapse to one delivery"
  would be the wrong answer there even if it were reachable.

Both keep a one-line comment recording that they are per-domain-only paths and
why, so the next reader does not re-derive the reachability argument. Neither
gets code.

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
for a scheduling decision, and the fact is consumed only here.

The two surviving `engine == "teclaw"` checks — `snapshot_skill_mappings:118`
and `_build_plan:332` — are **left alone**. They test a different property
("has no Center request contract"), which happens to be true of the same
engine today but is not the same statement; routing them through the new
predicate would assert that any future whole-artifact engine also lacks a
Center contract, which nothing establishes.

## Changes

### 1. `core/skill_center/runtime_projection_contract.py`

Add `_WHOLE_ARTIFACT_ENGINES` and `runtime_delivers_whole_artifact(engine)` as
above; export the function in `__all__`.

No change to `ProjectionScope`'s fields, defaults, `everything()` or
`inverted()`. Add one paragraph to its class docstring recording that its
guarantees ("a single-MCP add stays a single device write", `claim_all_mcp`'s
empty-container premise, the delivery-before-declaration ordering) describe
engines with separate runtime endpoints, and pointing at the predicate for the
other case — so the next reader does not rediscover spec Problem 3.

### 2. `core/skill_center/services/bot_runtime_projector.py`

**2a.** Import `runtime_delivers_whole_artifact` beside the existing
`ProjectionScope` import.

**2b.** `project` (`:124`): insert the branch and early return immediately
after `_resolve_plan`, above the existing comment block. The existing comment
block and both scope-driven halves stay exactly as they are — the diff here is
purely an insertion, so `git diff` shows no modified line in the per-domain
path.

**2c.** Add `_apply_whole_artifact_projection`, holding what moved out of
`_apply_skill_projection` plus the scoped Passport call, as sketched above.
The moved Center-refusal comment travels with it.

**2d.** Extract `_apply_passport_projection` from `_apply_non_skill_projection`
(`:549-574`) and call it from the tail of that method. Signature:
`(*, identity_modes, engine, bot_id, owner_id, projection, effective_cli_items)`;
synchronous, since nothing in the block awaits.

**2e.** `_apply_skill_projection`: delete both teclaw arms (`:417-423`,
`:426-432`) and drop the `engine` parameter, now unused. Update its one
call site in `project`. Its docstring/comments gain nothing — the method
simply no longer has a teclaw case to explain.

**2f.** `project_mcp_and_cli` (`:188`) and `project_for_cleanup` (`:218`):
one comment each recording per-domain-only reachability, with the evidence.
No code change.

**2g.** Class docstring: extend the "Resolving and applying are separated by
the `ProjectionScope`" paragraph with the whole-artifact case, in the register
of the surrounding prose.

### 3. No DI, no boundary, no contract changes

- No constructor parameter, so `di/modules/skill_center_module.py` is untouched.
- The new import is intra-module (`skill_center` → `skill_center`), so
  `core/skill_center/README.md`'s Context Boundary needs no new entry and the
  Rule-22 architecture test is unaffected.
- `BotRuntimeProjectorProtocol` and `api/bot_runtime_projector.py` keep their
  signatures — every new method is private.
- `SkillSetService`, `MCPSyncService`, `TeclawDeviceSyncService`,
  `ConfigComposer` and every DI module are unmodified.

## Tests

### New — `tests/community/core/skill_center/test_skill_set_management_service.py`

The projector's fakes already live here (`_RuntimeFactory`,
`_TeclawRuntimeBots`, `_TeclawRuntimeSkills`, `_McpInstallations`,
`_RuntimePool`, `_RuntimeLayouts`, `_RuntimePassport`,
`_RuntimeCallerIdentity`); the two existing teclaw projector tests are at
`:2493` and `:2519`. New cases go beside them.

`_RuntimeFactoryService` (`:474`) records `desired_skills` and `mcp_codes` as
last-write-wins **scalars**, so neither can distinguish one call from four. It
already records `deliveries: list[tuple[frozenset, frozenset]]` — one append
per `sync_mcp_delivery` — with a comment explaining why a list and not a
scalar. Follow that idiom:

- add `runtime_syncs: list[list[dict]]`, appended in `sync_runtime` (`:484`);
- add `mcp_projections: list[tuple[frozenset[str], frozenset[str], set[str]]]`,
  appended at the top of `sync_mcp_projection` (`:498`) **before** it
  delegates, so the deliver-before-declare composition at `:512-514` that
  existing tests assert on is preserved verbatim.

Keep `desired_skills`, `mcp_codes`, `deliveries` and `collect_calls` exactly as
they are, so no existing assertion changes. Leave the unrelated
`sync_mcp_projection` stub at `:2865` alone.

Cases, one per acceptance criterion:

1. `test_teclaw_projects_the_whole_artifact_once_per_scope_shape` —
   parametrised over `ProjectionScope(skills=True)`,
   `ProjectionScope(mcp=True, claimed_mcp={"x"})`,
   `ProjectionScope(mcp=True, released_mcp={"x"})`,
   `ProjectionScope(skills=True, mcp=True, claimed_mcp={"x"})` and
   `ProjectionScope.everything()`. Each: `len(runtime_syncs) == 1` and
   `mcp_projections == []`. (criteria 1, 2, 3)
2. `test_teclaw_mcp_only_scope_still_delivers_the_skill_bearing_artifact` —
   `skills=False, mcp=True`: one delivery, and the `desired_skills` it carried
   are the projection's, not empty. Pins the behaviour most likely to be
   optimised back out. (criterion 2)
3. `test_teclaw_still_updates_the_passport_with_identity_coloured_items` —
   one `update_passport` whose `resource_scope` carries `mcp_codes`,
   `mcp_items` with resolved `identity_mode`, and `cli_items`; and an
   `mcp=False` scope makes no Passport call. Guards the extraction in 2d.
   (criterion 4)
4. `test_teclaw_empty_scope_delivers_nothing` — `ProjectionScope()`: both
   lists empty, no Passport call. (criterion 8)
5. `test_teclaw_failed_delivery_raises_reconcile_error` — `sync_runtime`
   returns `False`: `SkillSetRuntimeReconcileError`, no Passport call.
   (criterion 7)
6. `test_per_domain_engine_keeps_the_scope_split` — an **openclaw** Bot with
   `ProjectionScope(mcp=True, claimed_mcp={"x"})`: `runtime_syncs == []` and
   `len(mcp_projections) == 1`, with `claimed` still guarded down to the
   projected set. The regression guard for criterion 5 and the most important
   test here. (criterion 5)

No test is added for `project_mcp_and_cli` / `project_for_cleanup` on teclaw:
neither is reachable, and a test asserting behaviour of an unreachable path
would pin the wrong contract. Criterion 6's other half — no teclaw branch left
downstream — is checked by reading the diff, and enforced by 2e deleting the
`engine` parameter, which makes a reintroduced branch a syntax error rather
than a silent regression.

### Existing — must keep passing untouched

- `test_teclaw_v4_rejects_center_without_any_center_runtime_request:2493` —
  the Center refusal must still fire before any runtime call, now from its new
  home. This is the direct test of the move in 2c/2e.
- `test_teclaw_v4_repo_projection_uses_artifact_runtime_not_pool_mapping:2519`
  — asserts `desired_skills` and empty Pool calls under
  `ProjectionScope.everything()`.
- `test_non_skill_projection_never_writes_skill_mappings:2552` — an
  **openclaw** bot on `project_mcp_and_cli`; direct cover that 2d's extraction
  changed nothing.
- `tests/community/contracts/test_bot_runtime_projector.py` — Service API
  conformance; signatures unchanged, so it passes with no edit.
- `tests/community/core/devices/test_teclaw_device_sync.py` — untouched file
  and behaviour; run as evidence the delivery contract did not move.

If any of these needs an edit, the implementation moved something it should
not have — fix the implementation, not the test.

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

Then `scripts/ci/python_sast_local.sh` from the repo root — the gate
`scripts/ci/pre_push.sh` runs in lint-only mode.

**Environment.** `uv sync --frozen` cannot run here: `uv.lock` pins
`mirrors.aliyun.com`, which this sandbox's network policy answers `403` to.
The venv was populated with `UV_DEFAULT_INDEX=https://pypi.org/simple uv sync`
and `uv.lock` restored from backup immediately — **`uv.lock` must not appear
in the diff.** Check `git status` before every commit. Run pytest as
`.venv/bin/python -m pytest`, never `uv run pytest`, which re-locks against
the blocked mirror.

Baseline before any edit: 123 passed across
`test_skill_set_management_service.py` (77), and
`test_teclaw_device_sync.py` + `test_bot_runtime_projector.py` +
`test_direct_activation_service.py` (46).

## Risks

**The extraction in 2d silently changes the Passport payload.** This is the
highest-risk step, because that block is the security-relevant fix from the
2026-08-26 spec (problem 1: skill-set mutations resetting every MCP's
`identityMode` to `owner`). Mitigated by making it a move with no edits to the
body, by test 3 asserting `mcp_items` still carry `identity_mode`, and by
`test_non_skill_projection_never_writes_skill_mappings` passing unedited.

**A teclaw Bot whose MCP configuration reaches the container only through
`sync_single_mcp`.** Would break this change if true; it is not.
`sync_single_mcp` ignores `mcp_data` and delivers the composed artifact, into
which `McporterComposer` inlines each MCP's endpoint, `api_key` and headers —
the mechanism `sync_service.py:571` relies on when it notes *"凭据已内联进产物,
改 api_key 即改产物字节"*. Covered by the existing
`test_mcp_methods_redeliver_the_whole_artifact_and_return_bool`.

**Losing the `get_mcp_detail` catalogue check on teclaw.** An unknown server
code currently fails the projection at `sync_mcp_delivery:569-581`. After this
change that check no longer runs for teclaw — but the composer's collector
raises `McpDetailUnavailableError` on the same condition
(`config_compose/services/collector.py:62`), `_compose_and_deliver` converts it
to `{"success": False}`, and a falsy `sync_runtime` still raises
`SkillSetRuntimeReconcileError`. Fail-closed is preserved one layer down;
test 5 pins the conversion.

**A future engine added to `_WHOLE_ARTIFACT_ENGINES` that is not
whole-artifact.** Mitigated by the docstring naming `teclaw_device_sync.py` as
the authority, and by test 6 pinning per-domain behaviour for a named engine.

**Ordering.** `sync_mcp_projection`'s "configuration before allow-list"
invariant is not weakened: on teclaw both were always the same document, and
on every per-domain engine the code path is untouched.
