# Design — Evolving the Service-Bot Release Lifecycle

Companion to `spec.md` / `plan.md` in this directory. The spec defines the
public contract; this document defines the internal evolution the public
surface is built on — requested during review: *"take this time for the
service bot lifecycle service to evolve … a lot of things are put into the ext
field of `ac_bot_publish` and it's hard for people to follow; publish_id can
be in different fields in that ext; the logic itself is also a mess."*

This is a design to review, not a description of code already written.

---

## 1. The mess, precisely

### 1.1 `ext` is four different things in one JSON column

A census of every `ext` read/write in `core/service_bot` shows the blob
conflating four concerns with four different lifecycles:

| Concern | Keys | Lifecycle |
|---|---|---|
| **Deployment topology** — what is running where | `binding.{verify,online}`, `publish.{verify,online}`, `restart.{stage}`, `restart.restarting`, `scale.publish_id`, `bot_uuid`, `destroy_publish_id` | Written by every deploy-shaped operation; read by progress sync, retry, restart, rollback, engine-runtime stage resolution, cron |
| **Build artifact** — what to deploy | `migration_path`, `config_artifact`, `build_target_path`, `skills_manifest`, `build_rsync_excludes` | Written once per build; read by both release stages |
| **Failure / recovery bookkeeping** | `error_message`, `source_status`, `retry` | Written on failure; read by retry and the status report |
| **Hitchhiking domain states** | `approval`, `approval_history`, `rollback`, `rollback_restored_from`, `engine_overrides_by_stage`, `data_init_status`, eval keys | Each owned by a different service, sharing the column because it was there |

### 1.2 "publish_id" lives in four homes

The BaaS workflow id for an operation is stored in **four places depending on
which operation minted it**:

1. `ext.publish.{stage}` — first release / upgrade (`publish_ext_mixin.py`,
   `release_stage.py`)
2. `ext.restart.{stage}` — restart (`restart_mixin.py`)
3. `ext.scale.publish_id` — scale (`scale_mixin.py`)
4. `ac_publish_operation.baas_publish_id` — **every** operation, since the #197
   idempotency ledger

Reading it back therefore requires knowing who wrote it —
`restart_mixin.py:525-527` literally compares a ledger row's id against *two*
ext homes to classify it:

```python
if op.baas_publish_id != (ext.get("publish") or {}).get(stage_enum.value): ...
if op.baas_publish_id != (ext.get("restart") or {}).get(stage_enum.value): ...
```

The root cause is historical: the ext stashes predate the ledger. Since #197,
every BaaS mutation opens a ledger row keyed
`(publish_id, operation_kind, stage, attempt)` carrying `baas_publish_id`,
`state`, `params`, `result`. **The ext copies are a legacy shadow of a table
that is already the better answer.**

### 1.3 The logic is a mixin soup

`PublishFlowService` inherits **14 mixins** (`ProgressSyncMixin`,
`RestartMixin`, `ScaleMixin`, `StageStatusMixin`, `RollbackOpsMixin`,
`BaasPublishOpsMixin`, `DeviceBindingMixin`, `PublishExtMixin`,
`EvalPublishMixin`, `UpgradeResolutionMixin`, `RetryOpsMixin`,
`DraftRestoreOpsMixin`, `PublishImagePolicyMixin`, + the facade itself) over
~8,000 lines. The mixins share state through `self._ext_state`,
`self._publish_service`, `self._baas_service` — none of which they declare, so
no mixin can be read, tested, or reasoned about alone, and the effective call
graph only exists at runtime. The extraction of `BuildStageRunner`,
`ReleaseStageRunner` and `PublishOperationRunner` (the
`2026-07-12-publish-flow-service-refactor` work) already proved the better
shape: **explicit collaborators taking their real dependencies** — it just
stopped before the mixins.

### 1.4 The status machine is implicit

`DRAFT → BUILDING → BUILT → VALIDATE_PUB → VALIDATING → ONLINE_PUB → SUCCESS`
(+ `FAILED`, `UPGRADED`, `RELEASED`) exists nowhere as a declaration. It is
smeared across `process()`'s two if-branches, the task handlers in `tasks.py`,
`progress_sync_mixin`'s advance table, and the `source_status` values each
failure path happens to write. Whether a transition is user-driven or
task-driven, and what its failure rollback target is, can only be recovered by
reading all four places.

---

## 2. Design principles

