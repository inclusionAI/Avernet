# Plan: RSI-04 direct delivery of a compiled revision

Spec: `spec.md` beside this file. Paths are under
`apps/backend/src/agentclaw/community/` unless they start with `apps/`,
`docs/` or `tests/` (the last meaning `apps/backend/tests/community/`).

## Shape

```
POST …/genome/promotions
  GenomePromotionService.promote
    resolve → compile → drift (automated) → applier.preflight          nothing written
    promotions.insert(row: pending)                                    refs untouched
    applier.start(bot, promotion_id, principal, audit)  ──▶ ApplyRunService.start_apply(state=genome(pid))
       ApplyInProgressError → row apply_failed, raise 409               (lock + GenomeCompiler.check, sync)
       other exception      → row apply_failed (type only)
       accepted             → row applying, apply_id
  ← 201 Promotion(status=applying, apply_id)

apply task (durable; ApplyRunService.run_apply_task → _run)
  compile  (GenomeCompiler: promotion row → revision → DesiredState; no status check)
  apply    (orchestrator → report)
  applies.finish(report)
  publish: completion_handlers[state.kind].on_finished(axes, state, outcome) ──▶ PromotionSettler.settle
  locks.release

task queue gives up on the apply task (deadline in claim scan / on reschedule, or Fail)
  apply task's on_give_up(task):
    applies.finish(FAILED) only if the report is still RUNNING; locks.release(token); publish

PromotionSettler.settle(axes, promotion_id, outcome)
  SUCCEEDED / PARTIAL → promotions.settle_moving_refs(...)  one transaction:
       CAS active (expected = row.from_revision_id) + previous, revision promoted,
       ref.labels = [] | ["partial"], row applied|applied_partial        (CAS lost → row apply_failed)
  FAILED → promotions.finish(apply_failed, reason)
  row already terminal → no-op

```

The apply engine still names no producer: it calls "the handler registered for
this state kind", exactly as it picks a compiler.

## 1. Apply engine: the completion seam

New `core/apply_engine/completion.py`:

```python
@dataclass(frozen=True)
class ApplyOutcome:              # what a producer learns when its apply ends
    apply_id: str
    status: ApplyStatus          # SUCCEEDED | PARTIAL | FAILED, never RUNNING
    summary: str                 # one line: failed-entry count or the failure note; no raw exception text

@runtime_checkable
class ApplyCompletionHandler(Protocol):
    @abstractmethod
    def on_finished(self, *, env: str, entity_id: str, bot_id: str, state: StateRef, outcome: ApplyOutcome) -> None: ...

StateCompletionHandlers = Dict[str, ApplyCompletionHandler]   # map multibinding keyed by state kind
def outcome_of(report: ApplyReport) -> ApplyOutcome: ...       # the one summariser
```

- `ApplyRunService.__init__` takes `completion_handlers: StateCompletionHandlers`
  (required, like `compilers`). A kind without a handler (the Manifest) gets
  nothing called.
- `_run`'s `finally`: after `applies.finish` and **before** `locks.release`,
  call the handler (so a promotion that follows sees settled refs). A handler
  that raises does not lose the notice: the lock is still released, then the
  exception leaves `run_apply_task`, which the queue treats as a retry.
- **A re-run whose report is already terminal only publishes.**
  `run_apply_task` first reads the apply record; when it is terminal (a run
  wrote it, then the publish failed or the worker died before publishing),
  it publishes `outcome_of` the stored report and completes, applying
  nothing. Publishing is idempotent (settling twice is a no-op), so every
  path that ends an apply converges on exactly one settled promotion. One
  extra indexed read per task run.
- `terminate_unstartable` (run/termination.py) gains an optional handler
  argument and calls it the same way. `terminate_on_launch_failure` runs on
  the caller's thread inside `start_apply`, whose exception reaches the
  promotion service directly, so it calls nothing.
