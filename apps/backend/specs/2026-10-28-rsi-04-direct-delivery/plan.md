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

apply worker (ApplyRunService._run / run_apply_task)
  compile (GenomeCompiler: promotion pending|applying, active == from_revision) → orchestrator → report
  applies.finish(report)
  completion_handlers[state.kind].on_finished(axes, state, report)    ──▶ PromotionSettler.settle
  locks.release

PromotionSettler.settle(axes, promotion_id, outcome)
  SUCCEEDED / PARTIAL → promotions.settle_moving_refs(...)  one transaction:
       CAS active (expected = row.from_revision_id) + previous, revision promoted,
       ref.labels = [] | ["partial"], row applied|applied_partial        (CAS lost → row apply_failed)
  FAILED → promotions.finish(apply_failed, reason)
  row already terminal → no-op

GET …/promotions/{id} / list
  row applying + its apply terminal (incl. abandoned → FAILED) → settle, then answer
  row pending older than the apply lock TTL with no apply_id      → apply_failed, then answer
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
def outcome_of(report: ApplyReport) -> ApplyOutcome: ...       # the one summariser, also used by the read-time settle
```

- `ApplyRunService.__init__` takes `completion_handlers: StateCompletionHandlers`
  (required, like `compilers`). A kind without a handler (the Manifest) gets
  nothing called.
- `_run`'s `finally`: after `applies.finish` and **before** `locks.release`,
  call the handler (so a promotion that follows sees settled refs). A handler
  that raises is logged and swallowed — the report is already terminal and the
  read-time settle repairs the promotion.
- `terminate_unstartable` (run/termination.py) gains an optional handler
  argument and calls it the same way. `terminate_on_launch_failure` runs on
  the caller's thread inside `start_apply`, whose exception reaches the
  promotion service directly, so it calls nothing.
- The abandoned case (dead worker; `_to_report` derives FAILED at read) has no
  writer to hook; the genome settles it at read time (§3).
- DI: `apply_engine_module` (or wherever `StateCompilers` gets its empty
  base) binds an empty `StateCompletionHandlers` map;
  `bot_management_module.bot_config_manifest_apply_service` passes it.
- `ApplyRunServiceProtocol` is unchanged; `get_apply` already gives the
  genome what the read-time settle needs. `apply_engine/README.md` boundary
  lists `ApplyCompletionHandler`, `ApplyOutcome`, `StateCompletionHandlers`.

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
- Round trips: settle = 1 read + 1 transaction of ≤5 statements; the
  read-time settle adds one `get_apply` (1 query) only for rows still
  `applying`.

## 3. Genome promotion (`core/bot_genome/promotion/`)

- `applier.py`: `RevisionApplier` becomes
  ```python
  kind: str
  def preflight(self, bot, state: DesiredState) -> None
  def start(self, bot, *, promotion_id, principal_id, audit_actor) -> str   # the apply id; raises ApplyBusy
  def outcome(self, bot, apply_id) -> ApplyOutcome | None                   # None while running
  ```
  `PendingDeliveryApplier` is deleted.
- New `direct_applier.py`: `DirectApplier(RevisionApplier)`, `kind="direct"`,
  over `ApplyRunServiceProtocol` (imported from `api/apply_run_service.py`).
  `preflight` refuses nothing today (service bots go to their draft; #3212
  will refuse verify/online here). `start` calls
  `start_apply(entity_id, bot_id, bot=<record>, owner_id, actor_id=principal_id,
  audit_actor=…, state=genome_state(pid), trigger="genome_promotion", phase=None)`
  and maps `ApplyInProgressError` to the genome's `ApplyBusy`. `outcome`
  reads `get_apply` and returns `outcome_of(report)` for a terminal one.
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
  `get_promotion`/`list_promotions` run the read-time settle for rows that
  are `applying` (via `applier.outcome`) or stale `pending` (older than
  `APPLY_LOCK_TTL_SECONDS`, no apply id). `list` settles at most the page's
  rows. `promote` settles the bot's earlier in-flight promotions the same
  way before inserting its own row.
- `errors.py`: `ApplyBusy` (→ 409 `apply_in_progress` in `errors_genome.py`
  and the domain-error status map test).
- `models.py`: `PromotionStatus` closed set; `Promotion.apply_id`,
  `settled_at`; `ApplyResult` removed if nothing reads it after the change.

## 4. Genome compiler (`core/bot_genome/compile.py`)

- `_DELIVERABLE = {"pending", "applying"}`; the "superseded" rule becomes:
  `active` must still point at `row.from_revision_id` (or not exist when that
  is `None`). Re-promoting the current `active` (from == target) stays
  deliverable.
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
  `activation.claim_manifest_skill(skill_id=…)`. `_NotDelivered` and the
  #3103 docstring paragraph go.
- Skill Center claim: `manifest_direct_claim_commands.claim_manifest_skill`
  accepts a target whose locator is `center://…` or `git://…` (shared, no
  bot ownership check — access was established by `_resolve_skill`) besides
  the bot's own `local://…`. Same same-name Set/Default detach logic. The
  `skill_center/AGENTS.md` paragraph on Manifest claims and its contract test
  are updated.
- `MaterialiserPorts` / both delivery bundles carry the `SharedSkillPort`;
  the platform (teclaw-platform) road gets the same port.

## 7. HTTP (`adapters/http/openapi_v1/bots/`)

- `schemas_genome.py`: `PromotionPayload.status` literal gains `pending`,
  `applied_partial`; `apply_kind` literal `direct` only; new `apply_id`,
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
| completion seam | `apply_engine/test_apply_service_lifecycle.py`: handler called once on SUCCEEDED/PARTIAL/FAILED/CompileRefused/engine exception/unstartable, before lock release; raising handler swallowed; Manifest kind calls nothing |
| repository | `test_genome_repository.py`: insert pending, mark_applying conditional, settle moves pair + labels + promoted, CAS-lost → apply_failed, double settle no-op, DDL/ORM pin |
| promotion | `bot_genome/test_promotion_service.py` (rewritten order): lock busy → 409 + row apply_failed + refs untouched; start error; principal for automated actors; read-time settle of applying/abandoned/stale pending |
| settler | new `bot_genome/test_promotion_settle.py` |
| applier | new `bot_genome/test_direct_applier.py` over a fake run service |
| compiler | `bot_genome/test_compile.py`: deliverable rule rewritten |
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
- **`claim_manifest_skill` widening** touches the Skill Center control plane;
  guarded by its own tests and `_resolve_skill`'s access check.
- **Owner as principal** lets an automated actor do what the owner may; the
  gate and drift checks before it are the control.
- **A worker dying between `applies.finish` and the handler** leaves a row
  `applying` until read; the read-time settle closes it.
</content>
</invoke>