1. **One concept, one home.** A fact is stored in exactly one place; everything
   else derives it. The ledger is the home of operation facts; the release row
   is the home of release facts.
2. **Typed at the boundary, typed inside.** The `ext` JSON is parsed into a
   Pydantic model at the persistence seam and serialized back there; no raw
   dict-key access anywhere else. What today is `(ext.get("binding") or
   {}).get(stage)` in eleven files becomes `facts.bindings.for_stage(stage)`
   in one.
3. **The state machine is data.** Transitions — source, target, driver
   (user/task), failure rollback target — are one declared table. `process()`
   and the task handlers *consult* it; nothing else encodes it.
4. **Explicit collaborators, no mixins.** Every unit takes its dependencies in
   its constructor. The facade composes; it does not inherit.
5. **Wire-compatible evolution.** The stored JSON keys, the internal HTTP
   surface, and the DB schema do not change in this phase; the shape of the
   *code* changes. Schema changes are a later phase with their own out-of-band
   DDL, per the repo's standing rule.
6. **Strangler, not big-bang.** ~8k lines with production crash-safety
   semantics (#197) and three subsequent fix-specs is not rewritten in one PR.
   Each phase leaves the system fully working and fully tested.

---

## 3. Target architecture

New package: `core/service_bot/release/` — the lifecycle's future home. The
name is deliberate: the domain concept is a *release*; "publish" remains the
verb.

```
core/service_bot/release/
├── facts.py          # ReleaseFacts — the typed ext model (§3.1)
├── store.py          # ReleaseStore — the only reader/writer of ext (§3.2)
├── machine.py        # the declared status machine (§3.3)
├── operations.py     # ledger-backed operation queries (§3.4)
└── lifecycle.py      # ReleaseLifecycleService — the public surface's core (§3.5)
```

### 3.1 `ReleaseFacts` — the typed ext

```python
class StageBindings(BaseModel):      # ext["binding"]
    verify: int | None = None
    online: int | None = None
    def for_stage(self, stage: PublishStage) -> int | None: ...

class BuildArtifact(BaseModel):      # migration_path / config_artifact / …
    migration_path: str | None = None        # ARCA (mounted)
    config_artifact: dict | None = None      # teclaw (frozen, possibly offloaded)
    build_target_path: str | None = None
    skills_manifest: dict | None = None
    @property
    def present(self) -> bool: ...           # replaces the copy-pasted
                                             # "neither present → not built" check

class FailureInfo(BaseModel):        # error_message / source_status / retry
    message: str | None = None
    failed_from: PublishStatus | None = None # today's "source_status"
    retry_in_progress: bool = False          # today's "retry"

class ReleaseFacts(BaseModel):
    bindings: StageBindings
    artifact: BuildArtifact
    failure: FailureInfo
    engine_overrides_by_stage: dict[str, dict]
    passthrough: dict[str, Any]              # approval, rollback, eval, data_init …
                                             # — owned by other services, carried
                                             # opaquely, NOT typed here (yet)

    @classmethod
    def from_ext(cls, ext: dict | None) -> "ReleaseFacts": ...
    def to_ext(self) -> dict: ...            # emits the EXACT legacy keys
```

`from_ext`/`to_ext` round-trip the **existing** JSON keys byte-compatibly —
including tolerating the legacy shapes in old rows — so no stored record is
migrated and a rolling deploy can mix old and new code. A round-trip
property test (`to_ext(from_ext(x))` preserves every key it does not own,
verbatim) is the contract.

`passthrough` is the honest boundary of this phase: approval, rollback and eval
state are other services' property. They keep their keys, untouched, and their
typing is Phase 3's work — *typed here now* would mean this design deciding
three other domains' models in passing.

### 3.2 `ReleaseStore` — the only door to `ext`

The evolution of `PublishExtState`, absorbing it. Same persistence semantics
(latest-read-back, CAS status+ext writes via `compare_and_set_status_with_ext`,
the deep-copy snapshot discipline) — but its read surface returns
`ReleaseFacts` and its write surface takes mutators of `ReleaseFacts`:

```python
class ReleaseStore:
    def load(self, publish_id) -> tuple[BotPublishRecord, ReleaseFacts]
    def mutate(self, publish_id, fn: Callable[[ReleaseFacts], None]) -> ReleaseFacts
    def advance(self, publish_id, target, source) -> bool                  # CAS
    def advance_with_facts(self, publish_id, target, source, fn) -> bool   # CAS + ext
```

**Enforcement, not convention:** a new architecture test asserts that outside
`release/facts.py` and `release/store.py`, no module under `core/service_bot`
touches `ext[`, `ext.get(`, or `ext.setdefault(` on a publish record. That is
what makes the census in §1.1 impossible to regrow.

### 3.3 The status machine as data

```python
@dataclass(frozen=True)
class Transition:
    source: PublishStatus
    target: PublishStatus
    driver: Driver                 # USER | TASK
    on_failure: PublishStatus | None   # today's implicit source_status target

RELEASE_MACHINE: tuple[Transition, ...] = (
    Transition(DRAFT,        BUILDING,    USER, on_failure=None),
    Transition(BUILDING,     BUILT,       TASK, on_failure=BUILDING),
    Transition(BUILT,        VALIDATE_PUB,TASK, on_failure=BUILT),
    Transition(VALIDATE_PUB, VALIDATING,  TASK, on_failure=VALIDATE_PUB),
    Transition(VALIDATING,   ONLINE_PUB,  USER, on_failure=None),
    Transition(ONLINE_PUB,   SUCCESS,     TASK, on_failure=ONLINE_PUB),
)
```

- `process()` stops being two hand-written branches: it looks up the
  USER-driven transition for the current status; found → CAS-advance + enqueue,
  not found → describe. The public surface's two writes and the internal
  `process` become provably the same two transitions — the spec's "only two
  user-driven advance points" claim becomes a property of data.
- The failure paths stop hand-writing `ext["source_status"] = <status>.value`
  eight times; the machine names each transition's rollback target once and the
  store's failure write consults it.
- A test asserts the machine and `PublishStatus` stay in agreement (every
  non-terminal status reachable, exactly two USER transitions, terminal set =
  {SUCCESS, UPGRADED, RELEASED, FAILED} minus re-entry rules).

This is a *representation* change: the transitions themselves are today's,
unchanged.

### 3.4 Operation queries: the ledger becomes the single home

New writes of `ext.publish.{stage}`, `ext.restart.{stage}` and
`ext.scale.publish_id` **stop**. The ledger already records every one of these
ids at `ID_RECORDED` — the ext copies are pure duplication.

`release/operations.py` exposes the read side the flow actually needs, backed
by `PublishOperationRepository`:

```python
def latest_release_workflow(publish_id, stage)  -> int | None   # FIRST_RELEASE/UPGRADE
def latest_restart_workflow(publish_id, stage)  -> int | None   # RESTART
def latest_scale_workflow(publish_id)           -> int | None   # SCALE
def is_restart_in_flight(publish_id, stage)     -> bool         # replaces ext.restart.restarting
```

with **read-fallback to the legacy ext keys** for records that predate #197's
ledger (parsed via `ReleaseFacts`' legacy fields, marked deprecated). The
classification dance in `restart_mixin.py:525-527` collapses: a ledger row
*carries* its `operation_kind`; nothing needs to guess it back from which ext
home matched.

`ext.binding` (topology: which device binding serves a stage) **stays in the
release row** — it is a fact about the release, not about one operation, and
it has cross-module readers (`engine_runtime/stage.py`, cron,
`bot_publish_service.get_bot_stage_binding_info`) whose contract does not
move in this phase. It becomes typed (`StageBindings`) but not relocated.

### 3.5 `ReleaseLifecycleService` — the public surface's core

As specified in `plan.md` §5, unchanged in role: target resolution + role bar,
the two precondition-guarded advances, the two reads. What this design changes
is what it is built on — it consults `RELEASE_MACHINE` for the advance points
and `ReleaseStore` for state, so the public surface is a client of the evolved
core, not of the mixin soup.

One simplification falls out: the CAS winner/loser discrimination no longer
needs the message-constant comparison hack (`plan.md` §4). The lifecycle
service drives the USER transition **itself** via `ReleaseStore.advance` (the
same CAS the flow uses) and enqueues the same durable task on a win — the
machine guarantees these are the same two transitions `process()` drives. A
lost CAS is a direct boolean, not a parsed message. `process()` remains for
the internal surface, now delegating to the same machine lookup, so the two
surfaces cannot drift.

### 3.6 Dissolving the mixins (bounded here, executed per-phase)

End state: `PublishFlowService` is a composition facade over explicit units —
`BuildStageRunner` and `ReleaseStageRunner` (exist), plus `ProgressSync`,
`RestartOps`, `ScaleOps`, `RollbackOps`, `RetryOps`, `DraftRestoreOps`,
`EvalOps` as constructor-injected collaborators; the facade keeps its public
method names (the Service API protocol is unchanged) and forwards.

This is mechanical but large (§1.3), so it is **phased per mixin**, and each
conversion's proof is the existing suite for that mixin passing unmodified.

---

## 4. Phasing

| Phase | Content | Ships |
|---|---|---|
| **1 — this change** | `release/` package: `ReleaseFacts` + `ReleaseStore` (absorbing `PublishExtState`), the declared machine consulted by `process()` and the failure writes, ledger-backed operation queries replacing ext id writes/reads, the no-raw-ext architecture test, and the public `publish` category on top | with the public API PR (this SDD) |
| **2 — mixin dissolution** | Convert the mixins to collaborators one at a time (`ProgressSync` first — biggest, most-read); no behaviour change, suites unmodified | follow-up PRs, own spec dir |
| **3 — hitchhikers move out** | Type/relocate `approval`, `rollback`, eval and data-init state with their owning services; shrink `passthrough` toward empty | follow-up, with those services' owners |
| **4 — schema** | If then still warranted: hoist `bindings` and `failure` to real columns with out-of-band DDL and tenant-axis decisions taken together | separate spec; **explicitly not now** |

Phase 1 is sized to this session and removes the two things called out in
review — the untyped ext maze and the scattered publish ids — while phases 2–4
are recorded so the direction survives the session.

**What Phase 1 deliberately does not change:** stored JSON keys (round-trip
compatible), the internal HTTP surface, the DB schema, the durable task
handlers' semantics, the #197 crash-safety properties (the ledger writes are
untouched — only the *duplicate* ext writes stop), and the three fix-specs'
behaviour (retry routing, supersede cleanup, idempotency), all pinned by their
existing suites.

### Rolling-deploy compatibility (Phase 1)

Old and new code briefly coexist against the same rows. Safe because: new code
reads workflow ids ledger-first with ext fallback (old rows and old writers
both satisfied); old code reading rows written by new code finds `binding`,
artifact and failure keys exactly where they always were — the only absent
things are the duplicate id stashes for *operations started by new code*, and
ledger rows exist for those, which old code also consults where it matters
(`restart_mixin` already reads the ledger first). The one-version window where
an old reader's ext-only path misses a new writer's id resolves to "re-derive
from ledger/BaaS", the same path #197 built for crash recovery.

---

## 5. What this changes in `plan.md`

- §4 (message-constant lift) is **withdrawn** — replaced by the machine-driven
  advance in §3.5 of this design. No edit to `publish_flow_service.py`'s
  strings is needed; instead `process()` is refactored to consult the machine
  (same transitions, same messages, internal suite unmodified).
- §5's `ReleaseLifecycleService` moves to `core/service_bot/release/lifecycle.py`
  and gains `ReleaseStore`/`RELEASE_MACHINE` as its substrate.
- The test plan gains: the facts round-trip property test, the machine
  agreement test, the ledger-first/ext-fallback tests, and the no-raw-ext
  architecture test.
- Everything else in `plan.md` (endpoints, preconditions, projections, role
  bar, error mapping, docs) stands as written.

## 6. Open questions for this design's review

1. **Phase 1 blast radius.** Stopping the ext id writes touches
   `publish_ext_mixin`, `release_stage`, `restart_mixin`, `scale_mixin`, and
   their readers (`progress_sync`, `retry_ops`, `rollback_ops`,
   `upgrade_resolution`, `baas_service:3425`, `arca_image_pin`,
   `bot_publish_service:641`). That is the minimal set that kills the
   scattered-id problem for real. The alternative — dual-write during Phase 1
   and stop writes in Phase 2 — is safer but means the mess's headline symptom
   survives this session. Recommendation: stop the writes now, keep the read
   fallback; the ledger has been authoritative since #197 shipped.
2. **Does `process()` refactor in Phase 1 or Phase 2?** Consulting the machine
   from `process()` is a small edit with a big drift-prevention payoff
   (recommendation: Phase 1); dissolving `progress_sync`'s advance table into
   it is Phase 2.
3. **The `release/` package name** — `release/` vs widening the existing
   `publish_flow/`. Recommendation: new package; `publish_flow/` becomes the
   legacy it strangles, and the import direction (`publish_flow` may import
   `release`, never the reverse) is testable.