- A worker that dies mid-run publishes nothing; the queue re-claims the task
  and the re-run either applies again (report still `RUNNING`) or only
  publishes (report terminal). The deadline case publishes through §1b.
- DI: `apply_engine_module` (or wherever `StateCompilers` gets its empty
  base) binds an empty `StateCompletionHandlers` map;
  `bot_management_module.bot_config_manifest_apply_service` passes it.
- `ApplyRunServiceProtocol` is unchanged. `apply_engine/README.md` boundary
  lists `ApplyCompletionHandler`, `ApplyOutcome`, `StateCompletionHandlers`.

## 1b. Task queue: the give-up hook

Today the queue gives up on a task in three places and runs no code for it:
the claim scan's retire `UPDATE … TIMED_OUT` (`claim_batch`, step 2b), a
`reschedule` whose `run_at` would overshoot the deadline (→ `TIMED_OUT`), and
a handler's `Fail` (→ `FAILED`).

- `TaskHandler` (`core/task_queue/types.py` / `services/registry.py`) gains an
  optional `on_give_up(task: TaskRecord) -> None`; a handler without it is
  unaffected.
- Repository (`core/repository/{protocols,implementations}/platform/task_queue.py`):
  a new `claim_batch_reporting_retired(...) -> ClaimOutcome(won, retired)`
  carries the rows the claim scan retired (step 2b's `UPDATE` already runs per
  candidate id; a row whose retire landed is read back). `claim_batch` keeps
  its signature and returns `.won`, so its existing callers and tests are
  untouched. `reschedule` keeps returning `bool`; the worker learns a
  reschedule ended in `TIMED_OUT` with one `get_by_id`, only for a task type
  that has a hook.
- Worker (`services/worker.py`): after each give-up it calls the handler's
  `on_give_up` with the record (outside any DB session); a raising hook is
  logged and swallowed — the task is already terminal.
- `README.md` (task queue): a "Give-up hook" paragraph.
- Apply task (`apply_engine/apply_task.py`): its handler implements
  `on_give_up` via a new `ApplyRunService.give_up(payload)`: read the apply
  record; if still `RUNNING`, finish it `FAILED` with the note "the apply task
  was given up (deadline elapsed)"; release the lock by the payload's token
  (a no-op when already released); publish `outcome_of` the (now) terminal
  report to the kind's handler — also when a run had already written it,
  since that run may have died before publishing; settling is idempotent.
  `ApplyRunServiceProtocol` is not widened: `give_up` is on the concrete
  service the handler already holds.

Hot-path cost: one extra handler call per apply; for the genome kind it is
one promotion read plus one transaction (§3). No extra query for Manifest
applies.

## 2. Genome storage (DDL edited in place, `sql/2026_10_19_bot_genome.sql`)

- `ac_genome_promotion`: `status` comment `pending/applying/applied/applied_partial/apply_failed`;
  new `apply_id varchar(32) DEFAULT NULL COMMENT 'the delivery apply'`; `created_at`
  keeps meaning "when the promotion was asked for"; new `settled_at datetime DEFAULT NULL`.
- `ac_genome_ref`: new `labels varchar(256) NOT NULL DEFAULT '[]' COMMENT 'JSON list of strings; today only "partial" on active'`.
  Every ref move writes it (`[]` unless the mover passes labels), so a later move never inherits a stale label.
- ORM (`bot_genome/repository/models.py`), rows, `Promotion` dataclass
  (`apply_id`, `settled_at`), `GenomeRefRow`/ref read model (`labels: tuple[str, ...]`, a closed `RefLabel` enum).
  The DDL/ORM contract test keeps them pinned.

Repository (`core/repository/{protocols,implementations}/bot/genome.py`):

