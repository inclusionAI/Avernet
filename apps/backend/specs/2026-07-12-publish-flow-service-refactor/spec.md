# Publish Flow Service Refactor

## Summary
The service-bot publish flow is orchestrated by a single 3185-line module that
has become unmaintainable: it drives every stage of the publish state machine
through one long branch-per-stage method, duplicates near-identical logic
between stages, hard-codes provider-specific special cases, and carries a set of
unclear, non-idiomatic identifier names. It also loses work on restart: the
backend-driven stage advances (build, release) run in fire-and-forget in-process
background tasks, so a pod reboot mid-build strands the record forever. This
refactor restructures the module into cohesive, well-named pieces and makes the
backend-driven pipeline **durable** — every backend stage advance becomes a
persisted, crash-safe task that resumes after restart. Aside from two
**intentional** behavior changes (durable/autonomous advancement, and `/process`
becoming a uniform async-submit), externally observable behavior — endpoints,
state-machine semantics, and stored record shape — is preserved.

## Motivation
`publish_flow_service.py` is the largest single unit of publish orchestration
and the hardest to change safely:

- It is **3185 lines** — over 3× the repository's 1000-line single-responsibility
  cap (enforced by `test_no_oversized_modules.py`, where it sits on the shrinking
  allowlist). New publish work keeps landing here because there is nowhere else
  for it to go, so the debt compounds.
- The central `process` method **mixes two different jobs**: issuing a command
  that advances the flow to the next stage, and answering a read-only query about
  the current stage. Several stages only move forward as an incidental side
  effect of a caller happening to poll, which makes the flow's progress logic
  hard to reason about and hard to trust.
- The stage logic is **not provider-agnostic**: provider-specific behavior
  (currently "teclaw" vs everything else) is expressed as scattered inline
  conditionals rather than routed through the provider abstraction the codebase
  already has. Adding or changing a provider means hunting down every branch.
- **Redundant code**: the verify-environment and online-environment release paths
  (both first-release and upgrade variants) are near-duplicates that differ only
  by a few stage-keyed values, yet each is written out and maintained separately.
- Many **field and method names are unclear or non-idiomatic English**, which
  raises the cost of reading and safely modifying the code.

The goal is to pay down this debt now, while the behavior is well-understood and
can be pinned by tests, rather than after more logic accretes.

## User Stories
- As a backend engineer, I want the publish flow split into small,
  single-purpose modules with clear names, so that I can find and change one
  concern without reading 3000 lines.
- As a backend engineer, I want advancing the flow and querying its status to be
  clearly separated, so that reading status never has surprising side effects and
  advancement is explicit and predictable.
- As a backend engineer, I want provider-specific behavior expressed through the
  existing provider abstraction rather than inline `if provider == X` checks, so
  that supporting a new provider does not mean editing stage logic in many places.
- As a backend engineer, I want the verify and online stages to share one
  parameterized implementation, so that a fix or change applies to both instead
  of being copied by hand.
- As an operator relying on the publish APIs, I want the endpoints, state
  transitions, messages, and stored records to behave exactly as before, so that
  this refactor is invisible to everything downstream.

## Acceptance Criteria
- [ ] Every existing publish HTTP endpoint (`process`, `sync`, `retry`,
      `restart`, `scale`, `sync scale`, `sync restart`, rollback,
      general publish/teardown, status query) returns the same results, status
      codes, and messages as before the refactor, for both provider families
      (teclaw and non-teclaw) — **except** `/process`, whose contract change is
      specified below.
- [ ] **(Intended change) `/process` is uniform async-submit.** The two
      user-driven advance points — `DRAFT` (starts the build→verify chain) and
      `VALIDATING` (the go-live gate) — enqueue the work and return an
      "in progress" result immediately, rather than blocking and returning the
      completed next state with freshly-created ids. DRAFT already behaves this
      way today; VALIDATING now matches it. `/process` on every other state
      (incl. BUILT) is describe-only. The resulting transitions and persisted
      fields are unchanged — only the moment/shape of the `/process` response
      differs (ids arrive via a subsequent status poll).
- [ ] **(Intended change) Backend-driven advances are durable.** Build, release,
      restart, and BaaS-wait progress are persisted tasks (via `TaskQueueService`)
      that survive a process/pod restart and resume, instead of running in
      fire-and-forget in-process background tasks that are lost on reboot. The
      BaaS-wait transitions (VALIDATE_PUB → VALIDATING, ONLINE_PUB → SUCCESS, and
      their restart variants) advance autonomously via a self-rescheduling poll
      rather than depending on a caller incidentally polling `/sync`; `/sync`
      stays available and idempotent as a manual/redundant driver. The
      manual go-live gate (VALIDATING → ONLINE) is unchanged — it fires only when
      a user calls `/process`; no task ever crosses it autonomously.
- [ ] **Stage-advance tasks are idempotent.** Re-running a build/release/restart
      task after a crash or lease-expiry re-claim does not double-create a BaaS
      bot, corrupt the build artifact, or duplicate bindings — it detects
      already-recorded progress and resumes.