- `GenomePromotionRepositoryProtocol.start(...)` (the move-refs-first write)
  is **replaced** by:
  - `insert(row)` (exists) — the `pending` row.
  - `mark_applying(axes, promotion_id, apply_id) -> row | None` — conditional
    on `status='pending'`.
  - `settle(axes, promotion_id, *, status, apply_detail, move_refs: RefMove | None, settled_at) -> SettleResult`
    — one transaction, conditional on `status in ('pending','applying')`:
    when `move_refs` is given, `_move_pair` (existing helper) with
    `expected_active=row.from_revision_id`, sets `active`'s `labels`, marks the revision `promoted` (from any `PROMOTABLE` status);
    a lost CAS or status change rolls back and the same call writes
    `apply_failed` with "active moved during the apply" instead. Answers
    `settled | already_settled | ref_conflict`.
  - `finish` (exists) stays for the pre-apply failures (lock busy, start
    error); conditioned on a non-terminal status.
- Round trips: settle = 1 read + 1 transaction of ≤5 statements, once per
  genome apply.

## 3. Genome promotion (`core/bot_genome/promotion/`)

- `applier.py`: `RevisionApplier` becomes
  ```python
  kind: str
  def preflight(self, bot, state: DesiredState) -> None                    # kept; refuses nothing here, #3212 uses it
  def start(self, bot, *, promotion_id, principal_id, audit_actor) -> str   # the apply id; raises ApplyBusy
  ```
  `PendingDeliveryApplier` is deleted.
- New `direct_applier.py`: `DirectApplier(RevisionApplier)`, `kind="direct"`,
  over `ApplyRunServiceProtocol` (imported from `api/apply_run_service.py`).
  `preflight` refuses nothing today (service bots go to their draft; #3212
  will refuse verify/online here). `start` calls
  `start_apply(entity_id, bot_id, bot=<record>, owner_id, actor_id=principal_id,
  audit_actor=…, state=genome_state(pid), trigger="genome_promotion", phase=None)`
  and maps `ApplyInProgressError` to the genome's `ApplyBusy`.
  The bot record comes from the bot repository the applier holds (the run
  service needs it for capabilities); the trigger value is added to the
  apply engine's trigger vocabulary (`triggers.py`) if it is a closed set.
- New `settle.py`: `PromotionSettler` over the promotion repository only
  (no applier → no DI cycle with `ApplyRunService`):
  `settle(axes, promotion_id, outcome)` maps SUCCEEDED/PARTIAL/FAILED to §2's
  `settle`. Also `GenomeCompletionHandler(ApplyCompletionHandler)` adapts
  `on_finished` to it (reads `state.reference` as the promotion id).
- `service.py` `promote`: order per Shape; principal = `actor.audit` user id
  for `USER`, `bot.owner_id` otherwise; audit label = `actor.audit`.
  `get_promotion`/`list_promotions` only read. Before inserting its row,
  `promote` marks `apply_failed` any of the bot's rows still `pending` with
  no apply id (a request that died between its two writes; nothing will
  publish for it) — one indexed query, usually empty.
- `errors.py`: `ApplyBusy` (→ 409 `apply_in_progress` in `errors_genome.py`
  and the domain-error status map test).
- `models.py`: `PromotionStatus` closed set; `Promotion.apply_id`,
  `settled_at`; `ApplyResult` removed if nothing reads it after the change.

## 4. Genome compiler (`core/bot_genome/compile.py`)

- No status check: `_revision_of` reads the promotion row and returns its
  `revision_id`; a missing row is refused (`check`: `RevisionNotFound`;
  `compile`: `CompileRefused`). `_DELIVERABLE`, the superseded rule and
  their tests go. The compile stays pure, so a re-run compiles the same
  state.
- `_skill_source`: `CenterSkill` → `CenterSkillSource()`.

## 5. Center version removal (RSI-02 contract)

- `core/bot_genome/contract/genome-revision.schema.json`: the center shape is
  `required: ["name","origin"]`, `center_version` gone (+ the contract copy
  under `apps/backend/api-contracts/genome/v1/`, kept equal by
  `test_contract_copies.py`).
- `spec_models.py` (`CenterSkill`, `CenterVersion` constraint), `capture/capture.py`
  (`CapturedSkill.center_version`, the empty-version refusal), `capture/bot_state.py`,
  `diff.py`/`gene_paths.py` if they read it, examples under
  `api-contracts/genome/v1/examples/` and their revision ids, `example_blobs.py`.
- `apply_engine/desired_state.py`: `CenterSkillVersion(version)` →
  `CenterSkillSource()` (no fields; the name is on `DesiredSkill`).

## 6. Skills materialiser: Center and repo delivery

- New narrow port `core/ports/shared_skill_port.py`:
  ```python
  class SharedSkillPort(Protocol):
      @abstractmethod
      def find_center_skill(self, *, bot_id, owner_id, actor_id, name) -> str: ...   # skill id
      @abstractmethod
      def find_repo_skill(self, *, bot_id, owner_id, actor_id, path) -> str: ...
  class SharedSkillNotFound(Exception); class SharedSkillAmbiguous(Exception)
  ```
  Implemented by `SkillQueryService` (it owns Local/Repo/Center addressing and
  visibility): Center → candidate rows by exact runtime name with a
  `center://` locator in this tenant/env (new small repository query in a new
  module beside `skill.py`, which is already over the line limit and
  allowlisted), each passed through the existing `_resolve` (view + Center
  access for the principal), 0/1/many; Repo → `get_by_git_path("git://"+path)`
  then `_resolve`. A fake implements the port in `tests/.../apply_engine/_fakes.py`
  with a reflection test.
- `materialisers/skills.py`: `resolve` maps a Center/Repo entry to
  `Intent(name, _SharedSkill(skill_id))` or a `ResolveFailure` with the
  readable reason; `plan` classifies it like a package (`updated` when the
  name is active, else `created`); `write` calls
  `activation.claim_apply_skill(skill_id=…)`. `_NotDelivered` and the
  #3103 docstring paragraph go.
- Rename first, as its own commit with no behaviour change (review: these
  are apply scope): `claim_manifest_skill` → `claim_apply_skill`,
  `remove_manifest_skill` → `remove_apply_skill`, `claim_manifest_mcp` →
  `claim_apply_mcp`, `remove_manifest_mcp` → `remove_apply_mcp`,
  `manifest_direct_mcp_codes` → `apply_direct_mcp_codes`,
  `ManifestDesiredStateCommittedError` → `ApplyDesiredStateCommittedError`,
  `manifest_direct_claim_commands.py` → `apply_claim_commands.py`, across the
  activation port, the Skill Center service and protocol, the repository, the
  apply-engine delegates and their tests (~85 references, 14 files). The
  persisted audit action string `skill_manifest_direct_claim` keeps its value.
- Skill Center claim: `apply_claim_commands.claim_apply_skill`
  accepts a target whose locator is `center://…` or `git://…` (shared, no
  bot ownership check — access was established by `_resolve_skill`) besides
  the bot's own `local://…`. Same same-name Set/Default detach logic. The
  `skill_center/AGENTS.md` passages on these claims and its contract test
  are updated.
- `MaterialiserPorts` / both delivery bundles carry the `SharedSkillPort`;
  the platform (teclaw-platform) road gets the same port.

## 7. HTTP (`adapters/http/openapi_v1/bots/`)

- `schemas_genome.py`: `PromotionPayload.status` literal gains `pending`,
  `applied_partial`; `apply_kind` dropped (one seam); new `apply_id`,
  `settled_at`; ref payload gains `labels` (a list). The
  `created_at` description drops "when the refs moved". Center skill entry
  schema loses `center_version`.
- `genome.py` promote docstring/OpenAPI text: 201 with `applying`; 409
  `apply_in_progress`; the refs move when the apply ends.
- `apps/gateway/configs/schemas/bots.openapi.json` regenerated only if the
  genome routes are published there (check; otherwise nothing).

## 8. Docs