- [ ] **Long tasks keep their claim.** A stage-advance task that runs longer than
      the worker lease (e.g. a slow build) renews its lease while running, so it
      is not concurrently re-claimed and double-executed on a multi-pod
      deployment. The task-queue worker is enabled in the base config so
      durable/autonomous advancement is on in every profile (worker kept off under
      the test profile).
- [ ] Every publish state transition (DRAFT → BUILDING → BUILT → VALIDATE_PUB →
      VALIDATING → ONLINE_PUB → SUCCESS, plus FAILED/RETRY/ROLLBACK/UPGRADED
      paths) fires under the same conditions and writes the same persisted
      fields as before.
- [ ] Persisted record shape is unchanged: the `ext` JSON keys and device-binding
      fields written and read are byte-for-byte compatible with existing records
      (no data migration). The `ac_task_queue` table gains only new `task_type`
      string values (no schema change).
- [ ] Advancing the flow and reading its status are separated so that a status
      read performs no state mutation.
- [ ] Provider-specific behavior is routed through the existing provider
      abstraction; there are no remaining ad-hoc `active_engine == "teclaw"` /
      `provider == TECLAW` conditionals embedded in stage logic.
- [ ] The verify and online release paths (first-release and upgrade) are served
      by a single shared, stage-parameterized implementation rather than
      duplicated per stage.
- [ ] No production module in the refactored publish flow exceeds the repository's
      1000-line cap, and the `publish_flow_service.py` allowlist entry in
      `test_no_oversized_modules.py` is removed (or updated to reflect reality).
- [ ] Public and internal method/field names that were unclear or non-idiomatic
      are renamed to clear English, with all in-repo callers updated in the same
      change. Persisted JSON/DB keys are left unchanged.
- [ ] Current behavior is pinned by tests (added/strengthened before the
      restructure) covering the teclaw and non-teclaw paths through build,
      verify, online, retry, restart, rollback, scale, and progress sync; the
      full suite passes after the refactor.

## In Scope
- Decomposing `publish_flow_service.py` into cohesive modules (e.g. stage
  drivers, status/progress sync, restart/scale/rollback operations, ext/state
  helpers) under the size cap.
- Separating flow advancement (command) from status lookup (query).
- Routing provider-specific behavior through the existing provider abstraction.
- Unifying the duplicated verify/online release logic behind one parameterized
  path.
- Making the backend-driven advances durable: modeling build, release, restart,
  and BaaS-wait progress as persisted `TaskQueueService` tasks (per-stage,
  chained), with idempotency guards so re-runs are safe, and making `/process` a
  uniform async-submit.
- A **lease-renewal** primitive added to the shared task-queue infra
  (`TaskQueueRepositoryProtocol` + both repo impls + the worker heartbeat) so a
  long-running stage task holds its claim; enabling the task-queue worker in the
  base config.
- Renaming unclear methods and fields (internal + public methods) to idiomatic
  English and updating all callers.
- Adding/strengthening characterization tests that pin current behavior before
  and validate it after the refactor, plus tests for durability/idempotency,
  lease renewal, and the new `/process` contract.

## Out of Scope
- Any change to externally observable behavior beyond the two intentional
  changes named in Acceptance Criteria (uniform async `/process`; durable/
  autonomous backend advancement). No change to state-machine semantics.
- Renaming persisted `ext` JSON keys or device-binding/DB column names (would
  require data migration).
- New features, new providers, new stages, or performance optimization beyond
  what naturally falls out of de-duplication.
- Refactoring the collaborating services (`baas_service`, `bot_build_service`,
  `bot_publish_service`, the `deploy/` producers) beyond the minimal surface
  needed to consume their existing abstractions.

## Open Questions
- **Command/query split & durability shape.** *(Resolved.)* All backend-driven
  advances (build, release, restart) and the BaaS-wait progress become durable,
  per-stage `TaskQueueService` tasks, chained, idempotent, and lease-renewed;
  `/process` becomes uniform async-submit; `/sync` stays as an idempotent manual
  driver; the manual VALIDATING → ONLINE gate stays user-only. Worker enabled in
  base `application.yaml`, community override dropped, kept off under the test
  profile. Enabling the worker also activates the already-registered devices BaaS
  poll handlers — confirmed acceptable (they are idempotent and built to run).
- **Provider abstraction surface.** *(Resolved.)* A small `ProviderBehavior`
  interface + router (keyed by `device_provider`, mirroring
  `DeployArtifactProducerRouter`) owned by the publish flow — distinct from the
  artifact-producer seam, since the varying behaviors are deploy-time.
- **`/process` async contract acceptance.** *(Resolved — accepted.)* Callers do
  not depend on the synchronous ids / already-advanced state in the `/process`
  response; uniform async-submit is acceptable.
- **Naming targets.** *(Resolved — see plan "Renames".)* Confirm the specific
  rename list (e.g. `restamp`,
  `_upgrade_last_publish`, `general_publish`/`general_teardown`, `scale`,
  `source_status`, stage-vs-status vocabulary) during `plan`, since some names
  touch public methods and their callers.