`work-items.md` (+zh-CN): RSI-03 "Left to RSI-04" closed; RSI-04 shipped
note; new follow-up pointer to #3212. `01-genome.md` (+zh-CN) §2 SkillEntry,
§5.5, §11 (`RevisionApplier`, promote docstring), §13.3; `08-promotion.md`
(+zh-CN) §2.4 statuses and example, §7.1 order, §8.4, §10 examples, §13 P-2
decided (pointer to #3212); contract `README.md` §4.1/§7 and
`manifest-v1-compatibility.md`; `core/bot_genome/README.md`,
`core/apply_engine/README.md`, `core/ports/README.md`,
`skill_center/AGENTS.md`; `CONTEXT-MAP.md` (genome → apply engine edge now
live, completion seam). ADR: none new — the decisions extend ADR 0020/0022
without reversing them; the P-2 decision is recorded in 08-promotion §13.

## 9. Tests

| Area | Tests |
|---|---|
| completion seam | `apply_engine/test_apply_service_lifecycle.py`: handler called once on SUCCEEDED/PARTIAL/FAILED/CompileRefused/engine exception/unstartable, before lock release; raising handler swallowed; Manifest kind calls nothing; `give_up` finishes a RUNNING report FAILED, releases, publishes, and does nothing for a terminal one |
| task queue | `repository/platform/test_task_queue_repository.py`: `claim_batch_reporting_retired` returns the retired rows; `claim_batch` unchanged; worker tests: hook called on claim-scan retire, reschedule→TIMED_OUT and Fail, never on success; a raising hook swallowed; a handler without a hook unaffected |
| repository | `test_genome_repository.py`: insert pending, mark_applying conditional, settle moves pair + labels + promoted, CAS-lost → apply_failed, double settle no-op, DDL/ORM pin |
| promotion | `bot_genome/test_promotion_service.py` (rewritten order): lock busy → 409 + row apply_failed + refs untouched; start error; principal for automated actors; a stale `pending` row is failed by the next promotion |
| settler | new `bot_genome/test_promotion_settle.py` |
| applier | new `bot_genome/test_direct_applier.py` over a fake run service |
| compiler | `bot_genome/test_compile.py`: any existing promotion compiles; a missing one is refused; status tests removed |
| center removal | `test_contract_models.py`, `test_capture.py`, `test_bot_state.py`, `contracts/test_bot_genome_schema.py`, equivalence test |
| skills | `apply_engine/test_skills_materialiser.py`: center/repo found/not found/ambiguous; claim called; plan outcomes; removal of a shared asset never deletes |
| skill center | claim of center/git target (`tests/community/core/skill_center/...` claim command tests), `SkillQueryService` lookup tests |
| HTTP | `test_genome_routes.py`, `endpoints/test_openapi_genome.py`, domain-error status map |
| DI | `di/test_apply_engine_module_wiring.py`, genome module wiring: `RevisionApplier` is `DirectApplier`, the genome handler is in the map |
| end to end (in-process) | new `bot_genome/test_promotion_delivers.py`: a fake-backed run service + real promotion service + real settler: promote → apply → settled `applied`, refs moved; partial; failed |

Singlebox (done-when): capture a personal bot, edit, capture, edit, capture;
promote r3, check `drift` is `None`; promote r1, check resources and script
removed and `drift` `None`. Recorded in the PR's Validation section; if the
container cannot run singlebox, that is stated with what was run instead.

## Risks

- **Revision ids change** for revisions with Center skills; tables are not
  deployed (same footing as #3072/#3083).
- **`claim_apply_skill` widening** touches the Skill Center control plane;
  guarded by its own tests and `_resolve_skill`'s access check.
- **Owner as principal** lets an automated actor do what the owner may; the
  gate and drift checks before it are the control.
- **A worker dying between `applies.finish` and the publish** leaves the
  report terminal and the promotion `applying`; the re-claimed task sees the
  terminal report and only publishes; a given-up task publishes from
  `give_up`. Settling twice is a no-op.
- **The give-up hook touches the shared task queue**; it is optional per type
  and every existing repository and worker test keeps its expectations.
